import unittest
from types import SimpleNamespace


class TestWeightedVotes(unittest.TestCase):
    def test_detective_vote_weight_changes_tally(self):
        from engine.game_state import GameState, PlayerState

        gs = GameState(
            players={
                "Dana": PlayerState("Dana", "Detective", "Analytical"),
                "Bob": PlayerState("Bob", "Villager", "Passive"),
                "Charlie": PlayerState("Charlie", "Villager", "Passive"),
            }
        )
        gs.votes = {
            "Dana": "Charlie",
            "Bob": "Charlie",
            "Charlie": "Bob",
        }

        counts = gs.get_weighted_vote_counts()

        self.assertEqual(counts["Charlie"], 3.0)
        self.assertEqual(counts["Bob"], 1.0)
        self.assertEqual(gs.tally_votes(), "Charlie")


class TestRoleDistribution(unittest.TestCase):
    def test_eleven_players_use_three_mafia(self):
        from engine.game_manager import _build_role_distribution, _recommended_mafia_count

        self.assertEqual(_recommended_mafia_count(11), 3)
        roles = _build_role_distribution(11)

        self.assertEqual(roles.count("Mafia"), 3)
        self.assertEqual(roles.count("Detective"), 1)
        self.assertEqual(roles.count("Doctor"), 1)

    def test_personality_picker_relaxes_frequency_cap_before_crashing(self):
        from engine.game_manager import _pick_personality_constrained

        personality = _pick_personality_constrained(
            role="Doctor",
            current_counts={
                "TheGhost": 2,
                "TheAnalyst": 2,
                "TheMartyr": 2,
                "TheConfessor": 1,
            },
            demo=True,
            archetype="Volatile",
        )

        self.assertIn(
            personality,
            {"TheGhost", "TheAnalyst", "TheMartyr", "TheConfessor"},
        )


class TestStreamingDedupe(unittest.TestCase):
    def test_repeated_passage_collapses_to_single_copy(self):
        from agents.base import _collapse_repeated_passage

        text = (
            "Ivy said Jack is ducking the point and needs to answer plainly. "
            "Ivy said Jack is ducking the point and needs to answer plainly."
        )

        collapsed = _collapse_repeated_passage(text)

        self.assertEqual(
            collapsed,
            "Ivy said Jack is ducking the point and needs to answer plainly.",
        )


class TestBeliefGraphEvasion(unittest.TestCase):
    def test_direct_question_increments_evasion_score(self):
        from agents.belief_state import BeliefGraph

        graph = BeliefGraph()
        history = [
            "Bob: Alice, give one name and say the exact move you think was fake.",
        ]

        flag = graph.check_evasion(
            "Alice",
            "What about Bob then? Why are we not talking about Charlie?",
            history,
            ["Alice", "Bob", "Charlie"],
        )

        self.assertIsNotNone(flag)
        self.assertEqual(graph.evasion_scores["Alice"], 1)
        self.assertIn("EVASION", flag)


class TestVoteGuidanceLogic(unittest.TestCase):
    def _make_orchestrator(self):
        from agents.belief_state import BeliefGraph, SuspicionState
        from engine.game_state import GameState, PlayerState
        from engine.orchestrator import MafiaGameOrchestrator

        orchestrator = MafiaGameOrchestrator.__new__(MafiaGameOrchestrator)
        orchestrator.gs = GameState(
            players={
                "Alice": PlayerState("Alice", "Villager", "Analytical"),
                "Bob": PlayerState("Bob", "Villager", "Methodical"),
                "Charlie": PlayerState("Charlie", "Mafia", "Manipulative"),
                "Dana": PlayerState("Dana", "Detective", "Analytical"),
            }
        )
        orchestrator._belief_graph = BeliefGraph()
        orchestrator._belief_graph.evasion_scores = {"Charlie": 1}
        orchestrator.mafia = [SimpleNamespace(name="Charlie")]
        orchestrator.detective = SimpleNamespace(
            name="Dana",
            findings={"Charlie": "Mafia"},
        )
        orchestrator._current_vote_shortlist = []
        orchestrator._current_vote_recommendations = {}
        orchestrator._beliefs = {}

        belief_map = {
            "Alice": {"Bob": 0.28, "Charlie": 0.42, "Dana": 0.10},
            "Bob": {"Alice": 0.20, "Charlie": 0.44, "Dana": 0.18},
            "Charlie": {"Alice": 0.45, "Bob": 0.40, "Dana": 0.35},
            "Dana": {"Alice": 0.08, "Bob": 0.12, "Charlie": 0.95},
        }
        for name, scores in belief_map.items():
            state = SuspicionState()
            state.probabilities = dict(scores)
            orchestrator._beliefs[name] = state

        return orchestrator

    def test_detective_red_check_pushes_shortlist(self):
        orchestrator = self._make_orchestrator()

        shortlist = orchestrator._build_vote_shortlist(
            orchestrator.gs.get_alive_players(),
            allowed_targets=["Alice", "Bob", "Charlie"],
        )

        self.assertEqual(shortlist[0], "Charlie")

    def test_high_confidence_vote_override_forces_belief_alignment(self):
        orchestrator = self._make_orchestrator()

        target, warning = orchestrator._resolve_vote_target(
            "Alice",
            parsed_target="Bob",
            reasoning="VOTE_DECISION: target=Bob basis=belief",
            action="ACTION: VOTE: Bob",
            allowed_targets=["Bob", "Charlie"],
            recommended_target="Charlie",
            confidence=0.82,
        )

        self.assertEqual(target, "Charlie")
        self.assertIn("forced to Charlie", warning)


if __name__ == "__main__":
    unittest.main()
