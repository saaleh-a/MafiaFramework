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
    Iroh Protocol reveal instructions into each agent call.

    State keys used in session.state["belief"]:
        suspicion:      SuspicionState for this agent
        archetype:      str archetype name
        graph:          BeliefGraph (shared)
        temporal:       TemporalConsistencyChecker (shared)
        all_beliefs:    dict[str, SuspicionState] (for Iroh Protocol)
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

        # Iroh Protocol: identity reveal
        role: str = state.get("role", "")
        name: str = state.get("name", "")
        all_beliefs: dict[str, SuspicionState] | None = state.get("all_beliefs")
        if role in ("Detective", "Doctor") and all_beliefs:
            if suspicion.should_reveal_identity(name, all_beliefs):
                context.extend_instructions(
                    self.source_id,
                    f"⚠ REVEAL_IDENTITY: The group suspects you ({name}) "
                    f"above the self-preservation threshold. You MUST reveal "
                    f"your role as {role} in your next ACTION to survive. "
                    f"Dying with your role hidden helps nobody.",
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
