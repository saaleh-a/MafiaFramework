from agents.base import run_agent_stream, format_discussion_prompt
from agent_framework import Agent, SlidingWindowStrategy
from agents.providers import BeliefStateProvider, CrossGameMemoryProvider
from agents.middleware import corporate_speak_middleware
from agents.game_tools import cast_vote, choose_target
from prompts.builder import build_mafia_prompt
from engine.game_state import GameState


class MafiaAgent:
    role = "Mafia"

    def __init__(self, name: str, partner_name: str, archetype: str, personality: str, client) -> None:
        self.name         = name
        self.partner_name = partner_name
        self.archetype    = archetype
        self.personality  = personality
        # Track reasoning from previous night for Syndicate coordination
        self.last_night_reasoning: str | None = None
        self.agent        = Agent(
            client=client,
            name=name,
            description=f"[Mafia] [{archetype}] [{personality}]",
            instructions=build_mafia_prompt(name, partner_name, archetype, personality),
            context_providers=[BeliefStateProvider(), CrossGameMemoryProvider()],
            middleware=[corporate_speak_middleware],
            tools=[cast_vote, choose_target],
            compaction_strategy=SlidingWindowStrategy(keep_last_groups=20),
        )
        self.session      = self.agent.create_session()

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

    async def choose_night_kill(
        self,
        game_state: GameState,
        partner_action: str | None = None,
        partner_reasoning: str | None = None,
    ) -> tuple[str, str]:
        targets      = game_state.get_alive_town()
        partner_note = f"\n{self.partner_name} is leaning toward: {partner_action}" if partner_action else ""

        # Syndicate channel: inject teammate's reasoning from the previous night
        syndicate_block = ""
        if partner_reasoning:
            syndicate_block = (
                f"\n\n--- SYNDICATE CHANNEL ---\n"
                f"{self.partner_name}'s REASONING from last night:\n"
                f"{partner_reasoning}\n"
                f"--- END SYNDICATE ---\n"
                f"Evaluate their reasoning. Do you CONFIRM or PIVOT? State why."
            )

        reasoning, action = await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}{partner_note}"
            f"{syndicate_block}\n\n"
            f"NIGHT. Choose kill target.\n"
            f"Valid targets: {', '.join(targets)}\n"
            f"You MUST call the choose_target tool OR write ACTION: [exact name only]",
            session=self.session,
        )
        # Store reasoning for partner's next-night coordination
        self.last_night_reasoning = reasoning
        return reasoning, action
