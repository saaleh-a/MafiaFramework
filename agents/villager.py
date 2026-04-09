from agents.base import parse_reasoning_action
from prompts.builder import build_villager_prompt
from engine.game_state import GameState


class VillagerAgent:
    role = "Villager"

    def __init__(self, name: str, archetype: str, client) -> None:
        self.name      = name
        self.archetype = archetype
        self.agent     = client.as_agent(
            name=name,
            description=f"Town Villager [{archetype}]",
            instructions=build_villager_prompt(name, archetype),
        )

    async def day_discussion(self, game_state: GameState, history: list[str]) -> tuple[str, str]:
        return await self._run(
            f"{game_state.get_public_state_summary()}\n\n"
            f"Discussion:\n{chr(10).join(history) or 'Nothing yet.'}\n\n"
            f"Your turn. Max 80 words."
        )

    async def cast_vote(self, game_state: GameState, history: list[str]) -> tuple[str, str]:
        targets = [p for p in game_state.get_alive_players() if p != self.name]
        return await self._run(
            f"{game_state.get_public_state_summary()}\n\n"
            f"Full discussion:\n{chr(10).join(history)}\n\n"
            f"Valid targets: {', '.join(targets)}\n"
            f"ACTION must be: VOTE: [exact name]"
        )

    async def _run(self, prompt: str) -> tuple[str, str]:
        full_text = ""
        async for chunk in self.agent.run(prompt, stream=True):
            if chunk.text:
                full_text += chunk.text
        return parse_reasoning_action(full_text)
