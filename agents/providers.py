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
    Self-Preservation Protocol reveal instructions into each agent call.

    State keys used in session.state["belief"]:
        suspicion:      SuspicionState for this agent
        archetype:      str archetype name
        graph:          BeliefGraph (shared)
        temporal:       TemporalConsistencyChecker (shared)
        all_beliefs:    dict[str, SuspicionState] (for Self-Preservation Protocol)
        role:           str role name
        name:           str player name
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

        # Vote format reinforcement: if previous vote was unparseable,
        # inject a stronger format requirement
        vote_failures: int = state.get("vote_parse_failures", 0)
        if vote_failures > 0:
            context.extend_instructions(
                self.source_id,
                "⚠ VOTE FORMAT REMINDER: Your previous vote could not be parsed. "
                "Your response MUST contain 'VOTE: [name]' on its own line. "
                "Use the exact name from the valid targets list. "
                "Example: 'VOTE: Alice'",
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

        # Self-Preservation Protocol: graduated identity reveal
        role: str = state.get("role", "")
        name: str = state.get("name", "")
        all_beliefs: dict[str, SuspicionState] | None = state.get("all_beliefs")
        if role in ("Detective", "Doctor") and all_beliefs:
            # Check if Detective has a red check (confirmed Mafia finding)
            has_red_check = False
            if role == "Detective":
                # The detective agent stores findings; check if any are Mafia
                # We access this through the session state or agent attribute
                findings = state.get("findings", {})
                has_red_check = any(v == "Mafia" for v in findings.values())

            spp_level = suspicion.get_spp_level(
                name, all_beliefs, has_red_check=has_red_check,
            )
            if spp_level == "soft_hint":
                context.extend_instructions(
                    self.source_id,
                    f"⚠ SELF-PRESERVATION PROTOCOL (SOFT HINT): You ({name}) are attracting "
                    f"moderate suspicion. Drop a hint that you have information "
                    f"that would change the current vote. Say something like "
                    f"'I have information that would change this vote' without "
                    f"revealing your role yet. This signals value to the Town.",
                )
            elif spp_level == "hard_claim":
                context.extend_instructions(
                    self.source_id,
                    f"⚠ SELF-PRESERVATION PROTOCOL (HARD CLAIM): Suspicion against you "
                    f"({name}) is rising dangerously. You SHOULD claim your "
                    f"role as {role} conditionally: 'I am {role}. I will "
                    f"reveal what I know if the vote is not redirected.' "
                    f"This forces the Town to reconsider.",
                )
            elif spp_level == "full_reveal":
                context.extend_instructions(
                    self.source_id,
                    f"⚠ SELF-PRESERVATION PROTOCOL (FULL REVEAL): You ({name}) are about "
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
