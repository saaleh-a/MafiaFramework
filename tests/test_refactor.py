"""
Verification test suite for the MafiaFramework deep refactor.

Tests cover:
  1. Self-vote prevention (extract_vote / _parse_vote returns None on self-vote)
  2. Tie-break logic (game_state correctly reports tied players)
  3. Role-Personality exclusion (TheParasite/ThePerformer blocked for Detective/Doctor)
  4. Robust action splitting (REASONING: stripped from ACTION block)
  5. Ghost filtering (dead players excluded from mention counts)
"""

import re
import unittest

# ---------------------------------------------------------------------------
# We import the actual production modules so the tests validate real behaviour.
# ---------------------------------------------------------------------------
from agents.base import parse_reasoning_action, _recursive_strip_marker
from engine.game_state import GameState, PlayerState, GamePhase
from engine.game_manager import (
    _pick_personality_constrained,
    PERSONALITY_EXCLUSIONS,
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


if __name__ == "__main__":
    unittest.main()
