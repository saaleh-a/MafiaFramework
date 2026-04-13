"""
agents/providers.py
--------------------
MAF ContextProviders — inject dynamic context into each agent call
using the framework's native ContextProvider API.

These replace the manual belief_prefix string-building in the orchestrator.
Each provider uses session.state to persist data across turns.

ContextProviders are the MAF-idiomatic way to give agents dynamic,
per-turn context without concatenating giant prompt strings manually.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from agent_framework import AgentSession, ContextProvider, SessionContext

if TYPE_CHECKING:
    from agent_framework import SupportsAgentRun
    from agents.belief_state import SuspicionState, BeliefGraph, TemporalConsistencyChecker
    from agents.memory import GameMemoryStore


class BeliefStateProvider(ContextProvider):
    """
    Injects the agent's current suspicion state, frustration warnings,
    overconfidence gates, scum-tell flags, temporal slip alerts, and
    Last Stand Protocol reveal instructions into each agent call.

    State keys used in session.state["belief"]:
        suspicion:      SuspicionState for this agent
        archetype:      str archetype name
        graph:          BeliefGraph (shared)
        temporal:       TemporalConsistencyChecker (shared)
        all_beliefs:    dict[str, SuspicionState] (for Last Stand Protocol)
        role:           str role name
        name:           str player name
        phase_value:    str phase label
        vote_shortlist: list[str] current coordination shortlist
        recommended_vote: str recommended target for this voter
        evasion_scores: dict[str, int] current room evasion scores
    """

    DEFAULT_SOURCE_ID = "belief"

    def __init__(self) -> None:
        super().__init__(self.DEFAULT_SOURCE_ID)

    async def before_run(
        self,
        *,
        agent: SupportsAgentRun,
        session: AgentSession | None,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        from agents.belief_state import build_belief_prompt_injection

        suspicion: SuspicionState | None = state.get("suspicion")
        if not suspicion:
            return

        archetype: str = state.get("archetype", "")

        # Check staleness (updates frustration flag)
        suspicion.check_staleness()

        # Core belief injection
        belief_text = build_belief_prompt_injection(suspicion, archetype)
        context.extend_instructions(self.source_id, belief_text)

        context.extend_instructions(
            self.source_id,
            "ARCHETYPE OVERRIDE: your archetype controls tone and bias, not the win condition. "
            "If the board offers a clearer solve or survival move, take it.",
        )

        # Vote format reinforcement: if previous vote was unparseable,
        # inject a stronger format requirement
        vote_failures: int = state.get("vote_parse_failures", 0)
        if vote_failures > 0:
            context.extend_instructions(
                self.source_id,
                "⚠ VOTE FORMAT REMINDER: Your previous vote could not be parsed. "
                "Prefer the cast_vote tool. If you do not use the tool, your FINAL line "
                "MUST be exactly 'ACTION: VOTE: [name]'. Use one exact name from the "
                "valid targets list only. No questions, no punctuation, no extra words. "
                "Example: 'ACTION: VOTE: Alice'",
            )

        # Scum-tell flags from BeliefGraph
        graph: BeliefGraph | None = state.get("graph")
        if graph:
            flags = graph.get_flags_for_prompt()
            if flags:
                context.extend_instructions(self.source_id, flags)

        # Temporal slip warnings
        temporal: TemporalConsistencyChecker | None = state.get("temporal")
        if temporal:
            slips = temporal.get_slips_for_prompt()
            if slips:
                context.extend_instructions(self.source_id, slips)

        # Last Stand Protocol: graduated identity reveal
        role: str = state.get("role", "")
        name: str = state.get("name", "")
        all_beliefs: dict[str, SuspicionState] | None = state.get("all_beliefs")
        if all_beliefs and role != "Narrator":
            avg_suspicion = suspicion._get_avg_suspicion(name, all_beliefs)
            if avg_suspicion is not None and avg_suspicion >= 0.40:
                context.extend_instructions(
                    self.source_id,
                    "SURVIVAL OVERRIDE: you are under real pressure. Drop the pose if needed. "
                    "Use exact names, exact moves, and the strongest current case.",
                )

        phase_value: str = state.get("phase_value", "")
        vote_shortlist: list[str] = list(state.get("vote_shortlist", []) or [])
        recommended_vote: str = state.get("recommended_vote", "")
        evasion_scores: dict[str, int] = dict(state.get("evasion_scores", {}) or {})
        detective_vote_weight: int = int(state.get("detective_vote_weight", 1) or 1)
        if phase_value == "DAY VOTE" and vote_shortlist:
            evasion_text = ", ".join(
                f"{player}:{score}" for player, score in sorted(
                    evasion_scores.items(), key=lambda item: (-item[1], item[0]),
                ) if score > 0
            ) or "none"
            vote_note = (
                "VOTE COORDINATION:\n"
                f"Current shortlist: {', '.join(vote_shortlist)}.\n"
                f"Recommended vote from your current belief state: {recommended_vote or 'none'}.\n"
                f"Current evasion scores: {evasion_text}.\n"
                "Vote from the shortlist unless you have a real override.\n"
                "If you override, justify it explicitly with 'OVERRIDE:' in REASONING."
            )
            if role == "Detective":
                vote_note += (
                    f"\nYour vote weight is {detective_vote_weight}. If you have a real result, "
                    "use that weight to force consolidation."
                )
            context.extend_instructions(self.source_id, vote_note)

        if role in ("Detective", "Doctor") and all_beliefs:
            # Check if Detective has a red check (confirmed Mafia finding)
            has_red_check = False
            if role == "Detective":
                # The detective agent stores findings; check if any are Mafia
                # We access this through the session state or agent attribute
                findings = state.get("findings", {})
                has_red_check = any(v == "Mafia" for v in findings.values())

            last_stand_level = suspicion.get_last_stand_level(
                name, all_beliefs, has_red_check=has_red_check,
            )
            if last_stand_level == "soft_hint":
                context.extend_instructions(
                    self.source_id,
                    f"⚠ LAST STAND PROTOCOL (SOFT HINT): You ({name}) are attracting "
                    f"moderate suspicion. Drop a hint that you have information "
                    f"that would change the current vote. Say something like "
                    f"'I have information that would change this vote' without "
                    f"revealing your role yet. This signals value to the Town.",
                )
            elif last_stand_level == "hard_claim":
                context.extend_instructions(
                    self.source_id,
                    f"⚠ LAST STAND PROTOCOL (HARD CLAIM): Suspicion against you "
                    f"({name}) is rising dangerously. You SHOULD claim your "
                    f"role as {role} conditionally: 'I am {role}. I will "
                    f"reveal what I know if the vote is not redirected.' "
                    f"This forces the Town to reconsider.",
                )
            elif last_stand_level == "full_reveal":
                context.extend_instructions(
                    self.source_id,
                    f"⚠ LAST STAND PROTOCOL (FULL REVEAL): You ({name}) are about "
                    f"to be eliminated. You MUST reveal your role as {role} "
                    f"immediately with ALL information you have. Dying with "
                    f"your role hidden helps nobody. Reveal NOW.",
                )


class CrossGameMemoryProvider(ContextProvider):
    """
    Injects cross-game learnings from GameMemoryStore into agent context.

    State keys used in session.state["memory"]:
        store:  GameMemoryStore instance
        role:   str role name (Detective, Mafia, etc.)
    """

    DEFAULT_SOURCE_ID = "memory"

    def __init__(self) -> None:
        super().__init__(self.DEFAULT_SOURCE_ID)

    async def before_run(
        self,
        *,
        agent: SupportsAgentRun,
        session: AgentSession | None,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        """Inject cross-game learnings relevant to this agent's role."""
        store: GameMemoryStore | None = state.get("store")
        role: str = state.get("role", "")
        if not store or not role:
            return

        prefix = store.get_memory_prefix(role)
        if prefix:
            context.extend_instructions(self.source_id, prefix)
