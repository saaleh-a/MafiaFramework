from agents.base import run_agent_stream, format_discussion_prompt
from agent_framework import AgentSession
from prompts.builder import build_villager_prompt
from engine.game_state import GameState


class VillagerAgent:
    role = "Villager"

    def __init__(self, name: str, archetype: str, client) -> None:
        self.name      = name
        self.archetype = archetype
        self.session   = AgentSession()
        self.agent     = client.as_agent(
            name=name,
            description=f"Town Villager [{archetype}]",
            instructions=build_villager_prompt(name, archetype),
        )

    async def day_discussion(self, game_state: GameState, history: list[str]) -> tuple[str, str]:
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
            f"ACTION must be: VOTE: [exact name from valid targets]",
            session=self.session,
        )
