from agents.base import run_agent_stream, format_discussion_prompt
from agent_framework import Agent, SlidingWindowStrategy
from agents.providers import BeliefStateProvider, CrossGameMemoryProvider
from agents.middleware import corporate_speak_middleware
from agents.game_tools import cast_vote, choose_target
from prompts.builder import build_detective_prompt
from engine.game_state import GameState


class DetectiveAgent:
    role = "Detective"

    def __init__(self, name: str, archetype: str, personality: str, client) -> None:
        self.name        = name
        self.archetype   = archetype
        self.personality  = personality
        self.findings: dict[str, str] = {}
        self.agent       = Agent(
            client=client,
            name=name,
            description=f"[Detective] [{archetype}] [{personality}]",
            instructions=build_detective_prompt(name, archetype, personality),
            context_providers=[BeliefStateProvider(), CrossGameMemoryProvider()],
            middleware=[corporate_speak_middleware],
            tools=[cast_vote, choose_target],
            compaction_strategy=SlidingWindowStrategy(keep_last_groups=20),
        )
        self.session     = self.agent.create_session()

    def record_finding(self, target: str, result: str) -> None:
        self.findings[target] = result

    async def day_discussion(self, game_state: GameState, history: list[str], belief_prefix: str = "") -> tuple[str, str]:
        """Belief/memory context is injected automatically by MAF ContextProviders."""
        findings_text = "\n".join(f"  {k}: {v}" for k, v in self.findings.items()) or "  Nothing yet."
        discussion = format_discussion_prompt(history, self.name)
        return await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}\n\n"
            f"Your private investigation log:\n{findings_text}\n\n"
            f"{discussion}\n\n"
            f"Your turn. Max 80 words.",
            session=self.session,
        )

    async def cast_vote(self, game_state: GameState, history: list[str]) -> tuple[str, str]:
        targets       = [p for p in game_state.get_alive_players() if p != self.name]
        findings_text = "\n".join(f"  {k}: {v}" for k, v in self.findings.items()) or "  None."
        return await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}\n\n"
            f"Your findings:\n{findings_text}\n\n"
            f"Full discussion:\n{chr(10).join(history)}\n\n"
            f"You are {self.name}. You CANNOT vote for yourself.\n"
            f"Valid targets: {', '.join(targets)}\n"
            f"You MUST call the cast_vote tool OR write ACTION: VOTE: [exact name from valid targets]",
            session=self.session,
        )

    async def choose_investigation_target(self, game_state: GameState) -> tuple[str, str]:
        alive     = [p for p in game_state.get_alive_players() if p != self.name]
        unchecked = [p for p in alive if p not in self.findings]
        return await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}\n\n"
            f"Already investigated: {list(self.findings.keys()) or 'Nobody.'}\n"
            f"Findings: {self.findings}\n"
            f"Unchecked: {unchecked}\n\n"
            f"NIGHT. Choose one player to investigate.\n"
            f"Valid targets: {', '.join(alive)}\n"
            f"You MUST call the choose_target tool OR write ACTION: [exact name only]",
            session=self.session,
        )
