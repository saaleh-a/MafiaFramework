from agents.base import run_agent_stream
from agent_framework import Agent, InMemoryHistoryProvider, SlidingWindowStrategy
from agents.middleware import ResilientSessionMiddleware, RateLimitMiddleware
from prompts.builder import build_narrator_prompt
from engine.game_state import GameState


class NarratorAgent:
    name = "Narrator"
    role = "Narrator"
    archetype = "Impartial"

    def __init__(self, client) -> None:
        self.agent = Agent(
            client=client,
            name="Narrator",
            description="Impartial omniscient game master",
            instructions=build_narrator_prompt(),
            context_providers=[InMemoryHistoryProvider("history", load_messages=True)],
            middleware=[ResilientSessionMiddleware(), RateLimitMiddleware()],
            compaction_strategy=SlidingWindowStrategy(keep_last_groups=15),
        )
        self.session = self.agent.create_session()

    async def announce(self, prompt: str, game_state: GameState) -> tuple[str, str]:
        context = (
            f"Game state (you see all roles):\n"
            f"{game_state.get_omniscient_state_summary()}\n\n"
            f"Task: {prompt}"
        )
        reasoning, action, new_session = await run_agent_stream(self.agent, context, session=self.session)
        if new_session is not None:
            self.session = new_session
        return reasoning, action
