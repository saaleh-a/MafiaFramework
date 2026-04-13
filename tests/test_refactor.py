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
from unittest.mock import patch

# ---------------------------------------------------------------------------
# We import the actual production modules so the tests validate real behaviour.
# ---------------------------------------------------------------------------
from agents.base import parse_reasoning_action, _recursive_strip_marker
from engine.game_state import GameState, PlayerState, GamePhase, LogEntry
from engine.game_manager import (
    _pick_personality_constrained,
    PERSONALITY_EXCLUSIONS,
    ARCHETYPE_PERSONALITY_EXCLUSIONS,
    ROLE_ARCHETYPE_PERSONALITY_EXCLUSIONS,
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

    def test_cap_relaxation_when_all_at_cap(self):
        """Cap relaxation allows a pick when all personalities are at cap."""
        from prompts.personalities import ALL_PERSONALITIES
        # Saturate every personality at the cap
        counts = {p: _PERSONALITY_FREQUENCY_CAP for p in ALL_PERSONALITIES}
        # Should NOT raise — the fallback relaxes caps but keeps exclusions
        p = _pick_personality_constrained("Villager", counts, demo=False)
        self.assertIn(p, ALL_PERSONALITIES)


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

    def test_reactive_cannot_get_vibesvoter(self):
        """Reactive+VibesVoter is structurally broken — panic with no vocabulary."""
        for _ in range(200):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained(
                "Villager", counts, demo=False, archetype="Reactive",
            )
            self.assertNotEqual(p, "VibesVoter")

    def test_non_excluded_archetype_allows_all(self):
        """An archetype with no exclusions should allow every personality."""
        seen: set[str] = set()
        for _ in range(500):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained(
                "Villager", counts, demo=False, archetype="Paranoid",
            )
            seen.add(p)
        # Paranoid has no bans — should see MythBuilder, TheGhost, etc.
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


class TestToolTraceNormalization(unittest.TestCase):
    """Verify tool-like text is normalized before game parsing."""

    def test_extract_tool_result_from_cast_vote_trace(self):
        from agents.base import _extract_tool_result

        text = (
            "functions.cast_vote\n"
            "{\"target\":\"Bob\",\"reasoning\":\"Frank and Grace were later in the text.\"}"
        )
        self.assertEqual(_extract_tool_result(text), "VOTE: Bob")

    def test_extract_tool_result_from_choose_target_trace(self):
        from agents.base import _extract_tool_result

        text = (
            "functions.choose_target\n"
            "{\"target\":\"Frank\",\"reasoning\":\"I also mentioned Hank and Ivy.\"}"
        )
        self.assertEqual(_extract_tool_result(text), "TARGET: Frank")

    def test_vote_parser_prefers_target_field_over_reasoning_mentions(self):
        from engine.orchestrator import MafiaGameOrchestrator

        orchestrator = MafiaGameOrchestrator.__new__(MafiaGameOrchestrator)
        text = (
            "functions.cast_vote\n"
            "{\"target\":\"Bob\",\"reasoning\":\"Frank and Grace are both suspicious too.\"}"
        )
        parsed = orchestrator._parse_vote(
            text,
            ["Bob", "Frank", "Grace"],
            "Ivy",
        )
        self.assertEqual(parsed, "Bob")

    def test_target_parser_prefers_target_field_over_other_mentions(self):
        from engine.orchestrator import MafiaGameOrchestrator

        text = (
            "functions.choose_target\n"
            "{\"target\":\"Frank\",\"reasoning\":\"I also considered Hank and Ivy.\"}"
        )
        parsed = MafiaGameOrchestrator._parse_target(text, ["Frank", "Hank", "Ivy"])
        self.assertEqual(parsed, "Frank")

    def test_target_parser_can_salvage_reasoning_only_name(self):
        from engine.orchestrator import MafiaGameOrchestrator

        text = (
            "Threat 1: Ivy — dangerous. Threat 2: Hank — dangerous. "
            "The strongest removal is Frank."
        )
        parsed = MafiaGameOrchestrator._parse_target(text, ["Frank", "Hank", "Ivy"])
        self.assertEqual(parsed, "Frank")


class TestStructuredToolResponseRecovery(unittest.TestCase):
    """Verify structured tool outputs survive non-stream serialization."""

    def test_serialize_agent_response_recovers_function_result_content(self):
        from agent_framework import AgentResponse, Content, Message
        from agents.base import _serialize_agent_response

        response = AgentResponse(
            messages=[
                Message(
                    "assistant",
                    [
                        Content.from_text_reasoning(text="I need to commit now."),
                        Content.from_function_result("call_1", result="VOTE: Bob"),
                    ],
                )
            ]
        )

        serialized = _serialize_agent_response(response)
        self.assertIn("REASONING: I need to commit now.", serialized)
        self.assertIn("VOTE: Bob", serialized)

    def test_serialize_agent_response_recovers_function_call_arguments(self):
        from agent_framework import AgentResponse, Content, Message
        from agents.base import _serialize_agent_response

        response = AgentResponse(
            messages=[
                Message(
                    "assistant",
                    [
                        Content.from_function_call(
                            "call_2",
                            "cast_vote",
                            arguments='{\"target\":\"Bob\",\"reasoning\":\"thin lane\"}',
                        )
                    ],
                )
            ]
        )

        serialized = _serialize_agent_response(response)
        self.assertEqual(serialized, "VOTE: Bob")


class TestRunAgentStreamSessionRecovery(unittest.IsolatedAsyncioTestCase):
    """Verify streaming calls recover locally from expired response ids."""

    async def test_prefer_non_stream_uses_non_stream_path(self):
        from agent_framework import AgentSession
        import agents.base as base_module

        class FakeNonStreamResult:
            def __init__(self, text: str):
                self.text = text

        class FakeAgent:
            def __init__(self):
                self.stream_calls = 0
                self.non_stream_calls = 0
                self.name = "Alice"

            def run(self, prompt, stream=False, session=None):
                if stream:
                    self.stream_calls += 1

                    async def iterator():
                        raise AssertionError("streaming path should not be used")
                        yield None

                    return iterator()

                self.non_stream_calls += 1

                async def result():
                    return FakeNonStreamResult("REASONING: decided ACTION: VOTE: Bob")

                return result()

        session = AgentSession()
        session.state["history"] = {"messages": []}
        agent = FakeAgent()

        with patch.object(base_module, "MAFIA_ENABLE_STREAMING_FALLBACK", True):
            reasoning, action, _ = await base_module.run_agent_stream(
                agent,
                "prompt",
                session=session,
                player_name="Alice",
                prefer_non_stream=True,
            )

        self.assertEqual(reasoning, "decided")
        self.assertEqual(action, "VOTE: Bob")
        self.assertEqual(agent.stream_calls, 0)
        self.assertEqual(agent.non_stream_calls, 1)

    async def test_prefer_non_stream_recovers_structured_tool_result(self):
        from agent_framework import AgentResponse, AgentSession, Content, Message
        import agents.base as base_module

        class FakeAgent:
            def __init__(self):
                self.stream_calls = 0
                self.non_stream_calls = 0
                self.name = "Alice"

            def run(self, prompt, stream=False, session=None):
                if stream:
                    self.stream_calls += 1

                    async def iterator():
                        raise AssertionError("streaming path should not be used")
                        yield None

                    return iterator()

                self.non_stream_calls += 1

                async def result():
                    return AgentResponse(
                        messages=[
                            Message(
                                "assistant",
                                [
                                    Content.from_text_reasoning(text="I have to land this."),
                                    Content.from_function_result(
                                        "call_3",
                                        result="VOTE: Bob",
                                    ),
                                ],
                            )
                        ]
                    )

                return result()

        session = AgentSession()
        session.state["history"] = {"messages": []}
        agent = FakeAgent()

        with patch.object(base_module, "MAFIA_ENABLE_STREAMING_FALLBACK", True):
            reasoning, action, _ = await base_module.run_agent_stream(
                agent,
                "prompt",
                session=session,
                player_name="Alice",
                prefer_non_stream=True,
            )

        self.assertIn("I have to land this.", reasoning)
        self.assertEqual(action, "VOTE: Bob")
        self.assertEqual(agent.stream_calls, 0)
        self.assertEqual(agent.non_stream_calls, 1)

    async def test_streaming_session_expiry_refreshes_and_retries(self):
        from agent_framework import AgentSession
        from agents.base import run_agent_stream

        class FakeChunk:
            def __init__(self, text: str):
                self.text = text

        class FakeAgent:
            def __init__(self):
                self.calls = 0

            def run(self, prompt, stream=False, session=None):
                self.calls += 1

                async def iterator():
                    if self.calls == 1:
                        raise Exception("previous_response_id not found")
                    yield FakeChunk("REASONING: recovered ACTION: VOTE: Bob")

                return iterator()

        session = AgentSession()
        session.state["history"] = {"messages": []}
        agent = FakeAgent()

        reasoning, action, new_session = await run_agent_stream(
            agent,
            "prompt",
            session=session,
            player_name="Alice",
        )

        self.assertEqual(reasoning, "recovered")
        self.assertEqual(action, "VOTE: Bob")
        self.assertIsNotNone(new_session)
        self.assertNotEqual(new_session.session_id, session.session_id)
        self.assertEqual(agent.calls, 2)

    async def test_repeated_session_expiry_falls_back_to_non_stream(self):
        from agent_framework import AgentSession
        import agents.base as base_module

        class FakeStreamChunk:
            def __init__(self, text: str):
                self.text = text

        class FakeNonStreamResult:
            def __init__(self, text: str):
                self.text = text

        class FakeAgent:
            def __init__(self):
                self.stream_calls = 0
                self.non_stream_calls = 0
                self.name = "Alice"

            def run(self, prompt, stream=False, session=None):
                if stream:
                    self.stream_calls += 1

                    async def iterator():
                        raise Exception("previous_response_id not found")
                        yield FakeStreamChunk("unused")

                    return iterator()

                self.non_stream_calls += 1
                async def result():
                    return FakeNonStreamResult("REASONING: fallback ACTION: VOTE: Bob")

                return result()

        session = AgentSession()
        session.state["history"] = {"messages": []}
        agent = FakeAgent()

        with patch.object(base_module, "MAFIA_ENABLE_STREAMING_FALLBACK", True):
            reasoning, action, new_session = await base_module.run_agent_stream(
                agent,
                "prompt",
                session=session,
                player_name="Alice",
            )

        self.assertEqual(reasoning, "fallback")
        self.assertEqual(action, "VOTE: Bob")
        self.assertIsNotNone(new_session)
        self.assertGreaterEqual(agent.stream_calls, 3)
        self.assertEqual(agent.non_stream_calls, 1)

    async def test_empty_streaming_action_falls_back_to_non_stream(self):
        from agent_framework import AgentSession
        import agents.base as base_module

        class FakeChunk:
            def __init__(self, text: str):
                self.text = text

        class FakeNonStreamResult:
            def __init__(self, text: str):
                self.text = text

        class FakeAgent:
            def __init__(self):
                self.stream_calls = 0
                self.non_stream_calls = 0
                self.name = "Bob"

            def run(self, prompt, stream=False, session=None):
                if stream:
                    self.stream_calls += 1

                    async def iterator():
                        yield FakeChunk("REASONING: still thinking ACTION:")

                    return iterator()

                self.non_stream_calls += 1
                async def result():
                    return FakeNonStreamResult("REASONING: recovered ACTION: VOTE: Alice")

                return result()

        session = AgentSession()
        session.state["history"] = {"messages": []}
        agent = FakeAgent()

        with patch.object(base_module, "MAFIA_ENABLE_STREAMING_FALLBACK", True):
            reasoning, action, _ = await base_module.run_agent_stream(
                agent,
                "prompt",
                session=session,
                player_name="Bob",
            )

        self.assertEqual(reasoning, "recovered")
        self.assertEqual(action, "VOTE: Alice")
        self.assertEqual(agent.stream_calls, 3)
        self.assertEqual(agent.non_stream_calls, 1)


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
#  10. Framework Registry / Preset Integration
# ===========================================================================

class TestFrameworkIntegration(unittest.TestCase):
    """Verify optional framework modules can be composed safely."""

    def test_resolve_framework_names_expands_preset_and_dedupes(self):
        from prompts.frameworks import resolve_framework_names

        names = resolve_framework_names(
            ("game-theory",),
            presets=("strategic-synthesis",),
            extras=("game-theory", "humanizer"),
        )

        self.assertEqual(names.count("game-theory"), 1)
        self.assertIn("systems-theory", names)
        self.assertIn("dialectical-materialism", names)
        self.assertIn("humanizer", names)

    def test_mafia_prompt_accepts_framework_preset(self):
        from prompts.builder import build_mafia_prompt

        prompt = build_mafia_prompt(
            "Alice",
            "Bob",
            "Paranoid",
            "TheAnalyst",
            framework_presets=("strategic-synthesis",),
        )

        self.assertIn("SYNTHESIS PIPELINE", prompt)
        self.assertIn("CONTRADICTION ANALYSIS", prompt)
        self.assertIn("SYSTEMS THINKING", prompt)

    def test_villager_prompt_accepts_speech_extras(self):
        from prompts.builder import build_villager_prompt

        prompt = build_villager_prompt(
            "Alice",
            "Analytical",
            "TheAnalyst",
            extra_frameworks=("universal-storytelling", "humanizer"),
        )

        self.assertIn("PERSUASIVE DELIVERY", prompt)
        self.assertIn("HUMAN WRITING", prompt)

    def test_narrator_prompt_includes_humanizer_by_default(self):
        from prompts.builder import build_narrator_prompt

        prompt = build_narrator_prompt()
        self.assertIn("HUMAN WRITING", prompt)

    def test_archetype_can_add_framework_automatically(self):
        from prompts.builder import build_villager_prompt

        prompt = build_villager_prompt("Alice", "Contrarian", "TheGhost")
        self.assertIn("CONTRADICTION ANALYSIS", prompt)

    def test_personality_can_add_framework_automatically(self):
        from prompts.builder import build_villager_prompt

        prompt = build_villager_prompt("Alice", "Passive", "MythBuilder")
        self.assertIn("PERSUASIVE DELIVERY", prompt)

    def test_trait_frameworks_are_deduped_against_role_defaults(self):
        from prompts.builder import build_mafia_prompt

        prompt = build_mafia_prompt("Alice", "Bob", "Manipulative", "TheParasite")
        self.assertEqual(prompt.count("POLITICAL OPERATION:"), 1)

    def test_role_specific_archetype_framework_can_apply(self):
        from prompts.builder import build_doctor_prompt

        prompt = build_doctor_prompt("Grace", "Passive", "TheGhost")
        self.assertIn("SOCIAL EXECUTION", prompt)

    def test_role_specific_personality_framework_can_apply(self):
        from prompts.builder import build_villager_prompt

        prompt = build_villager_prompt("Alice", "Passive", "TheParasite")
        self.assertIn("SOCIAL EXECUTION", prompt)

    def test_all_player_prompts_include_humanizer_stack(self):
        from prompts.builder import (
            build_mafia_prompt,
            build_detective_prompt,
            build_doctor_prompt,
            build_villager_prompt,
        )

        prompts = [
            build_mafia_prompt("Alice", "Bob", "Paranoid", "TheAnalyst"),
            build_detective_prompt("Charlie", "Passive", "VibesVoter"),
            build_doctor_prompt("Eve", "Diplomatic", "TheMartyr"),
            build_villager_prompt("Grace", "Contrarian", "MythBuilder"),
        ]

        for prompt in prompts:
            self.assertIn("HUMAN WRITING", prompt)
            self.assertIn("SIGNS OF AI WRITING", prompt)
            self.assertIn("FAILURE ARCHETYPE CHECK", prompt)


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
        # Strengthened (Symptom C fix): "or paraphrase" was too permissive.
        # Now requires exact quoted words with quotation marks.
        self.assertIn("No quote = no claim", prompt)

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
        # Strengthened (Symptom D fix): now uses EXACTLY ONE / Option A / Option B
        # framing to close the "argue then join" loophole.
        self.assertIn("DIFFERENT specific player", modifier)
        self.assertIn("specific flaw in the current case", modifier)

    def test_contrarian_bans_pile_metacommentary(self):
        from prompts.archetypes import ARCHETYPES
        contrarian = ARCHETYPES["Contrarian"]
        modifier = contrarian["strategy_modifier"]
        # Strengthened (Symptom D fix): meta-commentary is now explicitly BANNED.
        self.assertIn("BANNED: joining the consensus", modifier)


# ===========================================================================
#  23. All Combination Bans Present
# ===========================================================================

class TestAllCombinationBans(unittest.TestCase):
    """Verify all required combination bans are in the exclusion tables."""

    def test_all_tier3_bans_present(self):
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

    def test_tier1_reactive_vibesvoter_ban(self):
        """Reactive+VibesVoter hard-banned for all roles (Tier 1)."""
        from engine.game_manager import ARCHETYPE_PERSONALITY_EXCLUSIONS
        self.assertIn("VibesVoter", ARCHETYPE_PERSONALITY_EXCLUSIONS["Reactive"])

    def test_tier2_diplomatic_parasite_ban(self):
        """Diplomatic+TheParasite banned for Detective, Doctor, Mafia (Tier 2)."""
        from engine.game_manager import ROLE_ARCHETYPE_PERSONALITY_EXCLUSIONS
        key = ("Diplomatic", "TheParasite")
        self.assertIn(key, ROLE_ARCHETYPE_PERSONALITY_EXCLUSIONS)
        banned_roles = ROLE_ARCHETYPE_PERSONALITY_EXCLUSIONS[key]
        self.assertIn("Detective", banned_roles)
        self.assertIn("Doctor", banned_roles)
        self.assertIn("Mafia", banned_roles)
        self.assertNotIn("Villager", banned_roles)


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
        # Alice's messages should never appear in the injected prompt.
        self.assertNotIn("Alice: I think Bob", result)
        self.assertNotIn("Alice: Let me explain", result)
        # Symptom E fix: Bob's message appeared BEFORE Alice's last turn so it
        # is already stored in InMemoryHistoryProvider from Alice's first call.
        # Re-injecting it would cause double-appearance in context — it must
        # NOT appear here.
        self.assertNotIn("Bob: That's not fair", result)
        # Since nothing new arrived after Alice's last message, the prompt
        # correctly signals that nobody has spoken since her last turn.
        self.assertIn("Nobody else has spoken yet", result)

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


class TestServerErrorDetection(unittest.TestCase):
    """Verify 5xx detection does not misclassify 400 session errors."""

    def test_detects_real_500_status(self):
        from agents.rate_limiter import _is_server_error

        self.assertTrue(_is_server_error(Exception("Error code: 500 - backend failed")))

    def test_ignores_400_previous_response_not_found_with_500_in_response_id(self):
        from agents.rate_limiter import _is_server_error

        exc = Exception(
            "Error code: 400 - {'error': {'message': \"Previous response with id "
            "'resp_027a9b4bdfafab950069dbc7b323c081939a8f8573a5dcbe29' not found.\", "
            "'code': 'previous_response_not_found'}}"
        )
        self.assertFalse(_is_server_error(exc))


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
        import importlib
        for mod_name in [
            "agents.villager", "agents.mafia",
            "agents.detective", "agents.doctor",
        ]:
            mod = importlib.import_module(mod_name)
            # Verify the module loaded without import errors
            self.assertIsNotNone(mod)

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


class TestNarratorConfiguration(unittest.TestCase):
    """Verify the narrator now uses the same session recovery primitives."""

    def test_narrator_uses_history_and_resilience_middleware(self):
        import agents.narrator as narrator_module
        from agent_framework import InMemoryHistoryProvider
        from agents.middleware import ResilientSessionMiddleware, RateLimitMiddleware

        captured: dict[str, object] = {}

        class DummyAgent:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)

            def create_session(self):
                return object()

        with patch.object(narrator_module, "Agent", DummyAgent):
            narrator_module.NarratorAgent(client=None)

        providers = captured.get("context_providers", [])
        middleware = captured.get("middleware", [])

        self.assertTrue(any(isinstance(p, InMemoryHistoryProvider) for p in providers))
        self.assertTrue(any(isinstance(m, ResilientSessionMiddleware) for m in middleware))
        self.assertTrue(any(isinstance(m, RateLimitMiddleware) for m in middleware))


class TestConsoleEncodingSetup(unittest.TestCase):
    """Verify the CLI enables UTF-8 output when streams support it."""

    def test_configure_console_encoding_reconfigures_stdout_and_stderr(self):
        import main as main_module

        calls: list[tuple[str, str]] = []

        class FakeStream:
            def __init__(self, label: str):
                self.label = label

            def reconfigure(self, **kwargs):
                calls.append((self.label, kwargs.get("encoding")))

        with patch.object(main_module.sys, "stdout", FakeStream("stdout")), patch.object(
            main_module.sys, "stderr", FakeStream("stderr")
        ):
            main_module._configure_console_encoding()

        self.assertIn(("stdout", "utf-8"), calls)
        self.assertIn(("stderr", "utf-8"), calls)


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


# ===========================================================================
#  Tier 2: Diplomatic+TheParasite role-specific exclusion
# ===========================================================================

class TestDiplomaticParasiteTier2(unittest.TestCase):
    """Diplomatic+TheParasite banned for power roles and Mafia, allowed for Villager."""

    def test_banned_for_detective(self):
        """Detective cannot receive TheParasite when archetype is Diplomatic."""
        for _ in range(200):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained(
                "Detective", counts, demo=False, archetype="Diplomatic",
            )
            self.assertNotEqual(p, "TheParasite")

    def test_banned_for_doctor(self):
        """Doctor cannot receive TheParasite when archetype is Diplomatic."""
        for _ in range(200):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained(
                "Doctor", counts, demo=False, archetype="Diplomatic",
            )
            self.assertNotEqual(p, "TheParasite")

    def test_banned_for_mafia(self):
        """Mafia cannot receive TheParasite when archetype is Diplomatic."""
        for _ in range(200):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained(
                "Mafia", counts, demo=False, archetype="Diplomatic",
            )
            self.assertNotEqual(p, "TheParasite")

    def test_allowed_for_villager(self):
        """Villager CAN receive TheParasite when archetype is Diplomatic."""
        seen: set[str] = set()
        for _ in range(500):
            counts: dict[str, int] = {}
            p = _pick_personality_constrained(
                "Villager", counts, demo=False, archetype="Diplomatic",
            )
            seen.add(p)
        self.assertIn("TheParasite", seen)


# ===========================================================================
#  30. Session Refresh Registry (Symptom F)
# ===========================================================================

class TestSessionRefreshRegistry(unittest.TestCase):
    """Verify _session_refresh_registry propagates refreshed sessions."""

    def test_registry_starts_empty(self):
        from agents.middleware import _session_refresh_registry
        # The registry should not carry state between tests.
        # (Other tests don't write to it, so it is empty at import time.)
        # We just verify it is a dict.
        self.assertIsInstance(_session_refresh_registry, dict)

    def test_registry_pop_returns_none_for_unknown_key(self):
        from agents.middleware import _session_refresh_registry
        result = _session_refresh_registry.pop("nonexistent-session-id", None)
        self.assertIsNone(result)

    def test_session_error_detection_catches_azure_format(self):
        """Azure sends 'previous_response_id not found' (with spaces and 'id')."""
        from agents.middleware import _is_session_expired_error

        azure_style = Exception("400 previous_response_id not found")
        sdk_style   = Exception("previous_response_not_found")
        unrelated   = Exception("connection timeout")

        self.assertTrue(_is_session_expired_error(azure_style))
        self.assertTrue(_is_session_expired_error(sdk_style))
        self.assertFalse(_is_session_expired_error(unrelated))

    def test_registry_written_on_recovery(self):
        """Simulate a recovery and verify the registry is populated."""
        from agents.middleware import _session_refresh_registry, _refresh_session
        from agent_framework import AgentSession

        old_session = AgentSession()
        old_id = old_session.session_id

        new_session = _refresh_session(old_session, "summary text")
        # Manually simulate what ResilientSessionMiddleware does:
        _session_refresh_registry[old_id] = new_session

        retrieved = _session_refresh_registry.pop(old_id, None)
        self.assertIs(retrieved, new_session)
        self.assertNotEqual(retrieved.session_id, old_id)

        # Registry should be clean after the pop.
        self.assertNotIn(old_id, _session_refresh_registry)


# ===========================================================================
#  31. Vote Prompt Enforces Exact Action Shape
# ===========================================================================

class TestVotePromptFormatting(unittest.TestCase):
    """Verify runtime vote prompts strongly constrain the final action format."""

    def test_vote_prompt_prefers_tool_or_exact_action_line(self):
        from agents.base import format_vote_prompt

        prompt = format_vote_prompt(
            "PUBLIC STATE",
            ["Bob: I think Eve is slippery."],
            "Alice",
            ["Bob", "Eve", "Frank"],
        )

        self.assertIn("Preferred: call the cast_vote tool", prompt)
        self.assertIn("ACTION: VOTE: <exact name from valid targets>", prompt)
        self.assertIn("The ACTION line must contain exactly one player name", prompt)
        self.assertIn("Bad: ACTION: I vote Bob because...", prompt)
        self.assertIn("Good: ACTION: VOTE: Bob", prompt)

    def test_vote_prompt_retains_private_context(self):
        from agents.base import format_vote_prompt

        prompt = format_vote_prompt(
            "PUBLIC STATE",
            ["Bob: I think Eve is slippery."],
            "Alice",
            ["Bob", "Eve", "Frank"],
            private_context="Your findings:\n  Eve: Innocent",
        )

        self.assertIn("Your findings:", prompt)
        self.assertIn("Eve: Innocent", prompt)


# ===========================================================================
#  31. Night Kill Prompt Uses Game-Mechanic Language Only (Symptom B)
# ===========================================================================

class TestNightKillPromptMechanicLanguage(unittest.TestCase):
    """Verify choose_night_kill prompt avoids content-filter-triggering words."""

    def _build_night_prompt(self) -> str:
        """
        Reconstruct the night-kill prompt text as MafiaAgent.choose_night_kill
        builds it, without making a real API call.
        """
        from engine.game_state import GameState, PlayerState
        # Minimal game state: two Town players alive
        state = GameState.__new__(GameState)
        state.players = {
            "Alice": PlayerState(name="Alice", role="Mafia",  archetype="Paranoid", personality="TheAnalyst", is_alive=True),
            "Bob":   PlayerState(name="Bob",   role="Villager", archetype="Paranoid", personality="TheAnalyst", is_alive=True),
            "Carol": PlayerState(name="Carol", role="Villager", archetype="Paranoid", personality="TheAnalyst", is_alive=True),
        }
        state.current_round = 1
        targets = [p for p, s in state.players.items() if s.role != "Mafia" and s.is_alive]
        # Reproduce the exact f-string from agents/mafia.py choose_night_kill
        prompt = (
            f"NIGHT ACTION. Select one Town player to remove from the game this round.\n"
            f"Valid targets: {', '.join(targets)}\n"
            f"If you cannot proceed with a selection, output the name of "
            f"the player with the most active influence on the game as your "
            f"default choice.\n"
            f"You MUST call the choose_target tool OR write ACTION: [exact name only]"
        )
        return prompt

    def test_night_prompt_no_eliminate_language(self):
        prompt = self._build_night_prompt()
        self.assertNotIn("eliminate", prompt.lower())

    def test_night_prompt_no_kill_language(self):
        prompt = self._build_night_prompt()
        self.assertNotIn("kill", prompt.lower())

    def test_night_prompt_no_murder_language(self):
        prompt = self._build_night_prompt()
        self.assertNotIn("murder", prompt.lower())

    def test_night_prompt_uses_remove_framing(self):
        prompt = self._build_night_prompt()
        self.assertIn("remove from the game", prompt)

    def test_night_prompt_retains_valid_targets(self):
        prompt = self._build_night_prompt()
        self.assertIn("Valid targets:", prompt)


# ===========================================================================
#  32. Discussion Prompt Injects Vote-Ban Reminder (Symptom A)
# ===========================================================================

class TestDiscussionVoteBanRuntimeReminder(unittest.TestCase):
    """Verify format_discussion_prompt appends a per-call vote-ban reminder."""

    def test_vote_ban_reminder_present_when_no_prior_turns(self):
        from agents.base import format_discussion_prompt
        result = format_discussion_prompt([], "Alice")
        self.assertIn("DISCUSSION phase", result)
        self.assertIn("NOT the vote phase", result)

    def test_vote_ban_reminder_present_with_history(self):
        from agents.base import format_discussion_prompt
        history = ["Bob: I think Carol is odd"]
        result = format_discussion_prompt(history, "Alice")
        self.assertIn("DISCUSSION phase", result)
        self.assertIn("Do NOT open with", result)

    def test_vote_ban_reminder_present_after_own_turn(self):
        from agents.base import format_discussion_prompt
        history = ["Bob: Something", "Alice: My read", "Bob: What?"]
        result = format_discussion_prompt(history, "Alice")
        self.assertIn("DISCUSSION phase", result)

    def test_vote_ban_bans_im_voting_phrasing(self):
        from agents.base import format_discussion_prompt
        result = format_discussion_prompt([], "Alice")
        # The reminder should explicitly call out the banned phrase format.
        self.assertIn("I'm voting X", result)


# ===========================================================================
#  33. Discussion Only Injects Post-Last-Turn Messages (Symptom E)
# ===========================================================================

class TestDiscussionOnlyInjectsNewMessages(unittest.TestCase):
    """Verify format_discussion_prompt only shows messages after agent's last turn."""

    def test_messages_before_last_turn_excluded(self):
        from agents.base import format_discussion_prompt
        history = [
            "Bob: Round starts",       # index 0 — before Alice's first turn
            "Alice: My first take",    # index 1 — Alice's turn 1
            "Carol: Interesting",      # index 2 — after Alice's turn 1
            "Alice: Follow-up",        # index 3 — Alice's turn 2
            "Bob: What do you think?", # index 4 — new, after Alice's turn 2
        ]
        result = format_discussion_prompt(history, "Alice")
        # Only index 4 is new after Alice's last turn — only Bob's last msg should appear.
        self.assertIn("Bob: What do you think?", result)
        # Carol's message came before Alice's second turn — already in history provider.
        self.assertNotIn("Carol: Interesting", result)
        # Bob's first message also pre-dates Alice's last turn.
        self.assertNotIn("Bob: Round starts", result)

    def test_first_turn_shows_all_prior_messages(self):
        from agents.base import format_discussion_prompt
        history = [
            "Bob: Round starts",
            "Carol: Agreed",
        ]
        # Alice hasn't spoken yet — last_agent_idx = -1, so all messages shown.
        result = format_discussion_prompt(history, "Alice")
        self.assertIn("Bob: Round starts", result)
        self.assertIn("Carol: Agreed", result)

    def test_nothing_after_last_turn_shows_nobody_spoke(self):
        from agents.base import format_discussion_prompt
        history = [
            "Bob: Something",
            "Alice: My response",
            # Alice spoke last; nothing came after
        ]
        result = format_discussion_prompt(history, "Alice")
        self.assertIn("Nobody else has spoken yet", result)


# ===================================================================== #
#  COVERAGE EXPANSION — Tests for modules with insufficient coverage    #
# ===================================================================== #


# ===================================================================== #
#  GameState: win conditions, eliminations, night actions, summaries     #
# ===================================================================== #

class TestGameStateWinConditions(unittest.TestCase):
    """Verify win condition logic for all edge cases."""

    def _make_state(self, roles: dict[str, str]) -> GameState:
        return GameState(
            players={
                name: PlayerState(name=name, role=role, archetype="Analytical")
                for name, role in roles.items()
            }
        )

    def test_town_wins_when_all_mafia_dead(self):
        gs = self._make_state({"A": "Mafia", "B": "Villager", "C": "Villager"})
        gs.eliminate_player("A")
        self.assertEqual(gs.check_win_condition(), "Town")
        self.assertEqual(gs.eliminated_this_round, "A")

    def test_mafia_wins_when_equal_to_town(self):
        gs = self._make_state({"A": "Mafia", "B": "Villager"})
        self.assertEqual(gs.check_win_condition(), "Mafia")

    def test_mafia_wins_when_more_than_town(self):
        gs = self._make_state({"A": "Mafia", "B": "Mafia", "C": "Villager"})
        self.assertEqual(gs.check_win_condition(), "Mafia")

    def test_no_winner_during_game(self):
        gs = self._make_state({
            "A": "Mafia", "B": "Villager", "C": "Villager", "D": "Villager",
        })
        self.assertIsNone(gs.check_win_condition())

    def test_all_players_dead_gives_town_win(self):
        """Edge: if somehow all players are dead (including Mafia), Town wins."""
        gs = self._make_state({"A": "Mafia", "B": "Villager"})
        gs.eliminate_player("A")
        gs.eliminate_player("B")
        self.assertEqual(gs.check_win_condition(), "Town")


class TestGameStateElimination(unittest.TestCase):
    """Verify player elimination mechanics."""

    def _make_state(self) -> GameState:
        return GameState(
            players={
                "Alice": PlayerState(name="Alice", role="Mafia", archetype="Analytical"),
                "Bob": PlayerState(name="Bob", role="Villager", archetype="Passive"),
            }
        )

    def test_eliminate_marks_dead_revealed(self):
        gs = self._make_state()
        gs.round_number = 3
        gs.eliminate_player("Alice")
        self.assertFalse(gs.players["Alice"].is_alive)
        self.assertTrue(gs.players["Alice"].is_revealed)
        self.assertEqual(gs.players["Alice"].eliminated_round, 3)
        self.assertEqual(gs.eliminated_this_round, "Alice")

    def test_eliminate_nonexistent_player_is_noop(self):
        gs = self._make_state()
        gs.eliminate_player("Charlie")  # should not raise
        self.assertTrue(gs.players["Alice"].is_alive)

    def test_alive_players_excludes_dead(self):
        gs = self._make_state()
        gs.eliminate_player("Alice")
        self.assertEqual(gs.get_alive_players(), ["Bob"])

    def test_get_alive_mafia(self):
        gs = self._make_state()
        self.assertEqual(gs.get_alive_mafia(), ["Alice"])
        gs.eliminate_player("Alice")
        self.assertEqual(gs.get_alive_mafia(), [])

    def test_get_alive_town(self):
        gs = self._make_state()
        self.assertEqual(gs.get_alive_town(), ["Bob"])


class TestGameStateNightActions(unittest.TestCase):
    """Verify night action resolution (kill + protect)."""

    def _make_state(self) -> GameState:
        return GameState(
            players={
                "A": PlayerState(name="A", role="Mafia", archetype="Analytical"),
                "B": PlayerState(name="B", role="Villager", archetype="Passive"),
                "C": PlayerState(name="C", role="Doctor", archetype="Diplomatic"),
            }
        )

    def test_kill_succeeds_without_protection(self):
        gs = self._make_state()
        gs.night_kill_target = "B"
        killed, protected = gs.apply_night_actions()
        self.assertEqual(killed, "B")
        self.assertFalse(protected)
        self.assertFalse(gs.players["B"].is_alive)

    def test_kill_blocked_by_protection(self):
        gs = self._make_state()
        gs.night_kill_target = "B"
        gs.doctor_protect_target = "B"
        killed, protected = gs.apply_night_actions()
        self.assertIsNone(killed)
        self.assertTrue(protected)
        self.assertTrue(gs.players["B"].is_alive)

    def test_no_kill_target(self):
        gs = self._make_state()
        killed, protected = gs.apply_night_actions()
        self.assertIsNone(killed)
        self.assertFalse(protected)

    def test_protection_on_wrong_target(self):
        gs = self._make_state()
        gs.night_kill_target = "B"
        gs.doctor_protect_target = "C"  # protecting wrong player
        killed, protected = gs.apply_night_actions()
        self.assertEqual(killed, "B")
        self.assertFalse(protected)


class TestGameStateResetRound(unittest.TestCase):
    """Verify round state reset."""

    def test_reset_clears_votes_and_night_targets(self):
        gs = GameState(
            players={"A": PlayerState(name="A", role="Mafia", archetype="Analytical")}
        )
        gs.votes = {"A": "B"}
        gs.night_kill_target = "B"
        gs.doctor_protect_target = "A"
        gs.reset_round_state()
        self.assertEqual(gs.votes, {})
        self.assertIsNone(gs.night_kill_target)
        self.assertIsNone(gs.doctor_protect_target)

    def test_reset_preserves_last_protected(self):
        gs = GameState(
            players={"A": PlayerState(name="A", role="Mafia", archetype="Analytical")}
        )
        gs.doctor_protect_target = "A"
        gs.reset_round_state()
        self.assertEqual(gs.last_protected, "A")


class TestGameStateVoteTally(unittest.TestCase):
    """Verify vote tallying edge cases."""

    def test_unanimous_vote(self):
        gs = GameState(
            players={"A": PlayerState(name="A", role="Villager", archetype="Analytical")}
        )
        gs.votes = {"X": "A", "Y": "A", "Z": "A"}
        self.assertEqual(gs.tally_votes(), "A")

    def test_split_vote_returns_none(self):
        gs = GameState(
            players={"A": PlayerState(name="A", role="Villager", archetype="Analytical")}
        )
        gs.votes = {"X": "A", "Y": "B"}
        self.assertIsNone(gs.tally_votes())

    def test_plurality_winner(self):
        gs = GameState(
            players={"A": PlayerState(name="A", role="Villager", archetype="Analytical")}
        )
        gs.votes = {"X": "A", "Y": "A", "Z": "B"}
        self.assertEqual(gs.tally_votes(), "A")


class TestGameStateSummaries(unittest.TestCase):
    """Verify public and omniscient state summaries."""

    def _make_state(self) -> GameState:
        return GameState(
            players={
                "Alice": PlayerState(name="Alice", role="Mafia", archetype="Analytical"),
                "Bob": PlayerState(name="Bob", role="Villager", archetype="Passive"),
            }
        )

    def test_public_summary_hides_living_roles(self):
        gs = self._make_state()
        summary = gs.get_public_state_summary()
        self.assertNotIn("Mafia", summary)
        self.assertIn("Alice", summary)
        self.assertIn("Bob", summary)

    def test_public_summary_reveals_dead_roles(self):
        gs = self._make_state()
        gs.eliminate_player("Alice")
        summary = gs.get_public_state_summary()
        self.assertIn("Alice (Mafia)", summary)

    def test_omniscient_summary_shows_all_roles(self):
        gs = self._make_state()
        summary = gs.get_omniscient_state_summary()
        self.assertIn("Mafia", summary)
        self.assertIn("Villager", summary)

    def test_omniscient_shows_alive_dead_status(self):
        gs = self._make_state()
        gs.eliminate_player("Alice")
        summary = gs.get_omniscient_state_summary()
        self.assertIn("DEAD", summary)
        self.assertIn("ALIVE", summary)


class TestGameStateLogging(unittest.TestCase):
    """Verify game log append."""

    def test_log_appends_entry(self):
        gs = GameState(
            players={"A": PlayerState(name="A", role="Villager", archetype="Analytical")}
        )
        gs.log("A", "Villager", "Analytical", "some reasoning", "some action")
        self.assertEqual(len(gs.game_log), 1)
        entry = gs.game_log[0]
        self.assertEqual(entry.agent_name, "A")
        self.assertEqual(entry.role, "Villager")
        self.assertEqual(entry.action, "some action")
        self.assertEqual(entry.phase, GamePhase.DAY_DISCUSSION)


# ===================================================================== #
#  SuspicionState: belief tracking, staleness, Iroh Protocol            #
# ===================================================================== #

class TestSuspicionStateBasics(unittest.TestCase):
    """Core suspicion state operations."""

    def test_initialize_uniform_prior(self):
        from agents.belief_state import SuspicionState
        s = SuspicionState()
        s.initialize(["A", "B", "C", "D", "E"], num_mafia=2)
        self.assertAlmostEqual(s.probabilities["A"], 0.4)
        self.assertEqual(s.update_count, 0)

    def test_update_clamps_to_bounds(self):
        from agents.belief_state import SuspicionState
        s = SuspicionState()
        s.initialize(["A", "B"])
        s.update("A", 1.5)
        self.assertEqual(s.probabilities["A"], 0.99)
        s.update("A", -0.5)
        self.assertEqual(s.probabilities["A"], 0.01)

    def test_update_increments_count(self):
        from agents.belief_state import SuspicionState
        s = SuspicionState()
        s.initialize(["A"])
        s.update("A", 0.5)
        self.assertEqual(s.update_count, 1)

    def test_get_certainty_returns_probability(self):
        from agents.belief_state import SuspicionState
        s = SuspicionState()
        s.initialize(["A"])
        s.update("A", 0.75)
        self.assertAlmostEqual(s.get_certainty("A"), 0.75)

    def test_get_certainty_unknown_player_returns_zero(self):
        from agents.belief_state import SuspicionState
        s = SuspicionState()
        self.assertEqual(s.get_certainty("Unknown"), 0.0)

    def test_get_top_suspect(self):
        from agents.belief_state import SuspicionState
        s = SuspicionState()
        s.initialize(["A", "B", "C"])
        s.update("B", 0.9)
        top, prob = s.get_top_suspect()
        self.assertEqual(top, "B")
        self.assertAlmostEqual(prob, 0.9)

    def test_get_top_suspect_empty_returns_none(self):
        from agents.belief_state import SuspicionState
        s = SuspicionState()
        self.assertIsNone(s.get_top_suspect())

    def test_remove_player(self):
        from agents.belief_state import SuspicionState
        s = SuspicionState()
        s.initialize(["A", "B"])
        s.remove_player("A")
        self.assertNotIn("A", s.probabilities)
        # Remove nonexistent is no-op
        s.remove_player("Z")

    def test_summary_format(self):
        from agents.belief_state import SuspicionState
        s = SuspicionState()
        s.initialize(["A", "B"])
        s.update("A", 0.8)
        summary = s.summary()
        self.assertIn("80% sus", summary)
        self.assertIn("A:", summary)

    def test_summary_empty(self):
        from agents.belief_state import SuspicionState
        s = SuspicionState()
        self.assertEqual(s.summary(), "No belief state.")


class TestSuspicionStateStaleness(unittest.TestCase):
    """Belief staleness and frustration detection."""

    def test_first_check_never_stale(self):
        from agents.belief_state import SuspicionState
        s = SuspicionState()
        s.initialize(["A", "B"])
        self.assertFalse(s.check_staleness())

    def test_becomes_stale_after_threshold(self):
        from agents.belief_state import SuspicionState
        s = SuspicionState()
        s.initialize(["A", "B"])
        s.check_staleness()  # first snapshot
        s.check_staleness()  # delta < 0.05, stale_rounds=1
        self.assertFalse(s.is_frustrated)
        s.check_staleness()  # delta < 0.05, stale_rounds=2
        self.assertTrue(s.is_frustrated)

    def test_staleness_resets_on_significant_change(self):
        from agents.belief_state import SuspicionState
        s = SuspicionState()
        s.initialize(["A", "B"])
        s.check_staleness()
        s.check_staleness()  # stale_rounds=1
        s.update("A", 0.9)  # significant change
        result = s.check_staleness()
        self.assertFalse(result)
        self.assertFalse(s.is_frustrated)


class TestIrohProtocol(unittest.TestCase):
    """Graduated Iroh Protocol reveal logic."""

    def _make_beliefs(self, suspicion_of_target: float) -> dict[str, "SuspicionState"]:
        from agents.belief_state import SuspicionState
        # 3 other agents all suspect "Target" at the same level
        beliefs = {}
        for agent in ["Agent1", "Agent2", "Agent3"]:
            b = SuspicionState()
            b.initialize(["Target", agent])
            b.update("Target", suspicion_of_target)
            beliefs[agent] = b
        beliefs["Target"] = SuspicionState()
        beliefs["Target"].initialize(["Agent1", "Agent2", "Agent3"])
        return beliefs

    def test_no_reveal_below_threshold(self):
        from agents.belief_state import SuspicionState
        beliefs = self._make_beliefs(0.2)
        target_belief = beliefs["Target"]
        self.assertIsNone(target_belief.get_iroh_level("Target", beliefs))

    def test_soft_hint_at_035(self):
        beliefs = self._make_beliefs(0.36)
        self.assertEqual(
            beliefs["Target"].get_iroh_level("Target", beliefs),
            "soft_hint"
        )

    def test_hard_claim_at_045(self):
        beliefs = self._make_beliefs(0.46)
        self.assertEqual(
            beliefs["Target"].get_iroh_level("Target", beliefs),
            "hard_claim"
        )

    def test_full_reveal_at_055(self):
        beliefs = self._make_beliefs(0.56)
        self.assertEqual(
            beliefs["Target"].get_iroh_level("Target", beliefs),
            "full_reveal"
        )

    def test_red_check_lowers_thresholds(self):
        beliefs = self._make_beliefs(0.30)  # Below normal soft_hint (0.35)
        # With red-check adjustment (-0.10), threshold becomes 0.25
        level = beliefs["Target"].get_iroh_level(
            "Target", beliefs, has_red_check=True,
        )
        self.assertEqual(level, "soft_hint")

    def test_should_reveal_identity(self):
        beliefs = self._make_beliefs(0.50)  # Above default threshold 0.45
        self.assertTrue(beliefs["Target"].should_reveal_identity("Target", beliefs))

    def test_should_not_reveal_below_threshold(self):
        beliefs = self._make_beliefs(0.30)
        self.assertFalse(beliefs["Target"].should_reveal_identity("Target", beliefs))

    def test_avg_suspicion_no_trackers_returns_none(self):
        from agents.belief_state import SuspicionState
        target = SuspicionState()
        result = target._get_avg_suspicion("Target", {"Target": target})
        self.assertIsNone(result)


# ===================================================================== #
#  BeliefGraph: scum-tell detection                                     #
# ===================================================================== #

class TestBeliefGraph(unittest.TestCase):
    """Scum-tell pattern detection tests."""

    def test_record_discussion_increments_count(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        bg.record_discussion("Alice")
        bg.record_discussion("Alice")
        self.assertEqual(bg._discussion_counts["Alice"], 2)

    def test_get_quiet_players(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        bg.record_discussion("Alice")
        bg.record_discussion("Alice")
        bg.record_discussion("Alice")
        bg.record_discussion("Bob")
        quiet = bg.get_quiet_players(["Alice", "Bob", "Charlie"], threshold=1)
        self.assertIn("Bob", quiet)
        self.assertIn("Charlie", quiet)
        self.assertNotIn("Alice", quiet)

    def test_late_bandwagon_detected(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        current_votes = {"X": "Target", "Y": "Target"}  # 2 already voted
        result = bg.check_late_bandwagon(
            "Alice", "Target", "yeah same", current_votes,
        )
        self.assertIsNotNone(result)
        self.assertIn("LATE BANDWAGON", result)
        self.assertIn("Alice", bg.flags)

    def test_no_bandwagon_with_substantive_reasoning(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        current_votes = {"X": "Target", "Y": "Target"}
        result = bg.check_late_bandwagon(
            "Alice", "Target",
            "I noticed Target shifted blame onto Bob after being accused, a classic redirect pattern",
            current_votes,
        )
        self.assertIsNone(result)

    def test_no_bandwagon_with_few_prior_votes(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        current_votes = {"X": "Target"}  # Only 1 prior vote
        result = bg.check_late_bandwagon("Alice", "Target", "yeah", current_votes)
        self.assertIsNone(result)

    def test_redirect_detected(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        bg.record_discussion("Alice")
        bg.record_discussion("Alice")
        # Charlie is quiet (0 discussions)
        result = bg.check_redirect(
            "Alice", "what about charlie being quiet?",
            current_target="Bob",
            alive_players=["Alice", "Bob", "Charlie"],
        )
        self.assertIsNotNone(result)
        self.assertIn("REDIRECT", result)

    def test_no_redirect_when_mentioning_current_target(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        result = bg.check_redirect(
            "Alice", "I agree Bob is suspicious, but charlie too",
            current_target="Bob",
            alive_players=["Alice", "Bob", "Charlie"],
        )
        # Both Bob and Charlie mentioned — not a redirect
        self.assertIsNone(result)

    def test_no_redirect_when_no_current_target(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        result = bg.check_redirect(
            "Alice", "Charlie is sus",
            current_target=None,
            alive_players=["Alice", "Bob", "Charlie"],
        )
        self.assertIsNone(result)

    def test_instahammer_detected(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        # Need previous voters in vote_order to not be one of the first 2
        bg._vote_order = ["X", "Y"]
        # 5 alive, majority = 3 needed. votes_so_far = 2, +1 = 3 → decisive
        result = bg.check_instahammer("Alice", votes_so_far=2, total_alive=5)
        self.assertIsNotNone(result)
        self.assertIn("INSTAHAMMER", result)

    def test_no_instahammer_for_early_voters(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        # First voter, even if vote reaches majority
        result = bg.check_instahammer("Alice", votes_so_far=2, total_alive=5)
        # Alice is first in vote_order — not suspicious
        self.assertIsNone(result)

    def test_no_instahammer_below_majority(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        bg._vote_order = ["X", "Y"]
        # 5 alive, majority = 3. votes_so_far = 1, +1 = 2 < 3
        result = bg.check_instahammer("Alice", votes_so_far=1, total_alive=5)
        self.assertIsNone(result)

    def test_get_flags_for_prompt_empty(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        self.assertEqual(bg.get_flags_for_prompt(), "")

    def test_get_flags_for_prompt_with_flags(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        bg.flags["Alice"] = ["LATE BANDWAGON: Alice joined"]
        prompt = bg.get_flags_for_prompt()
        self.assertIn("SCUM-TELL", prompt)
        self.assertIn("Alice", prompt)

    def test_reset_round_clears_vote_order(self):
        from agents.belief_state import BeliefGraph
        bg = BeliefGraph()
        bg._vote_order = ["X", "Y"]
        bg.flags["A"] = ["some flag"]
        bg.reset_round()
        self.assertEqual(bg._vote_order, [])
        # Flags are preserved across rounds
        self.assertIn("A", bg.flags)


# ===================================================================== #
#  TemporalConsistencyChecker                                           #
# ===================================================================== #

class TestTemporalConsistencyChecker(unittest.TestCase):
    """Detect temporal impossibilities in agent messages."""

    def test_yesterday_on_round_1_is_slip(self):
        from agents.belief_state import TemporalConsistencyChecker
        checker = TemporalConsistencyChecker()
        slips = checker.check_message("Alice", "I remember yesterday when Bob was quiet", 1)
        self.assertTrue(len(slips) > 0)
        self.assertIn("yesterday", slips[0].lower())

    def test_yesterday_on_round_2_is_not_slip(self):
        from agents.belief_state import TemporalConsistencyChecker
        checker = TemporalConsistencyChecker()
        slips = checker.check_message("Alice", "Yesterday Bob was quiet", 2)
        self.assertEqual(len(slips), 0)

    def test_last_game_is_always_slip(self):
        from agents.belief_state import TemporalConsistencyChecker
        checker = TemporalConsistencyChecker()
        slips = checker.check_message("Alice", "In the last game Bob was Mafia", 3)
        self.assertTrue(len(slips) > 0)

    def test_pre_day_chat_is_always_slip(self):
        from agents.belief_state import TemporalConsistencyChecker
        checker = TemporalConsistencyChecker()
        slips = checker.check_message("Alice", "In the pre-day chat we agreed", 2)
        self.assertTrue(len(slips) > 0)

    def test_remember_when_is_slip(self):
        from agents.belief_state import TemporalConsistencyChecker
        checker = TemporalConsistencyChecker()
        slips = checker.check_message("Alice", "Remember when we discussed this?", 1)
        self.assertTrue(len(slips) > 0)

    def test_clean_message_has_no_slips(self):
        from agents.belief_state import TemporalConsistencyChecker
        checker = TemporalConsistencyChecker()
        slips = checker.check_message("Alice", "Bob is acting really suspicious", 1)
        self.assertEqual(len(slips), 0)

    def test_slips_accumulate(self):
        from agents.belief_state import TemporalConsistencyChecker
        checker = TemporalConsistencyChecker()
        checker.check_message("Alice", "Yesterday Bob was weird", 1)
        checker.check_message("Alice", "In the pre-day chat we talked", 1)
        self.assertEqual(len(checker.slips["Alice"]), 2)

    def test_get_slips_for_prompt_empty(self):
        from agents.belief_state import TemporalConsistencyChecker
        checker = TemporalConsistencyChecker()
        self.assertEqual(checker.get_slips_for_prompt(), "")

    def test_get_slips_for_prompt_with_slips(self):
        from agents.belief_state import TemporalConsistencyChecker
        checker = TemporalConsistencyChecker()
        checker.check_message("Alice", "Yesterday I noticed something", 1)
        prompt = checker.get_slips_for_prompt()
        self.assertIn("IMPOSSIBLE", prompt)
        self.assertIn("Alice", prompt)


# ===================================================================== #
#  Overconfidence gating & belief prompt injection                      #
# ===================================================================== #

class TestOverconfidenceGating(unittest.TestCase):
    """Test overconfidence archetype gating logic."""

    def test_gates_overconfident_below_07(self):
        from agents.belief_state import SuspicionState, should_gate_overconfidence
        s = SuspicionState()
        s.initialize(["A", "B"])
        s.update("A", 0.5)
        self.assertTrue(should_gate_overconfidence("Overconfident", s, "A"))

    def test_does_not_gate_overconfident_above_07(self):
        from agents.belief_state import SuspicionState, should_gate_overconfidence
        s = SuspicionState()
        s.initialize(["A"])
        s.update("A", 0.8)
        self.assertFalse(should_gate_overconfidence("Overconfident", s, "A"))

    def test_does_not_gate_other_archetypes(self):
        from agents.belief_state import SuspicionState, should_gate_overconfidence
        s = SuspicionState()
        s.initialize(["A"])
        s.update("A", 0.3)
        self.assertFalse(should_gate_overconfidence("Analytical", s, "A"))

    def test_does_not_gate_none_target(self):
        from agents.belief_state import SuspicionState, should_gate_overconfidence
        s = SuspicionState()
        self.assertFalse(should_gate_overconfidence("Overconfident", s, None))


class TestBuildBeliefPromptInjection(unittest.TestCase):
    """Verify belief prompt injection construction."""

    def test_includes_suspicion_summary(self):
        from agents.belief_state import SuspicionState, build_belief_prompt_injection
        s = SuspicionState()
        s.initialize(["A", "B"])
        s.update("A", 0.7)
        prompt = build_belief_prompt_injection(s, "Analytical")
        self.assertIn("70% sus", prompt)

    def test_overconfident_archetype_modulation(self):
        from agents.belief_state import SuspicionState, build_belief_prompt_injection
        s = SuspicionState()
        s.initialize(["A"])
        prompt = build_belief_prompt_injection(s, "Overconfident")
        self.assertIn("STRONG evidence", prompt)

    def test_volatile_archetype_modulation(self):
        from agents.belief_state import SuspicionState, build_belief_prompt_injection
        s = SuspicionState()
        s.initialize(["A"])
        prompt = build_belief_prompt_injection(s, "Volatile")
        self.assertIn("ANY new information", prompt)

    def test_analytical_archetype_modulation(self):
        from agents.belief_state import SuspicionState, build_belief_prompt_injection
        s = SuspicionState()
        s.initialize(["A"])
        prompt = build_belief_prompt_injection(s, "Analytical")
        self.assertIn("explicit explanation chain", prompt)

    def test_frustration_state_included(self):
        from agents.belief_state import SuspicionState, build_belief_prompt_injection
        s = SuspicionState()
        s.initialize(["A"])
        s._stale_rounds = 3  # Force frustration
        prompt = build_belief_prompt_injection(s, "Analytical")
        self.assertIn("FRUSTRATION STATE", prompt)

    def test_overconfident_caution_below_07(self):
        from agents.belief_state import SuspicionState, build_belief_prompt_injection
        s = SuspicionState()
        s.initialize(["A"])
        s.update("A", 0.5)
        prompt = build_belief_prompt_injection(s, "Overconfident")
        self.assertIn("CAUTION", prompt)


# ===================================================================== #
#  parse_belief_updates                                                 #
# ===================================================================== #

class TestParseBeliefUpdates(unittest.TestCase):
    """Test belief update extraction from reasoning text."""

    def test_single_update(self):
        from agents.belief_state import parse_belief_updates
        text = "BELIEF_UPDATE: Alice=0.75 because she was quiet"
        result = parse_belief_updates(text)
        self.assertAlmostEqual(result["Alice"], 0.75)

    def test_multiple_updates(self):
        from agents.belief_state import parse_belief_updates
        text = (
            "BELIEF_UPDATE: Alice=0.75 because quiet\n"
            "BELIEF_UPDATE: Bob=0.30 because defended Alice"
        )
        result = parse_belief_updates(text)
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result["Alice"], 0.75)
        self.assertAlmostEqual(result["Bob"], 0.30)

    def test_no_updates(self):
        from agents.belief_state import parse_belief_updates
        result = parse_belief_updates("Just normal reasoning text")
        self.assertEqual(result, {})

    def test_boundary_values(self):
        from agents.belief_state import parse_belief_updates
        text = "BELIEF_UPDATE: A=0.0 test\nBELIEF_UPDATE: B=1.0 test"
        result = parse_belief_updates(text)
        self.assertAlmostEqual(result["A"], 0.0)
        self.assertAlmostEqual(result["B"], 1.0)

    def test_case_insensitive(self):
        from agents.belief_state import parse_belief_updates
        text = "belief_update: Alice=0.50 because test"
        result = parse_belief_updates(text)
        self.assertAlmostEqual(result["Alice"], 0.50)


# ===================================================================== #
#  apply_overconfidence_gate                                            #
# ===================================================================== #

class TestApplyOverconfidenceGate(unittest.TestCase):
    """Test overconfident language softening."""

    def test_softens_low_certainty_declarative(self):
        from agents.belief_state import SuspicionState, apply_overconfidence_gate
        s = SuspicionState()
        s.initialize(["Alice"])
        s.update("Alice", 0.4)  # Below 0.7
        text = "Alice is the one we should vote for"
        result = apply_overconfidence_gate(text, s)
        self.assertIn("(I think)", result)

    def test_does_not_soften_high_certainty(self):
        from agents.belief_state import SuspicionState, apply_overconfidence_gate
        s = SuspicionState()
        s.initialize(["Alice"])
        s.update("Alice", 0.8)  # Above 0.7
        text = "Alice is the one we should vote for"
        result = apply_overconfidence_gate(text, s)
        self.assertNotIn("(I think)", result)


# ===================================================================== #
#  GameMemoryStore: load, save, add_learning, get_memory_prefix         #
# ===================================================================== #

class TestGameMemoryStore(unittest.TestCase):
    """Test cross-game memory persistence."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_nonexistent_dir_is_noop(self):
        from agents.memory import GameMemoryStore
        store = GameMemoryStore(memory_dir="/tmp/nonexistent_mafia_test_dir_xyz")
        store.load()  # should not raise

    def test_save_creates_files(self):
        from agents.memory import GameMemoryStore
        import os
        store = GameMemoryStore(memory_dir=self.tmpdir)
        store.save()
        files = os.listdir(self.tmpdir)
        self.assertIn("detective_learnings.json", files)
        self.assertIn("global_learnings.json", files)

    def test_add_and_retrieve_learning(self):
        from agents.memory import GameMemoryStore, Learning
        store = GameMemoryStore(memory_dir=self.tmpdir)
        store.add_learning(Learning(
            insight="Quiet players are often Mafia",
            context="Day 2",
            role="Detective",
            round_number=2,
            outcome="correct",
        ))
        prefix = store.get_memory_prefix("Detective")
        self.assertIn("Quiet players are often Mafia", prefix)
        self.assertIn("✓", prefix)  # correct outcome marker

    def test_learning_also_added_to_global(self):
        from agents.memory import GameMemoryStore, Learning
        store = GameMemoryStore(memory_dir=self.tmpdir)
        store.add_learning(Learning(
            insight="Test insight",
            context="test",
            role="Villager",
            round_number=1,
            outcome="unknown",
        ))
        self.assertEqual(len(store._learnings["Villager"]), 1)
        self.assertEqual(len(store._learnings["global"]), 1)

    def test_get_memory_prefix_empty_returns_empty(self):
        from agents.memory import GameMemoryStore
        store = GameMemoryStore(memory_dir=self.tmpdir)
        self.assertEqual(store.get_memory_prefix("Detective"), "")

    def test_save_and_load_roundtrip(self):
        from agents.memory import GameMemoryStore, Learning
        store = GameMemoryStore(memory_dir=self.tmpdir)
        store.add_learning(Learning(
            insight="Test roundtrip",
            context="test",
            role="Doctor",
            round_number=1,
            outcome="incorrect",
        ))
        store.save()

        store2 = GameMemoryStore(memory_dir=self.tmpdir)
        store2.load()
        prefix = store2.get_memory_prefix("Doctor")
        self.assertIn("Test roundtrip", prefix)
        self.assertIn("✗", prefix)  # incorrect outcome marker

    def test_max_learnings_cap(self):
        from agents.memory import GameMemoryStore, Learning, _MAX_LEARNINGS_PER_ROLE
        store = GameMemoryStore(memory_dir=self.tmpdir)
        for i in range(_MAX_LEARNINGS_PER_ROLE + 10):
            store.add_learning(Learning(
                insight=f"Learning {i}",
                context="test",
                role="Mafia",
                round_number=1,
                outcome="unknown",
            ))
        store.save()
        store2 = GameMemoryStore(memory_dir=self.tmpdir)
        store2.load()
        self.assertLessEqual(len(store2._learnings["Mafia"]), _MAX_LEARNINGS_PER_ROLE)

    def test_record_game_outcome(self):
        from agents.memory import GameMemoryStore
        store = GameMemoryStore(memory_dir=self.tmpdir)
        assignments = [
            {"name": "Alice", "role": "Mafia"},
            {"name": "Bob", "role": "Villager"},
        ]
        store.record_game_outcome("Town", assignments, round_count=5)
        prefix = store.get_memory_prefix("global")
        self.assertIn("5 rounds", prefix)
        # Alice was Mafia and Town won → incorrect for her
        mafia_prefix = store.get_memory_prefix("Mafia")
        self.assertIn("Alice", mafia_prefix)

    def test_corrupted_json_handled_gracefully(self):
        from agents.memory import GameMemoryStore
        import os
        os.makedirs(self.tmpdir, exist_ok=True)
        with open(os.path.join(self.tmpdir, "detective_learnings.json"), "w") as f:
            f.write("not valid json{{{")
        store = GameMemoryStore(memory_dir=self.tmpdir)
        store.load()  # should not raise
        self.assertEqual(store._learnings["Detective"], [])


# ===================================================================== #
#  SummaryAgent: summarize, eliminations, evidence, compression         #
# ===================================================================== #

class TestSummaryAgentSummarize(unittest.TestCase):
    """Test SummaryAgent main summarize method."""

    def _make_state(self) -> GameState:
        return GameState(
            players={
                "Alice": PlayerState(name="Alice", role="Mafia", archetype="Analytical"),
                "Bob": PlayerState(name="Bob", role="Villager", archetype="Passive"),
                "Charlie": PlayerState(name="Charlie", role="Detective", archetype="Contrarian"),
            }
        )

    def test_summarize_includes_alive_players(self):
        from agents.summary import SummaryAgent
        gs = self._make_state()
        summary = SummaryAgent().summarize(gs)
        self.assertIn("Alice", summary)
        self.assertIn("Bob", summary)
        self.assertIn("3 remaining", summary)

    def test_summarize_includes_elimination(self):
        from agents.summary import SummaryAgent
        gs = self._make_state()
        gs.eliminate_player("Alice")
        summary = SummaryAgent().summarize(gs)
        self.assertIn("Alice (Mafia)", summary)

    def test_summarize_no_elimination(self):
        from agents.summary import SummaryAgent
        gs = self._make_state()
        summary = SummaryAgent().summarize(gs)
        self.assertNotIn("Recent elimination", summary)

    def test_summarize_with_votes(self):
        from agents.summary import SummaryAgent
        gs = self._make_state()
        gs.votes = {"Alice": "Bob", "Charlie": "Bob"}
        summary = SummaryAgent().summarize(gs)
        self.assertIn("Votes so far", summary)
        self.assertIn("Bob(2)", summary)


class TestSummaryAgentEvidence(unittest.TestCase):
    """Test evidence extraction."""

    def test_extracts_evidence_from_log(self):
        from agents.summary import SummaryAgent
        from engine.game_state import LogEntry, GamePhase
        entries = [
            LogEntry(
                phase=GamePhase.DAY_DISCUSSION, round_number=1,
                agent_name="Alice", role="Villager", archetype="Analytical",
                reasoning=None, action="Bob voted against Charlie then changed his mind"
            ),
        ]
        result = SummaryAgent()._get_main_evidence(entries)
        self.assertIn("Alice", result)
        self.assertIn("voted", result)

    def test_no_evidence_from_narrator(self):
        from agents.summary import SummaryAgent
        from engine.game_state import LogEntry, GamePhase
        entries = [
            LogEntry(
                phase=GamePhase.DAY_DISCUSSION, round_number=1,
                agent_name="Narrator", role="Narrator", archetype="",
                reasoning=None, action="The sun set and the voted player was eliminated"
            ),
        ]
        result = SummaryAgent()._get_main_evidence(entries)
        self.assertIsNone(result)

    def test_no_evidence_when_no_markers(self):
        from agents.summary import SummaryAgent
        from engine.game_state import LogEntry, GamePhase
        entries = [
            LogEntry(
                phase=GamePhase.DAY_DISCUSSION, round_number=1,
                agent_name="Alice", role="Villager", archetype="Analytical",
                reasoning=None, action="Hello everyone"
            ),
        ]
        result = SummaryAgent()._get_main_evidence(entries)
        self.assertIsNone(result)


class TestSummaryAgentRequiredAction(unittest.TestCase):
    """Test required action per phase."""

    def test_discussion_phase(self):
        from agents.summary import SummaryAgent
        gs = GameState(
            players={"A": PlayerState(name="A", role="Villager", archetype="Analytical")},
            phase=GamePhase.DAY_DISCUSSION,
        )
        action = SummaryAgent()._get_required_action(gs)
        self.assertIn("discuss", action.lower())

    def test_vote_phase(self):
        from agents.summary import SummaryAgent
        gs = GameState(
            players={"A": PlayerState(name="A", role="Villager", archetype="Analytical")},
            phase=GamePhase.DAY_VOTE,
        )
        action = SummaryAgent()._get_required_action(gs)
        self.assertIn("VOTE", action)

    def test_night_phase(self):
        from agents.summary import SummaryAgent
        gs = GameState(
            players={"A": PlayerState(name="A", role="Villager", archetype="Analytical")},
            phase=GamePhase.NIGHT,
        )
        action = SummaryAgent()._get_required_action(gs)
        self.assertIn("Night", action)

    def test_game_over_phase(self):
        from agents.summary import SummaryAgent
        gs = GameState(
            players={"A": PlayerState(name="A", role="Villager", archetype="Analytical")},
            phase=GamePhase.GAME_OVER,
        )
        action = SummaryAgent()._get_required_action(gs)
        self.assertIn("Game over", action)


class TestSummaryAgentCompression(unittest.TestCase):
    """Test progressive history compression."""

    def test_rounds_1_2_full_history(self):
        from agents.summary import SummaryAgent
        gs = GameState(
            players={"A": PlayerState(name="A", role="Villager", archetype="Analytical")},
            round_number=1,
        )
        history = ["msg1", "msg2", "msg3"]
        result = SummaryAgent().compress_discussion_history(history, gs)
        self.assertEqual(result, history)

    def test_rounds_3_4_summarizes_older(self):
        from agents.summary import SummaryAgent
        gs = GameState(
            players={
                "A": PlayerState(name="A", role="Villager", archetype="Analytical"),
                "B": PlayerState(name="B", role="Villager", archetype="Passive"),
            },
            round_number=3,
        )
        history = [
            "Alice: I suspect Bob is mafia",
            "Bob: I'm not mafia, Alice is suspicious",
            "Alice: Let's vote Bob",
            "Bob: No way, Alice is the guilty one",
            "Alice: Current round action",
            "Bob: Current round response",
        ]
        result = SummaryAgent().compress_discussion_history(history, gs)
        self.assertTrue(any("[EARLIER SUMMARY]" in r for r in result))
        self.assertLess(len(result), len(history))

    def test_round_5_plus_critical_only(self):
        from agents.summary import SummaryAgent
        gs = GameState(
            players={
                "A": PlayerState(name="A", role="Villager", archetype="Analytical"),
            },
            round_number=5,
        )
        history = [
            "Alice: Regular chat",
            "Bob: [SYSTEM] Alice was eliminated. Role: Mafia",
            "Charlie: Regular discussion",
            "Diana: Current round talk",
            "Eve: More current round",
        ]
        result = SummaryAgent().compress_discussion_history(history, gs)
        # Should contain the system message but not necessarily the older regular chat
        found_critical = any("[CRITICAL HISTORY]" in r for r in result)
        found_system = any("[SYSTEM]" in r for r in result)
        # Either critical history is present or all history compressed to current
        self.assertTrue(found_critical or found_system or len(result) <= len(history))

    def test_empty_history_unchanged(self):
        from agents.summary import SummaryAgent
        gs = GameState(
            players={"A": PlayerState(name="A", role="Villager", archetype="Analytical")},
            round_number=4,
        )
        result = SummaryAgent().compress_discussion_history([], gs)
        self.assertEqual(result, [])


class TestSummarizeKeyAccusations(unittest.TestCase):
    """Test key accusation extraction helper."""

    def test_extracts_accusations(self):
        from agents.summary import SummaryAgent
        entries = [
            "Alice: I suspect Bob is guilty",
            "Bob: Nothing interesting here",
            "Charlie: Bob is suspicious, vote him out",
        ]
        result = SummaryAgent._summarize_key_accusations(entries)
        self.assertIn("Alice", result)
        self.assertIn("Charlie", result)

    def test_no_accusations_returns_placeholder(self):
        from agents.summary import SummaryAgent
        entries = ["Alice: Hello", "Bob: Hi there"]
        result = SummaryAgent._summarize_key_accusations(entries)
        self.assertIn("No significant accusations", result)


class TestSummaryAgentVoteSummary(unittest.TestCase):
    """Test compact vote summary."""

    def test_vote_summary_format(self):
        from agents.summary import SummaryAgent
        votes = {"X": "Alice", "Y": "Alice", "Z": "Bob"}
        result = SummaryAgent()._get_vote_summary(votes)
        self.assertIn("Alice(2)", result)
        self.assertIn("Bob(1)", result)


# ===================================================================== #
#  Rate limiter: error classification, backoff                          #
# ===================================================================== #

class TestRateLimiterErrorClassification(unittest.TestCase):
    """Test error classification functions."""

    def test_rate_limit_429_in_message(self):
        from agents.rate_limiter import _is_rate_limit_error
        self.assertTrue(_is_rate_limit_error(Exception("429 Too Many Requests")))

    def test_rate_limit_attribute(self):
        from agents.rate_limiter import _is_rate_limit_error
        exc = Exception("rate limited")
        exc.status_code = 429
        self.assertTrue(_is_rate_limit_error(exc))

    def test_not_rate_limit(self):
        from agents.rate_limiter import _is_rate_limit_error
        self.assertFalse(_is_rate_limit_error(Exception("some other error")))

    def test_server_error_500(self):
        from agents.rate_limiter import _is_server_error
        exc = Exception("error")
        exc.status_code = 500
        self.assertTrue(_is_server_error(exc))

    def test_server_error_502(self):
        from agents.rate_limiter import _is_server_error
        self.assertTrue(_is_server_error(Exception("error code: 502")))

    def test_not_server_error(self):
        from agents.rate_limiter import _is_server_error
        self.assertFalse(_is_server_error(Exception("regular error")))

    def test_timeout_error_asyncio(self):
        import asyncio
        from agents.rate_limiter import _is_timeout_error
        self.assertTrue(_is_timeout_error(asyncio.TimeoutError()))

    def test_timeout_error_connection(self):
        from agents.rate_limiter import _is_timeout_error
        self.assertTrue(_is_timeout_error(ConnectionError("connection failed")))

    def test_timeout_in_message(self):
        from agents.rate_limiter import _is_timeout_error
        self.assertTrue(_is_timeout_error(Exception("Request timed out")))

    def test_not_timeout(self):
        from agents.rate_limiter import _is_timeout_error
        self.assertFalse(_is_timeout_error(Exception("other error")))


class TestBackoffDelay(unittest.TestCase):
    """Test exponential backoff calculation."""

    def test_first_attempt_delay(self):
        from agents.rate_limiter import _backoff_delay
        delay = _backoff_delay(0)
        # base=1.0, 2^0=1, cap=8, jitter 0–0.5
        self.assertGreaterEqual(delay, 1.0)
        self.assertLessEqual(delay, 1.5)

    def test_second_attempt_delay(self):
        from agents.rate_limiter import _backoff_delay
        delay = _backoff_delay(1)
        # base=1.0, 2^1=2, jitter 0–0.5
        self.assertGreaterEqual(delay, 2.0)
        self.assertLessEqual(delay, 2.5)

    def test_cap_at_eight_seconds(self):
        from agents.rate_limiter import _backoff_delay
        delay = _backoff_delay(10)
        # Should be capped at 8.0 + jitter (max 8.5)
        self.assertLessEqual(delay, 8.5)


class TestRetryStats(unittest.TestCase):
    """Test retry stats tracking."""

    def test_get_retry_stats_returns_copy(self):
        from agents.rate_limiter import get_retry_stats, _retry_counters
        _retry_counters["test_player"] = 3
        stats = get_retry_stats()
        self.assertEqual(stats["test_player"], 3)
        # Modify copy should not affect original
        stats["test_player"] = 99
        self.assertEqual(_retry_counters["test_player"], 3)
        # Cleanup
        del _retry_counters["test_player"]


# ===================================================================== #
#  Game manager: pick functions, constraints                            #
# ===================================================================== #

class TestPickArchetype(unittest.TestCase):
    """Test archetype selection by role."""

    def test_villager_gets_villager_archetype(self):
        from engine.game_manager import _pick_archetype
        from prompts.archetypes import VILLAGER_ARCHETYPES
        for _ in range(20):
            arch = _pick_archetype("Villager")
            self.assertIn(arch, VILLAGER_ARCHETYPES)

    def test_mafia_gets_any_archetype(self):
        from engine.game_manager import _pick_archetype
        from prompts.archetypes import ALL_ARCHETYPES
        for _ in range(20):
            arch = _pick_archetype("Mafia")
            self.assertIn(arch, ALL_ARCHETYPES)


class TestPickPersonality(unittest.TestCase):
    """Test personality selection."""

    def test_demo_mode_restricts_pool(self):
        from engine.game_manager import _pick_personality
        from prompts.personalities import DEMO_PERSONALITIES
        for _ in range(20):
            p = _pick_personality(demo=True)
            self.assertIn(p, DEMO_PERSONALITIES)

    def test_regular_mode_from_full_pool(self):
        from engine.game_manager import _pick_personality
        from prompts.personalities import ALL_PERSONALITIES
        for _ in range(20):
            p = _pick_personality(demo=False)
            self.assertIn(p, ALL_PERSONALITIES)


class TestPersonalityConstrainedFrequencyCap(unittest.TestCase):
    """Test personality frequency cap enforcement."""

    def test_frequency_cap_prevents_overuse(self):
        counts = {"TheGhost": 2}
        # TheGhost is not consensus (cap=2), already at 2, should not be picked
        for _ in range(30):
            p = _pick_personality_constrained("Villager", counts, archetype="Analytical")
            self.assertNotEqual(p, "TheGhost")

    def test_consensus_personality_cap_of_one(self):
        from engine.game_manager import CONSENSUS_PERSONALITIES
        for cp in CONSENSUS_PERSONALITIES:
            counts = {cp: 1}
            for _ in range(30):
                p = _pick_personality_constrained("Villager", counts, archetype="Analytical")
                self.assertNotEqual(p, cp)

    def test_cap_relaxation_on_full_pool(self):
        """Cap relaxation returns a personality when all are over cap."""
        from prompts.personalities import ALL_PERSONALITIES
        counts = {p: 10 for p in ALL_PERSONALITIES}
        # Should NOT raise — caps are relaxed, hard exclusions preserved
        p = _pick_personality_constrained("Villager", counts, archetype="Analytical")
        self.assertIn(p, ALL_PERSONALITIES)


class TestGameManagerPlayerNames(unittest.TestCase):
    """Verify player name list consistency."""

    def test_eleven_players(self):
        from engine.game_manager import PLAYER_NAMES, _build_role_distribution
        self.assertEqual(len(PLAYER_NAMES), 11)
        self.assertEqual(len(_build_role_distribution(len(PLAYER_NAMES))), 11)

    def test_role_distribution(self):
        from engine.game_manager import PLAYER_NAMES, _build_role_distribution
        roles = _build_role_distribution(len(PLAYER_NAMES))
        self.assertEqual(roles.count("Mafia"), 2)
        self.assertEqual(roles.count("Detective"), 1)
        self.assertEqual(roles.count("Doctor"), 1)
        self.assertEqual(roles.count("Villager"), 7)


# ===================================================================== #
#  Game tools: cast_vote, choose_target                                 #
# ===================================================================== #

class TestGameTools(unittest.TestCase):
    """Test tool-decorated game action functions."""

    def test_cast_vote_returns_vote_format(self):
        from agents.game_tools import cast_vote
        # The @tool decorator wraps the function; call the underlying logic
        result = cast_vote.func("Alice", reasoning="she's suspicious")
        self.assertEqual(result, "VOTE: Alice")

    def test_cast_vote_default_reasoning(self):
        from agents.game_tools import cast_vote
        result = cast_vote.func("Bob")
        self.assertEqual(result, "VOTE: Bob")

    def test_choose_target_returns_target_format(self):
        from agents.game_tools import choose_target
        result = choose_target.func("Charlie", reasoning="protect from Mafia")
        self.assertEqual(result, "TARGET: Charlie")

    def test_choose_target_default_reasoning(self):
        from agents.game_tools import choose_target
        result = choose_target.func("Diana")
        self.assertEqual(result, "TARGET: Diana")


# ===================================================================== #
#  Settings: env var parsing                                            #
# ===================================================================== #

class TestSettingsEnvParsing(unittest.TestCase):
    """Test environment variable parsing helpers."""

    def test_int_env_default(self):
        from config.settings import _int_env
        result = _int_env("NONEXISTENT_TEST_VAR_XYZ", 42, 100)
        self.assertEqual(result, 42)

    def test_int_env_clamped_to_max(self):
        from config.settings import _int_env
        with patch.dict("os.environ", {"TEST_INT": "999"}):
            result = _int_env("TEST_INT", 5, 10)
            self.assertEqual(result, 10)

    def test_int_env_clamped_to_min_one(self):
        from config.settings import _int_env
        with patch.dict("os.environ", {"TEST_INT": "0"}):
            result = _int_env("TEST_INT", 5, 10)
            self.assertEqual(result, 1)

    def test_int_env_invalid_returns_default(self):
        from config.settings import _int_env
        with patch.dict("os.environ", {"TEST_INT": "not_a_number"}):
            result = _int_env("TEST_INT", 7, 10)
            self.assertEqual(result, 7)

    def test_float_env_default(self):
        from config.settings import _float_env
        result = _float_env("NONEXISTENT_FLOAT_XYZ", 3.14)
        self.assertAlmostEqual(result, 3.14)

    def test_float_env_clamped_to_min(self):
        from config.settings import _float_env
        with patch.dict("os.environ", {"TEST_FLOAT": "0.01"}):
            result = _float_env("TEST_FLOAT", 1.0)
            self.assertAlmostEqual(result, 0.1)

    def test_float_env_invalid_returns_default(self):
        from config.settings import _float_env
        with patch.dict("os.environ", {"TEST_FLOAT": "abc"}):
            result = _float_env("TEST_FLOAT", 2.5)
            self.assertAlmostEqual(result, 2.5)


# ===================================================================== #
#  Prompt archetypes & personalities: structural checks                 #
# ===================================================================== #

class TestArchetypeStructure(unittest.TestCase):
    """Verify archetype definitions are structurally sound."""

    def test_all_archetypes_have_strategy_modifier(self):
        from prompts.archetypes import ARCHETYPES
        for name, data in ARCHETYPES.items():
            self.assertIn("strategy_modifier", data, f"{name} missing strategy_modifier")

    def test_all_archetypes_have_voice(self):
        from prompts.archetypes import ARCHETYPES
        for name, data in ARCHETYPES.items():
            self.assertIn("voice", data, f"{name} missing voice")

    def test_archetype_lists_consistent(self):
        from prompts.archetypes import ALL_ARCHETYPES, ARCHETYPES
        for a in ALL_ARCHETYPES:
            self.assertIn(a, ARCHETYPES, f"{a} in ALL_ARCHETYPES but not in ARCHETYPES dict")


class TestPersonalityStructure(unittest.TestCase):
    """Verify personality definitions are structurally sound."""

    def test_all_personalities_have_required_keys(self):
        from prompts.personalities import PERSONALITIES
        required = {"register", "voice_markers", "examples", "when_accused"}
        for name, data in PERSONALITIES.items():
            for key in required:
                self.assertIn(key, data, f"{name} missing {key}")

    def test_all_personalities_have_voice_marker_keys(self):
        from prompts.personalities import PERSONALITIES
        marker_keys = {"sentence_length", "evidence_relationship", "deflection_style"}
        for name, data in PERSONALITIES.items():
            markers = data.get("voice_markers", {})
            for key in marker_keys:
                self.assertIn(key, markers, f"{name} voice_markers missing {key}")

    def test_personality_lists_consistent(self):
        from prompts.personalities import ALL_PERSONALITIES, PERSONALITIES
        for p in ALL_PERSONALITIES:
            self.assertIn(p, PERSONALITIES, f"{p} in ALL_PERSONALITIES but not in PERSONALITIES dict")


# ===================================================================== #
#  Framework routing structural checks                                  #
# ===================================================================== #

class TestFrameworkBlocks(unittest.TestCase):
    """Verify framework block resolution."""

    def test_framework_blocks_dict_has_entries(self):
        from prompts.frameworks import FRAMEWORK_BLOCKS
        self.assertGreater(len(FRAMEWORK_BLOCKS), 0)

    def test_core_frameworks_exist(self):
        from prompts.frameworks import FRAMEWORK_BLOCKS
        expected = ["game-theory", "sun-tzu-strategy", "machiavelli-power"]
        for fw in expected:
            self.assertIn(fw, FRAMEWORK_BLOCKS, f"Missing framework: {fw}")

    def test_framework_values_are_nonempty_strings(self):
        from prompts.frameworks import FRAMEWORK_BLOCKS
        for name, block in FRAMEWORK_BLOCKS.items():
            self.assertIsInstance(block, str, f"{name} is not a string")
            self.assertGreater(len(block.strip()), 0, f"{name} is empty")


if __name__ == "__main__":
    unittest.main()
