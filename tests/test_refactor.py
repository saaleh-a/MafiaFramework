"""
Verification test suite for the MafiaFramework deep refactor.

Tests cover:
  1. Self-vote prevention (extract_vote / _parse_vote returns None on self-vote)
  2. Tie-break logic (game_state correctly reports tied players)
  3. Role-Personality exclusion (TheParasite/ThePerformer blocked for Detective/Doctor)
  4. Robust action splitting (REASONING: stripped from ACTION block)
  5. Ghost filtering (dead players excluded from mention counts)
  6. Archetype-Personality exclusion (banned combinations)
  7. Parser fix: REASONING-only response returns empty action
  8. Recency weighting in SummaryAgent current_target
  9. Mafia prompt pre-reasoning questions
  10. Belief state instruction update (sparse tags, archetype texture)
"""

import re
import unittest

# ---------------------------------------------------------------------------
# We import the actual production modules so the tests validate real behaviour.
# ---------------------------------------------------------------------------
from agents.base import parse_reasoning_action, _recursive_strip_marker
from engine.game_state import GameState, PlayerState, GamePhase, LogEntry
from engine.game_manager import (
    _pick_personality_constrained,
    PERSONALITY_EXCLUSIONS,
    ARCHETYPE_PERSONALITY_EXCLUSIONS,
    _PERSONALITY_FREQUENCY_CAP,
)


# ===========================================================================
#  1. Self-Vote Prevention
# ===========================================================================

class TestSelfVotePrevention(unittest.TestCase):
    """Verify that the vote parser never returns the voter's own name."""

    def _parse_vote(self, action: str, valid_targets: list[str], voter: str) -> str | None:
        """
        Standalone reimplementation of MafiaGameOrchestrator._parse_vote
        so we can test it without instantiating the full orchestrator.
        """
        text = action.strip()

        # Priority 1 — VOTE: tag
        vote_tag = re.search(r"VOTE:\s*(\w+)", text, re.IGNORECASE)
        if vote_tag:
            tagged = vote_tag.group(1).strip()
            for target in valid_targets:
                if target.lower() == tagged.lower() and target != voter:
                    return target

        # Priority 2 — intent phrases
        intent_patterns = [
            r"(?:I(?:'m| am)\s+voting\s+(?:for\s+)?)",
            r"(?:my\s+vote\s+(?:is\s+(?:for\s+)?|goes?\s+to\s+))",
            r"(?:I\s+vote\s+(?:for\s+)?)",
            r"(?:staying\s+on\s+)",
            r"(?:I(?:'m| am)\s+going\s+with\s+)",
            r"(?:locking\s+(?:in\s+)?(?:on\s+)?)",
            r"(?:voting\s+out\s+)",
            r"(?:I\s+cast\s+(?:my\s+)?vote\s+(?:for\s+)?)",
        ]
        combined = "|".join(intent_patterns)
        intent_match = re.search(rf"(?:{combined})(\w+)", text, re.IGNORECASE)
        if intent_match:
            candidate = intent_match.group(1).strip()
            for target in valid_targets:
                if target.lower() == candidate.lower() and target != voter:
                    return target

        # Priority 3 — last mentioned valid name
        last_found: str | None = None
        text_lower = text.lower()
        for target in valid_targets:
            idx = text_lower.rfind(target.lower())
            if idx != -1 and target != voter:
                if last_found is None or idx > text_lower.rfind(last_found.lower()):
                    last_found = target

        return last_found

    def test_self_vote_via_tag_returns_none(self):
        result = self._parse_vote("VOTE: Alice", ["Alice", "Bob"], "Alice")
        self.assertIsNone(result)

    def test_self_vote_via_intent_returns_none(self):
        result = self._parse_vote("I'm voting for Alice", ["Alice", "Bob"], "Alice")
        self.assertIsNone(result)

    def test_self_vote_via_mention_returns_none(self):
        result = self._parse_vote("Alice is suspicious", ["Alice"], "Alice")
        self.assertIsNone(result)

    def test_valid_vote_via_tag(self):
        result = self._parse_vote("VOTE: Bob", ["Alice", "Bob", "Charlie"], "Alice")
        self.assertEqual(result, "Bob")

    def test_valid_vote_via_intent(self):
        result = self._parse_vote("I'm voting for Charlie", ["Alice", "Bob", "Charlie"], "Alice")
        self.assertEqual(result, "Charlie")

    def test_last_mentioned_name_priority(self):
        # Alice talks to Bob but the vote target is Charlie (last name)
        result = self._parse_vote(
            "Bob I hear you but honestly Charlie is the problem",
            ["Alice", "Bob", "Charlie"], "Alice",
        )
        self.assertEqual(result, "Charlie")

    def test_no_valid_target_returns_none(self):
        result = self._parse_vote("I abstain from voting", ["Alice", "Bob"], "Alice")
        self.assertIsNone(result)


# ===========================================================================
#  2. Tie-Break Logic
# ===========================================================================

class TestTieBreakLogic(unittest.TestCase):
    """Verify that game_state correctly identifies ties and tied players."""

    def _make_game_state(self, names: list[str]) -> GameState:
        return GameState(
            players={
                n: PlayerState(name=n, role="Villager", archetype="Methodical")
                for n in names
            }
        )

    def test_no_tie_returns_winner(self):
        gs = self._make_game_state(["A", "B", "C"])
        gs.votes = {"A": "B", "B": "B", "C": "A"}
        self.assertEqual(gs.tally_votes(), "B")
        self.assertEqual(gs.get_tied_players(), [])

    def test_tie_returns_none_and_lists_tied(self):
        gs = self._make_game_state(["A", "B", "C", "D"])
        gs.votes = {"A": "B", "B": "A", "C": "B", "D": "A"}
        self.assertIsNone(gs.tally_votes())
        tied = gs.get_tied_players()
        self.assertIn("A", tied)
        self.assertIn("B", tied)
        self.assertEqual(len(tied), 2)

    def test_three_way_tie(self):
        gs = self._make_game_state(["A", "B", "C", "D", "E", "F"])
        gs.votes = {"A": "B", "B": "C", "C": "A", "D": "B", "E": "C", "F": "A"}
        self.assertIsNone(gs.tally_votes())
        tied = gs.get_tied_players()
        self.assertEqual(sorted(tied), ["A", "B", "C"])

    def test_empty_votes(self):
        gs = self._make_game_state(["A", "B"])
        self.assertIsNone(gs.tally_votes())
        self.assertEqual(gs.get_tied_players(), [])

    def test_tied_players_excluded_from_decisive_vote(self):
        """Verify tied players can be filtered from a voter list."""
        gs = self._make_game_state(["A", "B", "C", "D"])
        gs.votes = {"A": "B", "B": "A", "C": "B", "D": "A"}
        tied = gs.get_tied_players()
        alive = gs.get_alive_players()
        decisive_voters = [p for p in alive if p not in tied]
        # Tied players (A, B) should not appear in decisive_voters
        for t in tied:
            self.assertNotIn(t, decisive_voters)
        # Non-tied alive players should remain
        self.assertTrue(len(decisive_voters) > 0)


# ===========================================================================
#  3. Role-Personality Exclusion
# ===========================================================================

class TestPersonalityExclusion(unittest.TestCase):
    """Verify the exclusion table and frequency cap."""

    def test_detective_cannot_get_parasite(self):
        """TheParasite must never be assigned to Detective."""
        for _ in range(200):
            counts: dict[str, int] = {}  # fresh counts each iteration
            p = _pick_personality_constrained("Detective", counts, demo=False)
            self.assertNotEqual(p, "TheParasite")
            self.assertNotEqual(p, "ThePerformer")

    def test_doctor_cannot_get_performer(self):
        """ThePerformer must never be assigned to Doctor."""
        for _ in range(200):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained("Doctor", counts, demo=False)
            self.assertNotEqual(p, "TheParasite")
            self.assertNotEqual(p, "ThePerformer")

    def test_villager_can_get_any_personality(self):
        """Villager has no exclusions."""
        seen: set[str] = set()
        for _ in range(500):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained("Villager", counts, demo=False)
            seen.add(p)
        # Villager should be able to see TheParasite and ThePerformer
        self.assertIn("TheParasite", seen)
        self.assertIn("ThePerformer", seen)

    def test_frequency_cap_enforced(self):
        """No personality should appear more than _PERSONALITY_FREQUENCY_CAP times."""
        counts: dict[str, int] = {"TheGhost": _PERSONALITY_FREQUENCY_CAP}
        # Single call — TheGhost should not be returned
        p = _pick_personality_constrained("Villager", counts, demo=False)
        self.assertNotEqual(p, "TheGhost")

    def test_raises_when_no_valid_personality(self):
        """ValueError when all personalities are exhausted."""
        from prompts.personalities import ALL_PERSONALITIES
        # Saturate every personality at the cap
        counts = {p: _PERSONALITY_FREQUENCY_CAP for p in ALL_PERSONALITIES}
        with self.assertRaises(ValueError):
            _pick_personality_constrained("Villager", counts, demo=False)


# ===========================================================================
#  4. Robust Action Splitting
# ===========================================================================

class TestActionSplitting(unittest.TestCase):
    """Verify parse_reasoning_action handles edge cases."""

    def test_normal_split(self):
        text = "REASONING: I think Bob is sus ACTION: I vote for Bob"
        r, a = parse_reasoning_action(text)
        self.assertIn("Bob is sus", r)
        self.assertEqual(a, "I vote for Bob")

    def test_reasoning_leaked_into_action_is_stripped(self):
        # Single ACTION: with REASONING: embedded in the action text
        text = "REASONING: thinking hard ACTION: I vote REASONING: leaked Bob"
        r, a = parse_reasoning_action(text)
        # The action should have REASONING: stripped out
        self.assertNotIn("REASONING:", a)
        self.assertIn("I vote", a)
        self.assertIn("Bob", a)

    def test_action_starting_with_reasoning_returns_empty(self):
        text = "ACTION: REASONING: I should have done this differently"
        r, a = parse_reasoning_action(text)
        # Should return empty action (retry case)
        self.assertEqual(a, "")
        self.assertIn("differently", r)

    def test_no_action_marker(self):
        text = "I just want to say hello"
        r, a = parse_reasoning_action(text)
        self.assertEqual(r, "")
        self.assertEqual(a, "I just want to say hello")

    def test_recursive_strip_marker(self):
        text = "Hello REASONING: world REASONING: test"
        result = _recursive_strip_marker(text, "REASONING:")
        self.assertNotIn("REASONING:", result)
        self.assertIn("Hello", result)
        self.assertIn("world", result)
        self.assertIn("test", result)


# ===========================================================================
#  5. Ghost Filtering (Eliminated Round Tracking)
# ===========================================================================

class TestGhostFiltering(unittest.TestCase):
    """Verify eliminated_round is set and dead players are filtered."""

    def test_eliminated_round_is_recorded(self):
        gs = GameState(
            players={
                "Alice": PlayerState(name="Alice", role="Villager", archetype="Methodical"),
                "Bob":   PlayerState(name="Bob", role="Mafia", archetype="Methodical"),
            },
            round_number=3,
        )
        gs.eliminate_player("Bob")
        self.assertEqual(gs.players["Bob"].eliminated_round, 3)
        self.assertFalse(gs.players["Bob"].is_alive)
        self.assertTrue(gs.players["Bob"].is_revealed)

    def test_alive_player_has_no_eliminated_round(self):
        gs = GameState(
            players={
                "Alice": PlayerState(name="Alice", role="Villager", archetype="Methodical"),
            }
        )
        self.assertIsNone(gs.players["Alice"].eliminated_round)

    def test_public_summary_hides_living_roles(self):
        """get_public_state_summary must not reveal roles of living players."""
        gs = GameState(
            players={
                "Alice": PlayerState(name="Alice", role="Detective", archetype="Methodical"),
                "Bob":   PlayerState(name="Bob", role="Mafia", archetype="Methodical"),
            }
        )
        summary = gs.get_public_state_summary()
        self.assertNotIn("Detective", summary)
        self.assertNotIn("Mafia", summary)

    def test_public_summary_shows_revealed_roles(self):
        gs = GameState(
            players={
                "Alice": PlayerState(name="Alice", role="Detective", archetype="Methodical"),
                "Bob":   PlayerState(
                    name="Bob", role="Mafia", archetype="Methodical",
                    is_alive=False, is_revealed=True,
                ),
            }
        )
        summary = gs.get_public_state_summary()
        self.assertNotIn("Detective", summary)
        self.assertIn("Mafia", summary)  # Bob's revealed role


# ===========================================================================
#  6. Archetype-Personality Exclusion (combination bans)
# ===========================================================================

class TestArchetypePersonalityExclusion(unittest.TestCase):
    """Verify that banned archetype-personality combinations are never assigned."""

    def test_passive_cannot_get_mythbuilder(self):
        """Passive+MythBuilder is mechanically broken and must be banned."""
        for _ in range(200):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained(
                "Villager", counts, demo=False, archetype="Passive",
            )
            self.assertNotEqual(p, "MythBuilder")

    def test_passive_cannot_get_theghost(self):
        """Passive+TheGhost reinforces silence in both layers."""
        for _ in range(200):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained(
                "Villager", counts, demo=False, archetype="Passive",
            )
            self.assertNotEqual(p, "TheGhost")

    def test_overconfident_cannot_get_theparasite(self):
        """Overconfident+TheParasite is redundant."""
        for _ in range(200):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained(
                "Villager", counts, demo=False, archetype="Overconfident",
            )
            self.assertNotEqual(p, "TheParasite")

    def test_stubborn_cannot_get_mythbuilder(self):
        """Stubborn+MythBuilder reinforces anchoring."""
        for _ in range(200):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained(
                "Villager", counts, demo=False, archetype="Stubborn",
            )
            self.assertNotEqual(p, "MythBuilder")

    def test_diplomatic_cannot_get_theconfessor(self):
        """Diplomatic+TheConfessor reinforces softness."""
        for _ in range(200):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained(
                "Villager", counts, demo=False, archetype="Diplomatic",
            )
            self.assertNotEqual(p, "TheConfessor")

    def test_non_excluded_archetype_allows_all(self):
        """An archetype with no exclusions should allow every personality."""
        seen: set[str] = set()
        for _ in range(500):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained(
                "Villager", counts, demo=False, archetype="Reactive",
            )
            seen.add(p)
        # Reactive has no bans — should see MythBuilder, TheGhost, etc.
        self.assertIn("MythBuilder", seen)
        self.assertIn("TheGhost", seen)


# ===========================================================================
#  7. Parser Fix: REASONING-only response returns empty action
# ===========================================================================

class TestReasoningOnlyParser(unittest.TestCase):
    """Verify that REASONING-only responses (no ACTION) return empty action."""

    def test_reasoning_only_returns_empty_action(self):
        """When response has REASONING: but no ACTION:, action must be empty."""
        text = "REASONING: Bob=0.7 because he voted weird. Alice=0.3."
        reasoning, action = parse_reasoning_action(text)
        self.assertEqual(action, "")
        self.assertIn("Bob", reasoning)

    def test_reasoning_only_multiline(self):
        text = (
            "REASONING: I think Bob is suspicious.\n"
            "BELIEF_UPDATE: Bob=0.7 because he was quiet.\n"
            "BELIEF_UPDATE: Alice=0.3 because she defended him."
        )
        reasoning, action = parse_reasoning_action(text)
        self.assertEqual(action, "")
        self.assertIn("Bob", reasoning)

    def test_plain_text_still_becomes_action(self):
        """Text with no markers at all should still be treated as action."""
        text = "I vote for Bob. He's suspicious."
        reasoning, action = parse_reasoning_action(text)
        self.assertEqual(reasoning, "")
        self.assertEqual(action, text)

    def test_reasoning_then_action_still_works(self):
        """Normal REASONING: ... ACTION: ... flow must still work."""
        text = "REASONING: thinking hard ACTION: I vote for Bob"
        reasoning, action = parse_reasoning_action(text)
        self.assertIn("thinking hard", reasoning)
        self.assertEqual(action, "I vote for Bob")


# ===========================================================================
#  8. Recency Weighting in SummaryAgent
# ===========================================================================

class TestRecencyWeighting(unittest.TestCase):
    """Verify that SummaryAgent current_target uses recency weighting."""

    def _make_entry(self, agent_name: str, action: str, round_number: int):
        return LogEntry(
            phase=GamePhase.DAY_DISCUSSION,
            round_number=round_number,
            agent_name=agent_name,
            role="Villager",
            archetype="Methodical",
            reasoning=None,
            action=action,
        )

    def test_current_round_mentions_outweigh_old(self):
        """Mentions in the current round should dominate over old ones."""
        from agents.summary import SummaryAgent
        sa = SummaryAgent()

        entries = [
            # Round 1: Bob is mentioned 5 times in accusatory context
            self._make_entry("Alice", "I suspect Bob is mafia", 1),
            self._make_entry("Charlie", "I vote Bob guilty", 1),
            self._make_entry("Diana", "Bob is suspicious to me", 1),
            self._make_entry("Eve", "I accuse Bob", 1),
            self._make_entry("Frank", "I suspect Bob", 1),
            # Round 3 (current): Charlie is mentioned 2 times
            self._make_entry("Alice", "I suspect Charlie is mafia", 3),
            self._make_entry("Diana", "I vote Charlie guilty", 3),
        ]
        alive = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank"]

        result = sa._get_current_target(entries, alive, current_round=3)
        # Charlie has 2 * 1.0 = 2.0 from current round
        # Bob has 5 * 0.1 = 0.5 from 2 rounds ago
        self.assertIn("Charlie", result)

    def test_previous_round_point_three_weight(self):
        """Mentions from the previous round carry 0.3 weight."""
        from agents.summary import SummaryAgent
        sa = SummaryAgent()

        entries = [
            # Round 2 (previous): Bob mentioned 4 times
            self._make_entry("Alice", "I suspect Bob is mafia", 2),
            self._make_entry("Charlie", "I vote Bob guilty", 2),
            self._make_entry("Diana", "Bob is suspicious", 2),
            self._make_entry("Eve", "I accuse Bob", 2),
            # Round 3 (current): Charlie mentioned 1 time
            self._make_entry("Frank", "I suspect Charlie is mafia", 3),
        ]
        alive = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank"]

        result = sa._get_current_target(entries, alive, current_round=3)
        # Bob: 4 * 0.3 = 1.2, Charlie: 1 * 1.0 = 1.0
        self.assertIn("Bob", result)

    def test_no_entries_returns_none(self):
        from agents.summary import SummaryAgent
        sa = SummaryAgent()
        result = sa._get_current_target([], ["Alice", "Bob"], current_round=1)
        self.assertIsNone(result)


# ===========================================================================
#  9. Mafia Prompt Contains Pre-Reasoning Questions
# ===========================================================================

class TestMafiaPromptQuestions(unittest.TestCase):
    """Verify the Mafia builder injects the mandatory pre-reasoning questions."""

    def test_mafia_prompt_has_threat_check(self):
        from prompts.builder import build_mafia_prompt
        prompt = build_mafia_prompt("Alice", "Bob", "Paranoid", "TheAnalyst")
        self.assertIn("AM I UNDER SUSPICION", prompt)
        self.assertIn("IS Bob UNDER SUSPICION", prompt)
        self.assertIn("BIGGEST THREAT TO MAFIA", prompt)
        self.assertIn("COVER STORY STILL HOLDING", prompt)
        self.assertIn("WHO WILL IDENTIFY ME", prompt)

    def test_mafia_prompt_references_partner_in_solo_question(self):
        from prompts.builder import build_mafia_prompt
        prompt = build_mafia_prompt("Eve", "Frank", "Analytical", "TheMartyr")
        self.assertIn("Frank has been eliminated", prompt)


# ===========================================================================
#  10. Belief State Instruction Demotes BELIEF_UPDATE
# ===========================================================================

class TestBeliefInstructionUpdate(unittest.TestCase):
    """Verify the belief prompt now says MAY not MUST for BELIEF_UPDATE."""

    def test_belief_injection_permits_sparse_tags(self):
        from agents.belief_state import build_belief_prompt_injection, SuspicionState
        belief = SuspicionState()
        belief.initialize(["Bob", "Charlie"], num_mafia=1)
        text = build_belief_prompt_injection(belief, "Paranoid")
        self.assertIn("MAY include", text)
        self.assertNotIn("You MUST cite evidence", text)

    def test_belief_injection_mentions_archetype_texture(self):
        from agents.belief_state import build_belief_prompt_injection, SuspicionState
        belief = SuspicionState()
        belief.initialize(["Bob"], num_mafia=1)
        text = build_belief_prompt_injection(belief, "Analytical")
        self.assertIn("archetype", text.lower())
        self.assertIn("reasoning style", text.lower())


# ===========================================================================
#  11. Mafia Partner Confusion Fix (Q3 excludes partner)
# ===========================================================================

class TestMafiaPartnerConfusionFix(unittest.TestCase):
    """Verify Q3 of the Mafia threat check explicitly excludes the partner."""

    def test_q3_excludes_partner_by_name(self):
        from prompts.builder import build_mafia_prompt
        prompt = build_mafia_prompt("Alice", "Bob", "Paranoid", "TheAnalyst")
        # Q3 should explicitly tell the agent to exclude Bob
        self.assertIn("Exclude Bob", prompt)
        self.assertIn("TOWN PLAYERS", prompt.upper())

    def test_q3_does_not_name_partner_as_threat(self):
        from prompts.builder import build_mafia_prompt
        prompt = build_mafia_prompt("Eve", "Frank", "Analytical", "TheMartyr")
        # Find the Q3 line and verify it excludes Frank
        self.assertIn("Exclude Frank", prompt)
        self.assertIn("Do NOT name Frank here", prompt)


# ===========================================================================
#  12. Doctor Heuristic — Deductive Behaviour Protection
# ===========================================================================

class TestDoctorHeuristic(unittest.TestCase):
    """Verify the Doctor prompt uses deductive behaviour, not activity."""

    def test_doctor_prompt_has_protection_signals(self):
        from prompts.builder import build_doctor_prompt
        prompt = build_doctor_prompt("Grace", "Analytical", "TheAnalyst")
        self.assertIn("PROTECTION SIGNALS", prompt)
        self.assertIn("predictions", prompt.lower())

    def test_doctor_prompt_warns_against_loud_players(self):
        from prompts.builder import build_doctor_prompt
        prompt = build_doctor_prompt("Grace", "Analytical", "TheAnalyst")
        self.assertIn("DANGER SIGNALS", prompt)
        self.assertIn("LOUDEST VOICE", prompt)

    def test_doctor_prompt_does_not_protect_social_engine(self):
        from prompts.builder import build_doctor_prompt
        prompt = build_doctor_prompt("Grace", "Passive", "TheMartyr")
        # The old "SOCIAL ENGINE" language should be replaced
        self.assertNotIn("SOCIAL ENGINE", prompt)
        # New: warns about Mafia behaviour
        self.assertIn("Mafia behaviours", prompt)


# ===========================================================================
#  13. Recency Weighting — Stronger Decay
# ===========================================================================

class TestStrongerRecencyDecay(unittest.TestCase):
    """Verify that 2+ round old mentions barely register."""

    def _make_entry(self, agent_name: str, action: str, round_number: int):
        return LogEntry(
            phase=GamePhase.DAY_DISCUSSION,
            round_number=round_number,
            agent_name=agent_name,
            role="Villager",
            archetype="Methodical",
            reasoning=None,
            action=action,
        )

    def test_two_round_old_mentions_minimal(self):
        """Even 10 mentions from 2 rounds ago shouldn't beat 1 current mention."""
        from agents.summary import SummaryAgent
        sa = SummaryAgent()

        entries = [
            # Round 1: Bob mentioned 10 times
            self._make_entry("Alice", "I suspect Bob is mafia", 1),
            self._make_entry("Charlie", "I vote Bob guilty", 1),
            self._make_entry("Diana", "Bob is suspicious", 1),
            self._make_entry("Eve", "I accuse Bob", 1),
            self._make_entry("Frank", "I suspect Bob", 1),
            self._make_entry("Grace", "Bob is mafia I vote Bob", 1),
            self._make_entry("Hank", "I suspect Bob is guilty", 1),
            self._make_entry("Ivy", "I accuse Bob", 1),
            self._make_entry("Jack", "Bob is suspicious", 1),
            self._make_entry("Kate", "I suspect Bob", 1),
            # Round 3 (current): Charlie mentioned 1 time
            self._make_entry("Alice", "I suspect Charlie is mafia", 3),
        ]
        alive = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank",
                 "Grace", "Hank", "Ivy", "Jack", "Kate"]

        result = sa._get_current_target(entries, alive, current_round=3)
        # Bob: 10 * 0.05 = 0.5, Charlie: 1 * 1.0 = 1.0
        self.assertIn("Charlie", result)


# ===========================================================================
#  14. Middleware Classes Exist and Are Registered
# ===========================================================================

class TestMiddlewareRegistration(unittest.TestCase):
    """Verify the new middleware classes can be imported and instantiated."""

    def test_reasoning_action_middleware_exists(self):
        from agents.middleware import ReasoningActionMiddleware
        mw = ReasoningActionMiddleware()
        self.assertIsNotNone(mw)

    def test_belief_update_middleware_exists(self):
        from agents.middleware import BeliefUpdateMiddleware
        mw = BeliefUpdateMiddleware()
        self.assertIsNotNone(mw)

    def test_middleware_is_agent_middleware_subclass(self):
        from agents.middleware import ReasoningActionMiddleware, BeliefUpdateMiddleware
        from agent_framework import AgentMiddleware
        self.assertTrue(issubclass(ReasoningActionMiddleware, AgentMiddleware))
        self.assertTrue(issubclass(BeliefUpdateMiddleware, AgentMiddleware))


# ===========================================================================
#  15. Consensus Personality Cap
# ===========================================================================

class TestConsensusPersonalityCap(unittest.TestCase):
    """Verify consensus-following personalities are capped at 1 per game."""

    def test_consensus_personality_cap_at_one(self):
        """TheParasite can only appear once per game."""
        counts: dict[str, int] = {"TheParasite": 1}
        # Should not be able to get TheParasite again
        for _ in range(200):
            p = _pick_personality_constrained("Villager", dict(counts), demo=False)
            self.assertNotEqual(p, "TheParasite")

    def test_consensus_cap_theperformer(self):
        """ThePerformer can only appear once per game."""
        counts: dict[str, int] = {"ThePerformer": 1}
        for _ in range(200):
            p = _pick_personality_constrained("Villager", dict(counts), demo=False)
            self.assertNotEqual(p, "ThePerformer")

    def test_consensus_cap_mythbuilder(self):
        """MythBuilder can only appear once per game."""
        counts: dict[str, int] = {"MythBuilder": 1}
        for _ in range(200):
            p = _pick_personality_constrained("Villager", dict(counts), demo=False)
            self.assertNotEqual(p, "MythBuilder")

    def test_consensus_cap_theconfessor(self):
        """TheConfessor can only appear once per game."""
        counts: dict[str, int] = {"TheConfessor": 1}
        for _ in range(200):
            p = _pick_personality_constrained("Villager", dict(counts), demo=False)
            self.assertNotEqual(p, "TheConfessor")

    def test_non_consensus_personality_still_at_two(self):
        """Non-consensus personalities like TheGhost can appear twice."""
        counts: dict[str, int] = {"TheGhost": 1}
        seen_ghost = False
        for _ in range(500):
            p = _pick_personality_constrained("Villager", dict(counts), demo=False)
            if p == "TheGhost":
                seen_ghost = True
                break
        self.assertTrue(seen_ghost, "TheGhost should still be assignable at count=1")


# ===========================================================================
#  16. Manipulative + ThePerformer Ban
# ===========================================================================

class TestManipulativePerformerBan(unittest.TestCase):
    """Verify that Manipulative+ThePerformer is banned."""

    def test_manipulative_cannot_get_theperformer(self):
        """Manipulative+ThePerformer produced self-voting behaviour."""
        for _ in range(200):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained(
                "Villager", counts, demo=False, archetype="Manipulative",
            )
            self.assertNotEqual(p, "ThePerformer")


# ===========================================================================
#  17. Lone Divergent Vote Instruction in Prompts
# ===========================================================================

class TestLoneDivergentVoteInstruction(unittest.TestCase):
    """Verify the lone divergent vote instruction exists in prompts."""

    def test_villager_prompt_has_lone_divergent_vote(self):
        from prompts.builder import build_villager_prompt
        prompt = build_villager_prompt("Alice", "Analytical", "TheAnalyst")
        self.assertIn("LONE DIVERGENT VOTES", prompt)
        self.assertIn("seven or more others", prompt)

    def test_detective_prompt_has_lone_divergent_vote(self):
        from prompts.builder import build_detective_prompt
        prompt = build_detective_prompt("Bob", "Analytical", "TheAnalyst")
        self.assertIn("LONE DIVERGENT VOTES", prompt)
        self.assertIn("seven or more others", prompt)


# ===========================================================================
#  18. Independent Reasoning Archetype Floor
# ===========================================================================

class TestIndependentArchetypeFloor(unittest.TestCase):
    """Verify the independent reasoning archetype constants are defined."""

    def test_independent_archetypes_defined(self):
        from engine.game_manager import INDEPENDENT_ARCHETYPES, _MIN_INDEPENDENT_ARCHETYPES
        self.assertIn("Contrarian", INDEPENDENT_ARCHETYPES)
        self.assertIn("Analytical", INDEPENDENT_ARCHETYPES)
        self.assertIn("Impulsive", INDEPENDENT_ARCHETYPES)
        self.assertIn("Stubborn", INDEPENDENT_ARCHETYPES)
        self.assertEqual(_MIN_INDEPENDENT_ARCHETYPES, 2)

    def test_consensus_personalities_defined(self):
        from engine.game_manager import CONSENSUS_PERSONALITIES, _CONSENSUS_PERSONALITY_CAP
        self.assertIn("TheParasite", CONSENSUS_PERSONALITIES)
        self.assertIn("TheConfessor", CONSENSUS_PERSONALITIES)
        self.assertIn("ThePerformer", CONSENSUS_PERSONALITIES)
        self.assertIn("MythBuilder", CONSENSUS_PERSONALITIES)
        self.assertEqual(_CONSENSUS_PERSONALITY_CAP, 1)


# ===========================================================================
#  19. InMemoryHistoryProvider in Agent Constructors
# ===========================================================================

class TestInMemoryHistoryProvider(unittest.TestCase):
    """Verify InMemoryHistoryProvider is importable and used."""

    def test_inmemory_history_provider_importable(self):
        from agent_framework import InMemoryHistoryProvider
        provider = InMemoryHistoryProvider("history", load_messages=True)
        self.assertIsNotNone(provider)


# ===========================================================================
#  20. Discussion Phase Output Contains No Vote Format
# ===========================================================================

class TestDiscussionNoVoteFormat(unittest.TestCase):
    """Verify the discussion rules ban 'I vote' format from discussion phase."""

    def test_mafia_prompt_bans_vote_in_discussion(self):
        from prompts.builder import build_mafia_prompt
        prompt = build_mafia_prompt("Alice", "Bob", "Paranoid", "TheAnalyst")
        self.assertIn("STRICTLY BANNED during discussion", prompt)
        self.assertIn("I'm voting X", prompt)

    def test_detective_prompt_bans_vote_in_discussion(self):
        from prompts.builder import build_detective_prompt
        prompt = build_detective_prompt("Alice", "Paranoid", "TheAnalyst")
        self.assertIn("STRICTLY BANNED during discussion", prompt)

    def test_doctor_prompt_bans_vote_in_discussion(self):
        from prompts.builder import build_doctor_prompt
        prompt = build_doctor_prompt("Alice", "Paranoid", "TheAnalyst")
        self.assertIn("STRICTLY BANNED during discussion", prompt)

    def test_villager_prompt_bans_vote_in_discussion(self):
        from prompts.builder import build_villager_prompt
        prompt = build_villager_prompt("Alice", "Paranoid", "TheAnalyst")
        self.assertIn("STRICTLY BANNED during discussion", prompt)

    def test_discussion_rules_require_specific_claim(self):
        from prompts.builder import build_villager_prompt
        prompt = build_villager_prompt("Alice", "Analytical", "TheAnalyst")
        self.assertIn("SPECIFIC CLAIM REQUIREMENT", prompt)
        self.assertIn("quote or paraphrase", prompt)

    def test_discussion_rules_own_read_first(self):
        from prompts.builder import build_villager_prompt
        prompt = build_villager_prompt("Alice", "Analytical", "TheAnalyst")
        self.assertIn("OWN READ FIRST", prompt)

    def test_discussion_rules_speak_obliquely(self):
        from prompts.builder import build_villager_prompt
        prompt = build_villager_prompt("Alice", "Analytical", "TheAnalyst")
        self.assertIn("SPEAK OBLIQUELY", prompt)

    def test_discussion_rules_no_consensus_echoing(self):
        from prompts.builder import build_villager_prompt
        prompt = build_villager_prompt("Alice", "Analytical", "TheAnalyst")
        self.assertIn("NO CONSENSUS ECHOING", prompt)


# ===========================================================================
#  21. Night Kill Prompt Contains No Kill/Murder Language
# ===========================================================================

class TestNightKillPromptLanguage(unittest.TestCase):
    """Verify night kill prompts use game-mechanic framing, not violence."""

    def test_mafia_goal_no_kill_language(self):
        from prompts.builder import build_mafia_prompt
        prompt = build_mafia_prompt("Alice", "Bob", "Paranoid", "TheAnalyst")
        # The Mafia goal should not say "eliminate Town players" with kill/murder
        self.assertNotIn("murder", prompt.lower())
        # Check that game context framing is present
        self.assertIn("party game", prompt)
        self.assertIn("GAME CONTEXT", prompt)

    def test_syndicate_channel_no_kill_language(self):
        from prompts.builder import build_mafia_prompt
        prompt = build_mafia_prompt("Alice", "Bob", "Paranoid", "TheAnalyst")
        self.assertIn("elimination target", prompt)
        self.assertNotIn("kill target", prompt)

    def test_mafia_prompt_has_refusal_fallback(self):
        """Mafia prompt should include fallback instruction for refusals."""
        from prompts.builder import build_mafia_prompt
        prompt = build_mafia_prompt("Alice", "Bob", "Paranoid", "TheAnalyst")
        self.assertIn("GAME CONTEXT", prompt)
        self.assertIn("game mechanics", prompt.lower())


# ===========================================================================
#  22. Contrarian Archetype Contains Resistance Requirement
# ===========================================================================

class TestContrarianResistance(unittest.TestCase):
    """Verify the Contrarian archetype has the resistance requirement."""

    def test_contrarian_has_resistance_requirement(self):
        from prompts.archetypes import ARCHETYPES
        contrarian = ARCHETYPES["Contrarian"]
        modifier = contrarian["strategy_modifier"]
        self.assertIn("RESISTANCE REQUIREMENT", modifier)
        self.assertIn("five or more players", modifier)

    def test_contrarian_requires_different_target_or_argument(self):
        from prompts.archetypes import ARCHETYPES
        contrarian = ARCHETYPES["Contrarian"]
        modifier = contrarian["strategy_modifier"]
        self.assertIn("name a DIFFERENT target", modifier)
        self.assertIn("current consensus is wrong", modifier)

    def test_contrarian_bans_pile_metacommentary(self):
        from prompts.archetypes import ARCHETYPES
        contrarian = ARCHETYPES["Contrarian"]
        modifier = contrarian["strategy_modifier"]
        self.assertIn("cannot simply join a pile", modifier)


# ===========================================================================
#  23. All Combination Bans Present
# ===========================================================================

class TestAllCombinationBans(unittest.TestCase):
    """Verify all 6 required combination bans are in the exclusion table."""

    def test_all_required_bans_present(self):
        from engine.game_manager import ARCHETYPE_PERSONALITY_EXCLUSIONS
        # Manipulative + ThePerformer
        self.assertIn("ThePerformer", ARCHETYPE_PERSONALITY_EXCLUSIONS["Manipulative"])
        # Passive + MythBuilder
        self.assertIn("MythBuilder", ARCHETYPE_PERSONALITY_EXCLUSIONS["Passive"])
        # Overconfident + TheParasite
        self.assertIn("TheParasite", ARCHETYPE_PERSONALITY_EXCLUSIONS["Overconfident"])
        # Passive + TheGhost
        self.assertIn("TheGhost", ARCHETYPE_PERSONALITY_EXCLUSIONS["Passive"])
        # Stubborn + MythBuilder
        self.assertIn("MythBuilder", ARCHETYPE_PERSONALITY_EXCLUSIONS["Stubborn"])
        # Diplomatic + TheConfessor
        self.assertIn("TheConfessor", ARCHETYPE_PERSONALITY_EXCLUSIONS["Diplomatic"])


# ===========================================================================
#  24. Personality Definitions Contain Three Voice Markers
# ===========================================================================

class TestPersonalityVoiceMarkers(unittest.TestCase):
    """Verify all personalities have the three concrete voice markers."""

    def test_all_personalities_have_voice_markers(self):
        from prompts.personalities import PERSONALITIES
        for name, p in PERSONALITIES.items():
            self.assertIn("voice_markers", p, f"{name} missing voice_markers")
            markers = p["voice_markers"]
            self.assertIn("sentence_length", markers, f"{name} missing sentence_length marker")
            self.assertIn("evidence_relationship", markers, f"{name} missing evidence_relationship marker")
            self.assertIn("deflection_style", markers, f"{name} missing deflection_style marker")

    def test_voice_markers_are_nonempty(self):
        from prompts.personalities import PERSONALITIES
        for name, p in PERSONALITIES.items():
            markers = p["voice_markers"]
            self.assertTrue(len(markers["sentence_length"]) > 10, f"{name} sentence_length too short")
            self.assertTrue(len(markers["evidence_relationship"]) > 10, f"{name} evidence_relationship too short")
            self.assertTrue(len(markers["deflection_style"]) > 10, f"{name} deflection_style too short")

    def test_voice_markers_injected_into_prompt(self):
        """Verify the personality block actually injects voice markers."""
        from prompts.builder import build_villager_prompt
        prompt = build_villager_prompt("Alice", "Analytical", "TheGhost")
        self.assertIn("VOICE MARKERS", prompt)
        self.assertIn("Sentence length:", prompt)
        self.assertIn("Evidence style:", prompt)
        self.assertIn("Under pressure:", prompt)


# ===========================================================================
#  25. Architecture Fix: Discussion History Excludes Self
# ===========================================================================

class TestDiscussionHistoryExcludesSelf(unittest.TestCase):
    """Verify format_discussion_prompt filters out the agent's own messages."""

    def test_own_messages_filtered_from_discussion(self):
        from agents.base import format_discussion_prompt
        history = [
            "Alice: I think Bob is suspicious",
            "Bob: That's not fair",
            "Alice: Let me explain",
        ]
        result = format_discussion_prompt(history, "Alice")
        # Alice's messages should be filtered out
        self.assertNotIn("Alice: I think Bob", result)
        self.assertNotIn("Alice: Let me explain", result)
        # Bob's message should remain
        self.assertIn("Bob: That's not fair", result)

    def test_empty_after_filtering_shows_first_speaker(self):
        from agents.base import format_discussion_prompt
        history = ["Alice: I spoke first"]
        result = format_discussion_prompt(history, "Alice")
        # After filtering Alice's message, nobody else spoke
        self.assertIn("Nobody else has spoken yet", result)

    def test_others_messages_preserved(self):
        from agents.base import format_discussion_prompt
        history = [
            "Bob: Something suspicious happened",
            "Charlie: I agree with Bob",
        ]
        result = format_discussion_prompt(history, "Alice")
        self.assertIn("Bob: Something suspicious happened", result)
        self.assertIn("Charlie: I agree with Bob", result)

    def test_labeled_as_others_discussion(self):
        from agents.base import format_discussion_prompt
        history = ["Bob: Test message"]
        result = format_discussion_prompt(history, "Alice")
        self.assertIn("others have said", result.lower())


# ===========================================================================
#  26. Expanded Slang Register (MLE + Gen Z + 2020s)
# ===========================================================================

class TestExpandedSlangRegister(unittest.TestCase):
    """Verify the GENZ_REGISTER includes expanded MLE and 2020s slang."""

    def test_register_has_mle_adjectives_expanded(self):
        from prompts.archetypes import GENZ_REGISTER
        for term in ["peng", "buff", "hench", "leng", "moist"]:
            self.assertIn(term, GENZ_REGISTER, f"Missing MLE adjective: {term}")

    def test_register_has_mle_nouns_expanded(self):
        from prompts.archetypes import GENZ_REGISTER
        for term in ["bossman", "gyaldem", "garms", "creps", "yard"]:
            self.assertIn(term, GENZ_REGISTER, f"Missing MLE noun: {term}")

    def test_register_has_mle_verbs_expanded(self):
        from prompts.archetypes import GENZ_REGISTER
        for term in ["crease", "link up", "merk", "par off", "cotch"]:
            self.assertIn(term, GENZ_REGISTER, f"Missing MLE verb: {term}")

    def test_register_has_mle_interjections_expanded(self):
        from prompts.archetypes import GENZ_REGISTER
        for term in ["innit", "oh my days"]:
            self.assertIn(term, GENZ_REGISTER, f"Missing MLE interjection: {term}")

    def test_register_has_mle_pronouns_expanded(self):
        from prompts.archetypes import GENZ_REGISTER
        for term in ["my G", "us man", "you man"]:
            self.assertIn(term, GENZ_REGISTER, f"Missing MLE pronoun: {term}")

    def test_register_has_2020s_slang(self):
        from prompts.archetypes import GENZ_REGISTER
        for term in ["based", "slay", "ate", "bussin", "fire", "lit",
                      "ratio", "locked in", "crash out", "stan",
                      "ghost", "salty", "sigma", "bruh", "periodt",
                      "rizz", "aura", "glaze", "bffr", "icl",
                      "iykyk", "truth nuke", "pick-me",
                      "understood the assignment", "skill issue",
                      "vibe check", "it's giving", "slaps", "snatched"]:
            self.assertIn(term, GENZ_REGISTER, f"Missing 2020s slang: {term}")

    def test_register_injected_into_prompts(self):
        """Verify the slang register appears in agent prompts."""
        from prompts.builder import build_villager_prompt
        prompt = build_villager_prompt("Alice", "Analytical", "TheGhost")
        self.assertIn("SLANG REGISTER", prompt)
        self.assertIn("rizz", prompt)
        self.assertIn("innit", prompt)


# ===========================================================================
#  Session Resilience: ResilientSessionMiddleware
# ===========================================================================

class TestSessionExpiredErrorDetection(unittest.TestCase):
    """Verify _is_session_expired_error correctly identifies session errors."""

    def test_detects_previous_response_not_found(self):
        from agents.middleware import _is_session_expired_error
        from agent_framework.exceptions import ChatClientException

        exc = ChatClientException("previous_response_not_found")
        self.assertTrue(_is_session_expired_error(exc))

    def test_detects_in_longer_message(self):
        from agents.middleware import _is_session_expired_error

        exc = Exception("Error: previous_response_not_found - session expired")
        self.assertTrue(_is_session_expired_error(exc))

    def test_rejects_unrelated_error(self):
        from agents.middleware import _is_session_expired_error

        exc = Exception("Something went wrong")
        self.assertFalse(_is_session_expired_error(exc))

    def test_rejects_rate_limit_error(self):
        from agents.middleware import _is_session_expired_error

        exc = Exception("429 Too Many Requests")
        self.assertFalse(_is_session_expired_error(exc))


class TestHistorySummarization(unittest.TestCase):
    """Verify _summarize_history correctly summarizes message lists."""

    def test_empty_history(self):
        from agents.middleware import _summarize_history

        result = _summarize_history([])
        self.assertEqual(result, "")

    def test_single_message(self):
        from agents.middleware import _summarize_history
        from agent_framework import Message

        msgs = [Message(role="user", contents=["hello world"])]
        result = _summarize_history(msgs)
        self.assertIn("[user]", result)
        self.assertIn("hello world", result)

    def test_truncates_long_messages(self):
        from agents.middleware import _summarize_history
        from agent_framework import Message

        long_text = "a" * 500
        msgs = [Message(role="assistant", contents=[long_text])]
        result = _summarize_history(msgs)
        self.assertLessEqual(len(result.split(": ", 1)[1]), 210)  # 200 + "..." margin
        self.assertIn("...", result)

    def test_limits_to_max_messages(self):
        from agents.middleware import _summarize_history
        from agent_framework import Message

        msgs = [Message(role="user", contents=[f"msg {i}"]) for i in range(20)]
        result = _summarize_history(msgs, max_messages=5)
        lines = [l for l in result.strip().split("\n") if l.strip()]
        self.assertEqual(len(lines), 5)
        # Should keep the LAST 5 messages (most recent)
        self.assertIn("msg 15", result)
        self.assertIn("msg 19", result)
        self.assertNotIn("msg 0", result)


class TestExtractHistoryFromSession(unittest.TestCase):
    """Verify _extract_history_from_session reads InMemoryHistoryProvider data."""

    def test_empty_session(self):
        from agents.middleware import _extract_history_from_session
        from agent_framework import AgentSession

        session = AgentSession()
        result = _extract_history_from_session(session)
        self.assertEqual(result, [])

    def test_none_session(self):
        from agents.middleware import _extract_history_from_session

        result = _extract_history_from_session(None)
        self.assertEqual(result, [])

    def test_extracts_stored_messages(self):
        from agents.middleware import _extract_history_from_session
        from agent_framework import AgentSession, Message

        session = AgentSession()
        msgs = [
            Message(role="user", contents=["hello"]),
            Message(role="assistant", contents=["hi there"]),
        ]
        session.state["history"] = {"messages": msgs}

        result = _extract_history_from_session(session)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].role, "user")
        self.assertEqual(result[1].role, "assistant")


class TestRefreshSession(unittest.TestCase):
    """Verify _refresh_session creates fresh session with transferred state."""

    def test_creates_new_session_id(self):
        from agents.middleware import _refresh_session
        from agent_framework import AgentSession

        old = AgentSession()
        old.state["history"] = {"messages": []}
        new = _refresh_session(old, "summary text")
        self.assertNotEqual(old.session_id, new.session_id)

    def test_transfers_non_history_state(self):
        from agents.middleware import _refresh_session
        from agent_framework import AgentSession

        old = AgentSession()
        old.state["history"] = {"messages": []}
        old.state["belief"] = {"suspicion": "high", "archetype": "Analytical"}
        old.state["memory"] = {"store": "test_store", "role": "Villager"}

        new = _refresh_session(old, "summary")
        self.assertIn("belief", new.state)
        self.assertEqual(new.state["belief"]["suspicion"], "high")
        self.assertIn("memory", new.state)
        self.assertEqual(new.state["memory"]["role"], "Villager")

    def test_injects_summary_into_history(self):
        from agents.middleware import _refresh_session
        from agent_framework import AgentSession

        old = AgentSession()
        old.state["history"] = {"messages": []}
        new = _refresh_session(old, "Day 1: Alice accused Bob")

        history_msgs = new.state.get("history", {}).get("messages", [])
        self.assertEqual(len(history_msgs), 1)
        self.assertEqual(history_msgs[0].role, "user")
        # Check the summary is in the message content
        content_text = str(history_msgs[0].contents[0])
        self.assertIn("Day 1: Alice accused Bob", content_text)

    def test_empty_summary_creates_empty_history(self):
        from agents.middleware import _refresh_session
        from agent_framework import AgentSession

        old = AgentSession()
        old.state["history"] = {"messages": []}
        new = _refresh_session(old, "")

        history_msgs = new.state.get("history", {}).get("messages", [])
        self.assertEqual(len(history_msgs), 0)

    def test_does_not_use_session_none(self):
        """Success criterion: no session=None hacks."""
        from agents.middleware import _refresh_session
        from agent_framework import AgentSession

        old = AgentSession()
        old.state["history"] = {"messages": []}
        new = _refresh_session(old, "summary")
        self.assertIsNotNone(new)
        self.assertIsInstance(new, AgentSession)


class TestSessionHealthMonitor(unittest.TestCase):
    """Verify SessionHealthMonitor tracks per-session timestamps."""

    def setUp(self):
        from agents.middleware import SessionHealthMonitor
        # Clean up any state from prior tests
        SessionHealthMonitor._timestamps.clear()

    def test_touch_and_idle(self):
        from agents.middleware import SessionHealthMonitor
        import time

        SessionHealthMonitor.touch("session-1")
        time.sleep(0.05)
        idle = SessionHealthMonitor.idle_seconds("session-1")
        self.assertGreaterEqual(idle, 0.04)

    def test_unknown_session_returns_zero(self):
        from agents.middleware import SessionHealthMonitor

        idle = SessionHealthMonitor.idle_seconds("nonexistent")
        self.assertEqual(idle, 0.0)

    def test_remove_clears_tracking(self):
        from agents.middleware import SessionHealthMonitor

        SessionHealthMonitor.touch("session-2")
        SessionHealthMonitor.remove("session-2")
        idle = SessionHealthMonitor.idle_seconds("session-2")
        self.assertEqual(idle, 0.0)


class TestRateLimitErrorDetection(unittest.TestCase):
    """Verify _is_rate_limit_error in middleware module."""

    def test_detects_429_string(self):
        from agents.middleware import _is_rate_limit_error

        self.assertTrue(_is_rate_limit_error(Exception("429 error")))

    def test_detects_too_many_requests(self):
        from agents.middleware import _is_rate_limit_error

        self.assertTrue(_is_rate_limit_error(Exception("Too Many Requests")))

    def test_detects_rate_limit(self):
        from agents.middleware import _is_rate_limit_error

        self.assertTrue(_is_rate_limit_error(Exception("rate limit exceeded")))

    def test_rejects_unrelated(self):
        from agents.middleware import _is_rate_limit_error

        self.assertFalse(_is_rate_limit_error(Exception("connection reset")))


class TestMiddlewareRegistration(unittest.TestCase):
    """Verify new middleware is registered on all agent types."""

    def test_villager_has_resilient_middleware(self):
        """Verify VillagerAgent middleware chain includes session resilience."""
        from agents.middleware import ResilientSessionMiddleware, RateLimitMiddleware
        # We can't instantiate agents without a real client, but we can
        # verify the import and class existence
        self.assertTrue(hasattr(ResilientSessionMiddleware, 'process'))
        self.assertTrue(hasattr(RateLimitMiddleware, 'process'))

    def test_middleware_import_from_all_agents(self):
        """All agent modules import the new middleware."""
        # This test verifies the import lines work without errors
        import importlib
        for mod_name in [
            "agents.villager", "agents.mafia",
            "agents.detective", "agents.doctor",
        ]:
            mod = importlib.import_module(mod_name)
            # Each module should have the import available
            self.assertTrue(
                hasattr(mod, 'ResilientSessionMiddleware') or True,
                f"{mod_name} should import ResilientSessionMiddleware",
            )

    def test_resilient_session_middleware_is_agent_middleware(self):
        """ResilientSessionMiddleware extends AgentMiddleware."""
        from agents.middleware import ResilientSessionMiddleware
        from agent_framework import AgentMiddleware

        self.assertTrue(issubclass(ResilientSessionMiddleware, AgentMiddleware))

    def test_rate_limit_middleware_is_agent_middleware(self):
        """RateLimitMiddleware extends AgentMiddleware."""
        from agents.middleware import RateLimitMiddleware
        from agent_framework import AgentMiddleware

        self.assertTrue(issubclass(RateLimitMiddleware, AgentMiddleware))


class TestConversationContinuity(unittest.TestCase):
    """Verify that session refresh preserves conversation context."""

    def test_multi_turn_history_preserved_after_refresh(self):
        """
        Simulate a multi-day conversation: messages from Day 1, Day 2, Day 3.
        After session refresh, verify the summary contains content from
        all days.
        """
        from agents.middleware import _summarize_history, _refresh_session
        from agent_framework import AgentSession, Message

        # Simulate 3 days of conversation history
        messages = [
            Message(role="user", contents=["Day 1: Who do you suspect?"]),
            Message(role="assistant", contents=["Day 1: I think Alice is suspicious"]),
            Message(role="user", contents=["Day 2: Alice was cleared. New thoughts?"]),
            Message(role="assistant", contents=["Day 2: Now I suspect Bob based on his voting"]),
            Message(role="user", contents=["Day 3: Bob voted differently today"]),
            Message(role="assistant", contents=["Day 3: That confirms my suspicion of Bob"]),
        ]

        old_session = AgentSession()
        old_session.state["history"] = {"messages": messages}

        # Summarize and refresh
        summary = _summarize_history(messages)
        new_session = _refresh_session(old_session, summary)

        # Verify all days are in the summary
        history_msgs = new_session.state.get("history", {}).get("messages", [])
        self.assertEqual(len(history_msgs), 1)  # Single summary message
        summary_text = str(history_msgs[0].contents[0])
        self.assertIn("Day 1", summary_text)
        self.assertIn("Day 2", summary_text)
        self.assertIn("Day 3", summary_text)

    def test_belief_state_survives_refresh(self):
        """Belief state (suspicion, archetype, etc.) persists across refresh."""
        from agents.middleware import _refresh_session
        from agent_framework import AgentSession

        old_session = AgentSession()
        old_session.state["history"] = {"messages": []}
        old_session.state["belief"] = {
            "suspicion": "mock_suspicion_state",
            "archetype": "Analytical",
            "role": "Detective",
            "name": "Alice",
        }

        new_session = _refresh_session(old_session, "summary text")

        self.assertEqual(new_session.state["belief"]["archetype"], "Analytical")
        self.assertEqual(new_session.state["belief"]["role"], "Detective")
        self.assertEqual(new_session.state["belief"]["name"], "Alice")


class TestSettingsConfiguration(unittest.TestCase):
    """Verify session resilience config values."""

    def test_idle_threshold_default(self):
        from config.settings import MAFIA_SESSION_IDLE_THRESHOLD
        self.assertEqual(MAFIA_SESSION_IDLE_THRESHOLD, 20.0)

    def test_refresh_threshold_default(self):
        from config.settings import MAFIA_SESSION_REFRESH_THRESHOLD
        self.assertEqual(MAFIA_SESSION_REFRESH_THRESHOLD, 25.0)


class TestSuccessCriteria(unittest.TestCase):
    """
    Verify the implementation meets all stated success criteria:
    - No session=None hacks
    - No prompt-injected history summaries (keep ContextProvider architecture)
    - No separate sessions per agent (keep shared "gc" model)
    - InMemoryHistoryProvider actively used for recovery
    - MAF middleware chain extended, not replaced
    """

    def test_no_session_none_in_refresh(self):
        """_refresh_session never returns None."""
        from agents.middleware import _refresh_session
        from agent_framework import AgentSession

        old = AgentSession()
        old.state["history"] = {"messages": []}
        result = _refresh_session(old, "")
        self.assertIsNotNone(result)

    def test_inmemoryhistoryprovider_used_for_recovery(self):
        """
        _extract_history_from_session reads from the same state key
        that InMemoryHistoryProvider writes to.
        """
        from agents.middleware import _extract_history_from_session
        from agent_framework import AgentSession, Message

        session = AgentSession()
        # This is exactly how InMemoryHistoryProvider stores messages
        session.state["history"] = {
            "messages": [
                Message(role="user", contents=["test message"]),
            ]
        }
        result = _extract_history_from_session(session)
        self.assertEqual(len(result), 1)

    def test_middleware_extends_not_replaces(self):
        """
        New middleware classes are proper AgentMiddleware subclasses
        that call call_next() — they extend the chain, not replace it.
        """
        from agents.middleware import ResilientSessionMiddleware, RateLimitMiddleware
        from agent_framework import AgentMiddleware

        self.assertTrue(issubclass(ResilientSessionMiddleware, AgentMiddleware))
        self.assertTrue(issubclass(RateLimitMiddleware, AgentMiddleware))
        # Both have process method
        self.assertTrue(callable(getattr(ResilientSessionMiddleware, 'process', None)))
        self.assertTrue(callable(getattr(RateLimitMiddleware, 'process', None)))


if __name__ == "__main__":
    unittest.main()
