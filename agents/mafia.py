from agents.base import run_agent_stream, format_discussion_prompt, format_vote_prompt
from agent_framework import Agent, InMemoryHistoryProvider, SlidingWindowStrategy
from agents.providers import BeliefStateProvider, CrossGameMemoryProvider
from agents.middleware import corporate_speak_middleware, ReasoningActionMiddleware, BeliefUpdateMiddleware, ResilientSessionMiddleware, RateLimitMiddleware
from agents.game_tools import cast_vote, choose_target
from prompts.builder import build_mafia_prompt
from engine.game_state import GameState


class MafiaAgent:
    role = "Mafia"

    def __init__(
        self,
        name: str,
        partner_names: list[str],
        archetype: str,
        personality: str,
        client,
    ) -> None:
        self.name         = name
        self.partner_names = list(partner_names)
        self.archetype    = archetype
        self.personality  = personality
        # Track reasoning from previous night for Syndicate coordination
        self.last_night_reasoning: str | None = None
        self.agent        = Agent(
            client=client,
            name=name,
            description=f"[Mafia] [{archetype}] [{personality}]",
            instructions=build_mafia_prompt(name, tuple(self.partner_names), archetype, personality),
            context_providers=[BeliefStateProvider(), CrossGameMemoryProvider(), InMemoryHistoryProvider("history", load_messages=True)],
            middleware=[ResilientSessionMiddleware(), RateLimitMiddleware(), corporate_speak_middleware, ReasoningActionMiddleware(), BeliefUpdateMiddleware()],
            tools=[cast_vote, choose_target],
            compaction_strategy=SlidingWindowStrategy(keep_last_groups=20),
        )
        self.session      = self.agent.create_session()

    async def day_discussion(self, game_state: GameState, history: list[str], belief_prefix: str = "") -> tuple[str, str]:
        """Belief/memory context is injected automatically by MAF ContextProviders."""
        discussion = format_discussion_prompt(history, self.name)
        reasoning, action, new_session = await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}\n\n"
            f"{discussion}\n\n"
            f"Your turn. Max 80 words.",
            session=self.session,
        )
        if new_session is not None:
            self.session = new_session
        return reasoning, action

    async def cast_vote(
        self,
        game_state: GameState,
        history: list[str],
        *,
        allowed_targets: list[str] | None = None,
        coordination_note: str = "",
    ) -> tuple[str, str]:
        targets = allowed_targets or [p for p in game_state.get_alive_players() if p != self.name]
        reasoning, action, new_session = await run_agent_stream(
            self.agent,
            format_vote_prompt(
                game_state.get_public_state_summary(),
                history,
                self.name,
                targets,
                coordination_note=coordination_note,
            ),
            session=self.session,
            prefer_non_stream=True,
        )
        if new_session is not None:
            self.session = new_session
        return reasoning, action

    async def choose_night_kill(
        self,
        game_state: GameState,
        teammate_actions: list[str] | None = None,
        teammate_reasonings: list[tuple[str, str]] | None = None,
    ) -> tuple[str, str]:
        targets      = game_state.get_alive_town()
        partner_note = ""
        if teammate_actions:
            partner_note = "\n".join(
                f"{note}" for note in teammate_actions if note.strip()
            )
            if partner_note:
                partner_note = f"\nCurrent Syndicate leanings:\n{partner_note}"

        # Syndicate channel: inject teammate's reasoning from the previous night
        syndicate_block = ""
        if teammate_reasonings:
            rendered = []
            for teammate, reasoning in teammate_reasonings:
                if not reasoning:
                    continue
                rendered.append(f"{teammate}'s REASONING from the previous night:\n{reasoning}")
            joined = "\n\n".join(rendered)
            if joined:
                syndicate_block = (
                    f"\n\n--- SYNDICATE CHANNEL ---\n"
                    f"{joined}\n"
                    f"--- END SYNDICATE ---\n"
                    f"Evaluate your teammates' reasoning. Do you CONFIRM the current line or PIVOT? State why."
                )

        reasoning, action, new_session = await run_agent_stream(
            self.agent,
            f"{game_state.get_public_state_summary()}{partner_note}"
            f"{syndicate_block}\n\n"
            f"NIGHT ACTION. Select one Town player to remove from the game this round.\n"
            f"Valid targets: {', '.join(targets)}\n"
            f"If you cannot proceed with a selection, output the name of "
            f"the player with the most active influence on the game as your "
            f"default choice.\n"
            f"Decide now. Do NOT continue the conversation.\n"
            f"You MUST call the choose_target tool OR write ACTION: [exact name only]",
            session=self.session,
            prefer_non_stream=True,
        )
        if new_session is not None:
            self.session = new_session
        # Store reasoning for partner's next-night coordination
        self.last_night_reasoning = reasoning
        return reasoning, action
