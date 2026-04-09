from agents.base import run_agent_stream
from prompts.builder import build_narrator_prompt
from engine.game_state import GameState


class NarratorAgent:
    name = "Narrator"
    role = "Narrator"
    archetype = "Impartial"

    def __init__(self, client) -> None:
        self.agent = client.as_agent(
            name="Narrator",
            description="Impartial omniscient game master",
            instructions=build_narrator_prompt(),
        )

    async def announce(self, prompt: str, game_state: GameState) -> tuple[str, str]:
        context = (
            f"Game state (you see all roles):\n"
            f"{game_state.get_omniscient_state_summary()}\n\n"
            f"Task: {prompt}"
        )
        return await run_agent_stream(self.agent, context)
