from agents.base import run_agent_stream, format_discussion_prompt
from agent_framework import AgentSession
from prompts.builder import build_doctor_prompt
from engine.game_state import GameState


class DoctorAgent:
    role = "Doctor"

    def __init__(self, name: str, archetype: str, personality: str, client) -> None:
        self.name           = name
        self.archetype      = archetype
        self.personality    = personality
        self.last_protected: str | None = None
        self.session        = AgentSession()
        self.agent          = client.as_agent(
            name=name,
            description=f"[Doctor] [{archetype}] [{personality}]",
            instructions=build_doctor_prompt(name, archetype, personality),
        )

    async def day_discussion(self, game_state: GameState, history: list[str], belief_prefix: str = "") -> tuple[str, str]:
        discussion = format_discussion_prompt(history, self.name)
        return await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}\n\n"
            f"{belief_prefix}"
            f"{discussion}\n\n"
            f"Your turn. Max 80 words. Stay inconspicuous.",
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

    async def choose_protection_target(self, game_state: GameState) -> tuple[str, str]:
        alive = game_state.get_alive_players()
        valid = [p for p in alive if p != self.last_protected]
        reasoning, action = await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}\n\n"
            f"You protected {self.last_protected or 'nobody'} last night - cannot repeat.\n\n"
            f"NIGHT. Choose one player to protect.\n"
            f"Valid targets: {', '.join(valid)}\n"
            f"ACTION must be: [exact name only]",
            session=self.session,
        )
        return reasoning, action
