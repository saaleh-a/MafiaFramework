from agents.base import parse_reasoning_action
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
        full_text = ""
        async for chunk in self.agent.run(context, stream=True):
            if chunk.text:
                full_text += chunk.text
        return parse_reasoning_action(full_text)
