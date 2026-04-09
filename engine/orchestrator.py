"""
engine/orchestrator.py - v3
-----------------------------
Game loop. Passes archetype through to all display and logging calls.
Everything else structurally identical to v2.
"""

import random
import sys
from engine.game_state import GameState, GamePhase
from engine.game_log import (
    print_phase_header, print_agent_action,
    print_vote_tally, print_night_result, print_game_over,
)
from agents.narrator  import NarratorAgent
from agents.mafia     import MafiaAgent
from agents.detective import DetectiveAgent
from agents.doctor    import DoctorAgent
from agents.villager  import VillagerAgent


class MafiaGameOrchestrator:
    def __init__(
        self,
        game_state:   GameState,
        narrator:     NarratorAgent,
        mafia_agents: list[MafiaAgent],
        detective:    DetectiveAgent,
        doctor:       DoctorAgent,
        villagers:    list[VillagerAgent],
        debug:        bool = False,
        quiet:        bool = False,
    ) -> None:
        self.gs        = game_state
        self.narrator  = narrator
        self.mafia     = mafia_agents
        self.detective = detective
        self.doctor    = doctor
        self.villagers = villagers
        self.debug     = debug
        self.quiet     = quiet
        self._agents: dict[str, any] = {}
        for a in mafia_agents + [detective, doctor] + villagers:
            self._agents[a.name] = a

    async def run_game(self) -> str:
        print_phase_header("GAME START", 0)
        await self._narrate("Announce the start of the Mafia game. Set the scene.")

        while self.gs.check_win_condition() is None:
            await self._run_day_phase()
            if self.gs.check_win_condition():
                break
            await self._run_night_phase()
            self.gs.round_number += 1
            self.gs.reset_round_state()

        winner = self.gs.check_win_condition()
        print_game_over(winner, self.gs)
        return winner

    async def _run_day_phase(self) -> None:
        self.gs.phase = GamePhase.DAY_DISCUSSION
        print_phase_header("DAY DISCUSSION", self.gs.round_number)

        if self.gs.round_number == 1:
            await self._narrate("Announce round 1. First morning. Town meets.")
        else:
            victim = self.gs.eliminated_this_round
            if victim:
                role = self.gs.players[victim].role
                await self._narrate(
                    f"Dawn. {victim} ({role}) was found dead. Town must now discuss."
                )
            else:
                await self._narrate("Dawn. Nobody died last night. Tension remains.")

        alive            = self.gs.get_alive_players()
        discussion_history: list[str] = []

        for _round in range(2):
            order = list(alive)
            random.shuffle(order)
            for name in order:
                if name not in self._agents:
                    continue
                agent = self._agents[name]
                reasoning, action = await agent.day_discussion(self.gs, discussion_history)
                self.gs.log(name, agent.role, agent.archetype, reasoning, action)
                self._print(name, agent.role, agent.archetype, reasoning, action)
                discussion_history.append(f"{name}: {action}")

        self.gs.phase = GamePhase.DAY_VOTE
        print_phase_header("DAY VOTE", self.gs.round_number)
        await self._narrate("Announce voting time. Players must choose who to eliminate.")

        for name in alive:
            if name not in self._agents:
                continue
            agent = self._agents[name]
            reasoning, action = await agent.cast_vote(self.gs, discussion_history)
            self.gs.log(name, agent.role, agent.archetype, reasoning, action)
            self._print(name, agent.role, agent.archetype, reasoning, action)
            vote_target = self._parse_vote(action, alive, name)
            if vote_target is None:
                # Fallback: assign a random valid target when vote parsing fails
                # (e.g. self-vote, refusal, or unparseable response)
                eligible = [p for p in alive if p != name]
                if eligible:
                    vote_target = random.choice(eligible)
                    print(
                        f"  [!] {name}'s vote was unparseable; "
                        f"random fallback -> {vote_target}",
                        file=sys.stderr,
                    )
            if vote_target:
                self.gs.votes[name] = vote_target

        eliminated = self.gs.tally_votes()
        print_vote_tally(self.gs.votes, eliminated)

        if eliminated:
            eliminated_role = self.gs.players[eliminated].role
            self.gs.eliminate_player(eliminated)
            await self._narrate(
                f"{eliminated} eliminated by vote. Role: {eliminated_role}. React dramatically."
            )
        else:
            await self._narrate("Vote tied. Nobody eliminated. Town is nervous.")

    async def _run_night_phase(self) -> None:
        if self.gs.check_win_condition():
            return

        self.gs.phase = GamePhase.NIGHT
        # Clear the day-vote elimination so that only the night kill
        # (if any) is visible to the next day's narrator.
        self.gs.eliminated_this_round = None
        print_phase_header("NIGHT", self.gs.round_number)
        await self._narrate("Night falls. Town sleeps. Mafia stirs.")

        alive_mafia   = self.gs.get_alive_mafia()
        targets       = self.gs.get_alive_town()
        mafia_actions: list[str] = []
        final_kill:   str | None = None

        for mafia_agent in self.mafia:
            if mafia_agent.name not in alive_mafia:
                continue
            partner_hint = mafia_actions[-1] if mafia_actions else None
            reasoning, action = await mafia_agent.choose_night_kill(self.gs, partner_hint)
            self.gs.log(mafia_agent.name, "Mafia", mafia_agent.archetype, reasoning, action)
            self._print(
                mafia_agent.name, "Mafia", mafia_agent.archetype,
                reasoning, f"[NIGHT TARGET]: {action}",
            )
            # Extract a valid target name from the action text
            parsed_target = self._parse_target(action, targets)
            mafia_actions.append(parsed_target or action.strip())
            final_kill = parsed_target

        if final_kill and final_kill in self.gs.get_alive_town():
            self.gs.night_kill_target = final_kill
        elif not final_kill:
            # Fallback only when no valid kill target was parsed at all
            town = self.gs.get_alive_town()
            self.gs.night_kill_target = town[0] if town else None

        if self.detective.name in self.gs.get_alive_players():
            reasoning, action = await self.detective.choose_investigation_target(self.gs)
            alive = self.gs.get_alive_players()
            target = self._parse_target(action, [p for p in alive if p != self.detective.name])
            if target and target in self.gs.players:
                true_role  = self.gs.players[target].role
                result     = "Mafia" if true_role == "Mafia" else "Innocent"
                self.detective.record_finding(target, result)
                self.gs.detective_findings[target] = result
                self.gs.log(self.detective.name, "Detective", self.detective.archetype, reasoning, action)
                self._print(
                    self.detective.name, "Detective", self.detective.archetype,
                    reasoning, f"[INVESTIGATED]: {target} -> {result}",
                )

        if self.doctor.name in self.gs.get_alive_players():
            reasoning, action = await self.doctor.choose_protection_target(self.gs)
            alive = self.gs.get_alive_players()
            protect_target = self._parse_target(action, alive) or action.strip()
            if protect_target in alive:
                self.gs.doctor_protect_target = protect_target
            self.gs.log(self.doctor.name, "Doctor", self.doctor.archetype, reasoning, action)
            self._print(
                self.doctor.name, "Doctor", self.doctor.archetype,
                reasoning, f"[PROTECTING]: {protect_target}",
            )

        killed, was_protected = self.gs.apply_night_actions()
        killed_role = (
            self.gs.players[killed].role
            if killed and killed in self.gs.players else None
        )
        print_night_result(killed, was_protected, killed_role)

        if was_protected:
            await self._narrate("Dawn. Someone targeted but survived - a mystery protector.")
        elif killed:
            await self._narrate(f"Dawn. {killed} ({killed_role}) found dead.")
        else:
            await self._narrate("A quiet dawn. Nobody died tonight.")

    async def _narrate(self, prompt: str) -> None:
        reasoning, announcement = await self.narrator.announce(prompt, self.gs)
        self.gs.log("Narrator", "Narrator", "Impartial", reasoning, announcement)
        self._print("Narrator", "Narrator", "Impartial", reasoning, announcement)

    def _print(
        self,
        name: str, role: str, archetype: str,
        reasoning: str | None, action: str,
    ) -> None:
        display_reasoning = None if self.quiet else reasoning
        print_agent_action(name, role, archetype, display_reasoning, action, not self.debug)

    def _parse_vote(self, action: str, valid_targets: list[str], voter: str) -> str | None:
        text = action.strip()
        if "VOTE:" in text.upper():
            after = text.upper().split("VOTE:", 1)[1].strip()
            for target in valid_targets:
                if target.upper() in after and target != voter:
                    return target
        text_lower = text.lower()
        for target in valid_targets:
            if target.lower() in text_lower and target != voter:
                return target
        return None

    @staticmethod
    def _parse_target(action: str, valid_targets: list[str]) -> str | None:
        """Extract a valid player name from free-form action text."""
        text = action.strip()
        # Exact match first
        if text in valid_targets:
            return text
        # Search for any valid target name mentioned in the text
        text_lower = text.lower()
        for target in valid_targets:
            if target.lower() in text_lower:
                return target
        return None
