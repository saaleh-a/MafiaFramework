from agents.base import run_agent_stream
from prompts.builder import build_mafia_prompt
from engine.game_state import GameState


class MafiaAgent:
    role = "Mafia"

    def __init__(self, name: str, partner_name: str, archetype: str, client) -> None:
        self.name         = name
        self.partner_name = partner_name
        self.archetype    = archetype
        self.agent        = client.as_agent(
            name=name,
            description=f"Mafia player [{archetype}]",
            instructions=build_mafia_prompt(name, partner_name, archetype),
        )

    async def day_discussion(self, game_state: GameState, history: list[str]) -> tuple[str, str]:
        return await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}\n\n"
            f"Discussion so far:\n{chr(10).join(history) or 'Nothing yet.'}\n\n"
            f"Your turn. Max 80 words."
        )

    async def cast_vote(self, game_state: GameState, history: list[str]) -> tuple[str, str]:
        targets = [p for p in game_state.get_alive_players() if p != self.name]
        return await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}\n\n"
            f"Full discussion:\n{chr(10).join(history)}\n\n"
            f"You are {self.name}. You CANNOT vote for yourself.\n"
            f"Valid targets: {', '.join(targets)}\n"
            f"ACTION must be: VOTE: [exact name from valid targets]"
        )

    async def choose_night_kill(self, game_state: GameState, partner_action: str | None = None) -> tuple[str, str]:
        targets      = game_state.get_alive_town()
        partner_note = f"\n{self.partner_name} is leaning toward: {partner_action}" if partner_action else ""
        return await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}{partner_note}\n\n"
            f"NIGHT. Choose kill target.\n"
            f"Valid targets: {', '.join(targets)}\n"
            f"ACTION must be: [exact name only]"
        )
