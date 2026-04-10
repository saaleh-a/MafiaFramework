from agents.base import run_agent_stream, format_discussion_prompt
from agent_framework import Agent, InMemoryHistoryProvider, SlidingWindowStrategy
from agents.providers import BeliefStateProvider, CrossGameMemoryProvider
from agents.middleware import corporate_speak_middleware, ReasoningActionMiddleware, BeliefUpdateMiddleware
from agents.game_tools import cast_vote
from prompts.builder import build_villager_prompt
from engine.game_state import GameState


class VillagerAgent:
    role = "Villager"

    def __init__(self, name: str, archetype: str, personality: str, client) -> None:
        self.name        = name
        self.archetype   = archetype
        self.personality  = personality
        self.agent       = Agent(
            client=client,
            name=name,
            description=f"[Villager] [{archetype}] [{personality}]",
            instructions=build_villager_prompt(name, archetype, personality),
            context_providers=[BeliefStateProvider(), CrossGameMemoryProvider(), InMemoryHistoryProvider("history", load_messages=True)],
            middleware=[corporate_speak_middleware, ReasoningActionMiddleware(), BeliefUpdateMiddleware()],
            tools=[cast_vote],
            compaction_strategy=SlidingWindowStrategy(keep_last_groups=20),
        )
        self.session     = self.agent.create_session()

    async def day_discussion(self, game_state: GameState, history: list[str], belief_prefix: str = "") -> tuple[str, str]:
        """Belief/memory context is injected automatically by MAF ContextProviders."""
        discussion = format_discussion_prompt(history, self.name)
        return await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}\n\n"
            f"{discussion}\n\n"
            f"Your turn. Max 80 words.",
            session=self.session,
        )

    async def cast_vote(self, game_state: GameState, history: list[str]) -> tuple[str, str]:
        targets = [p for p in game_state.get_alive_players() if p != self.name]
        return await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}\n\n"
            f"Full discussion:\n{chr(10).join(history)}\n\n"
            f"You are {self.name}. You CANNOT vote for yourself.\n"
            f"Valid targets: {', '.join(targets)}\n"
            f"You MUST call the cast_vote tool OR write ACTION: VOTE: [exact name from valid targets]",
            session=self.session,
        )
