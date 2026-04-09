from agents.base import run_agent_stream
from prompts.builder import build_detective_prompt
from engine.game_state import GameState


class DetectiveAgent:
    role = "Detective"

    def __init__(self, name: str, archetype: str, client) -> None:
        self.name     = name
        self.archetype = archetype
        self.findings: dict[str, str] = {}
        self.agent    = client.as_agent(
            name=name,
            description=f"Town Detective [{archetype}]",
            instructions=build_detective_prompt(name, archetype),
        )

    def record_finding(self, target: str, result: str) -> None:
        self.findings[target] = result

    async def day_discussion(self, game_state: GameState, history: list[str]) -> tuple[str, str]:
        findings_text = "\n".join(f"  {k}: {v}" for k, v in self.findings.items()) or "  Nothing yet."
        return await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}\n\n"
            f"Your private investigation log:\n{findings_text}\n\n"
            f"Discussion:\n{chr(10).join(history) or 'Nothing yet.'}\n\n"
            f"Your turn. Max 80 words."
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
            f"ACTION must be: VOTE: [exact name from valid targets]"
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
            f"ACTION must be: [exact name only]"
        )
