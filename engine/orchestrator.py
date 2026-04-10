"""
engine/orchestrator.py - v4
-----------------------------
Game loop. Passes archetype through to all display and logging calls.
Integrates:
  - SuspicionState: structured-intuition suspicion tracking per agent
  - SummaryAgent: Low-cognitive-load narrative summaries per phase
  - MAF ContextProviders: belief state + memory injected via session.state
  - MAF Agent middleware: corporate-speak enforcement via pipeline
  - MAF @tool: structured vote/target actions via tool-calling
  - Rate limiting: phase-tier semaphore + graceful degradation fallbacks

v4 changes (from v3):
  - Replaced manual belief_prefix string concatenation with MAF ContextProviders
  - Added _sync_provider_state() to push game state into session.state
  - Agents now use Agent() constructor with tools, middleware, compaction
  - Corporate-speak enforcement moved from base.py retry to agent middleware
"""

import asyncio
import logging
import random
import re
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
from agents.belief_state import (
    SuspicionState,
    build_belief_prompt_injection,
    parse_belief_updates,
    apply_overconfidence_gate,
    BeliefGraph,
    TemporalConsistencyChecker,
)
from agents.providers import BeliefStateProvider, CrossGameMemoryProvider
from agents.summary   import SummaryAgent
from agents.memory    import GameMemoryStore
from config.settings  import MAFIA_MAX_CONCURRENT_CALLS

logger = logging.getLogger(__name__)

# Phase-tier semaphore: limits concurrency within a single phase to
# prevent burst patterns during discussion→vote→night transitions.
_phase_semaphore: asyncio.Semaphore | None = None


def _get_phase_semaphore() -> asyncio.Semaphore:
    """Lazily create the per-phase concurrency limiter."""
    global _phase_semaphore
    if _phase_semaphore is None:
        # Allow slightly fewer concurrent calls per phase than the global
        # limit so that different phases don't compete for the full pool.
        phase_limit = max(2, MAFIA_MAX_CONCURRENT_CALLS - 1)
        _phase_semaphore = asyncio.Semaphore(phase_limit)
    return _phase_semaphore


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
        memory_store: GameMemoryStore | None = None,
        assignments:  list[dict] | None = None,
    ) -> None:
        self.gs        = game_state
        self.narrator  = narrator
        self.mafia     = mafia_agents
        self.detective = detective
        self.doctor    = doctor
        self.villagers = villagers
        self.debug     = debug
        self.quiet     = quiet
        self._memory   = memory_store
        self._assignments = assignments or []
        self._agents: dict[str, any] = {}
        for a in mafia_agents + [detective, doctor] + villagers:
            self._agents[a.name] = a

        # Suspicion State: each agent tracks suspicion levels (structured intuition)
        player_names = list(game_state.players.keys())
        self._beliefs: dict[str, SuspicionState] = {}
        for name in player_names:
            belief = SuspicionState()
            # Exclude self from suspicion tracking
            others = [n for n in player_names if n != name]
            belief.initialize(others, num_mafia=2)
            self._beliefs[name] = belief

        # Summary: generates low-cognitive-load narrative each phase
        self._summary = SummaryAgent()

        # BeliefGraph: scum-tell pattern detection (bandwagon, redirect, instahammer)
        self._belief_graph = BeliefGraph()

        # Temporal consistency: "DeepSeek" slip detection
        self._temporal_checker = TemporalConsistencyChecker()

        # Populate MAF ContextProvider state on each agent's session.
        # This is how the BeliefStateProvider and CrossGameMemoryProvider
        # get their data — via session.state, the MAF-idiomatic way.
        self._sync_provider_state()

    def _sync_provider_state(self) -> None:
        """
        Push current game state into each agent's session.state so that
        MAF ContextProviders can read it during before_run().

        Called once at init and again whenever beliefs/graphs update.
        """
        for name, agent in self._agents.items():
            session = agent.session
            belief = self._beliefs.get(name)

            # BeliefStateProvider state
            session.state.setdefault(BeliefStateProvider.DEFAULT_SOURCE_ID, {})
            belief_state = session.state[BeliefStateProvider.DEFAULT_SOURCE_ID]
            belief_state["suspicion"] = belief
            belief_state["archetype"] = agent.archetype
            belief_state["graph"] = self._belief_graph
            belief_state["temporal"] = self._temporal_checker
            belief_state["all_beliefs"] = self._beliefs
            belief_state["role"] = agent.role
            belief_state["name"] = name

            # CrossGameMemoryProvider state
            session.state.setdefault(CrossGameMemoryProvider.DEFAULT_SOURCE_ID, {})
            mem_state = session.state[CrossGameMemoryProvider.DEFAULT_SOURCE_ID]
            mem_state["store"] = self._memory
            mem_state["role"] = agent.role

    # ------------------------------------------------------------------ #
    #  Graceful degradation fallbacks                                      #
    # ------------------------------------------------------------------ #

    def _fallback_discussion(self, name: str) -> tuple[str, str]:
        """Fallback when discussion API call fails: player passes."""
        logger.warning("[%s] Discussion call failed — player passes turn", name)
        return ("", "I'll listen for now.")

    def _fallback_vote(self, name: str, alive: list[str]) -> tuple[str, str]:
        """Fallback when vote API call fails: vote using belief state."""
        belief = self._beliefs.get(name)
        eligible = [p for p in alive if p != name]
        if belief and eligible:
            # Vote for highest-suspicion player
            scored = [(p, belief.probabilities.get(p, 0.0)) for p in eligible]
            scored.sort(key=lambda x: -x[1])
            target = scored[0][0]
            logger.warning(
                "[%s] Vote call failed — fallback to highest suspicion: %s",
                name, target,
            )
            return ("", f"VOTE: {target}")
        if eligible:
            target = random.choice(eligible)
            logger.warning(
                "[%s] Vote call failed — random fallback: %s",
                name, target,
            )
            return ("", f"VOTE: {target}")
        return ("", "")

    def _fallback_night_kill(self, name: str, targets: list[str]) -> tuple[str, str]:
        """Fallback when Mafia night kill API call fails: target highest-threat."""
        belief = self._beliefs.get(name)
        if belief and targets:
            scored = [(p, belief.probabilities.get(p, 0.0)) for p in targets]
            # Mafia targets the player LEAST suspected (highest threat to Mafia)
            # because least-suspected Town players are likely power roles.
            scored.sort(key=lambda x: x[1])
            target = scored[0][0]
            logger.warning(
                "[%s] Night kill call failed — fallback to lowest-suspicion Town: %s",
                name, target,
            )
            return ("", target)
        if targets:
            target = random.choice(targets)
            logger.warning("[%s] Night kill call failed — random: %s", name, target)
            return ("", target)
        return ("", "")

    def _fallback_investigation(self, name: str, eligible: list[str]) -> tuple[str, str]:
        """Fallback when Detective investigation API call fails: most suspicious."""
        belief = self._beliefs.get(name)
        if belief and eligible:
            scored = [(p, belief.probabilities.get(p, 0.0)) for p in eligible]
            scored.sort(key=lambda x: -x[1])
            target = scored[0][0]
            logger.warning(
                "[%s] Investigation call failed — fallback to most suspicious: %s",
                name, target,
            )
            return ("", target)
        if eligible:
            target = random.choice(eligible)
            logger.warning("[%s] Investigation call failed — random: %s", name, target)
            return ("", target)
        return ("", "")

    def _fallback_protection(self, name: str, valid: list[str]) -> tuple[str, str]:
        """Fallback when Doctor protection API call fails: protect self if threatened."""
        belief = self._beliefs.get(name)
        if belief and name in [p for p in self.gs.get_alive_players()]:
            own_suspicion_values = []
            for _, b in self._beliefs.items():
                if name in b.probabilities:
                    own_suspicion_values.append(b.probabilities[name])
            avg_suspicion = (
                sum(own_suspicion_values) / len(own_suspicion_values)
                if own_suspicion_values else 0.0
            )
            # If Doctor is threatened (>0.3 suspicion), protect self
            if avg_suspicion > 0.3 and name in valid:
                logger.warning(
                    "[%s] Protection call failed — self-protect (suspicion %.2f)",
                    name, avg_suspicion,
                )
                return ("", name)
        if valid:
            target = random.choice(valid)
            logger.warning("[%s] Protection call failed — random: %s", name, target)
            return ("", target)
        return ("", "")

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

        # Persist cross-game learnings
        if self._memory:
            self._memory.record_game_outcome(
                winner=winner,
                role_assignments=self._assignments,
                round_count=self.gs.round_number,
            )
            self._memory.save()

        return winner

    async def _run_day_phase(self) -> None:
        self.gs.phase = GamePhase.DAY_DISCUSSION
        print_phase_header("DAY DISCUSSION", self.gs.round_number)

        # Summary Agent: display narrative summary at phase start
        narrative = self._summary.summarize(self.gs)
        print(narrative)

        if self.gs.round_number == 1:
            await self._narrate("Announce round 1. First morning. Town meets.")
        else:
            victim = self.gs.eliminated_this_round
            if victim:
                role = self.gs.players[victim].role
                await self._narrate(
                    f"Dawn. {victim} ({role}) was found dead. Town must now discuss."
                )
                # Remove eliminated player from all belief states
                for belief in self._beliefs.values():
                    belief.remove_player(victim)
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

                # Sync ContextProvider state before each agent turn.
                # The BeliefStateProvider and CrossGameMemoryProvider read
                # from session.state — this is the MAF-idiomatic approach.
                self._sync_provider_state()

                try:
                    reasoning, action = await agent.day_discussion(
                        self.gs, discussion_history,
                    )
                except Exception as exc:
                    logger.error("[%s] Discussion failed: %s", name, exc)
                    reasoning, action = self._fallback_discussion(name)

                # Parse and apply belief updates from reasoning
                belief = self._beliefs.get(name)
                if belief and reasoning:
                    updates = parse_belief_updates(reasoning)
                    for target, prob in updates.items():
                        belief.update(target, prob)

                # Overconfidence gate: soften if certainty too low
                if belief and agent.archetype == "Overconfident":
                    action = apply_overconfidence_gate(action, belief)

                # Track discussion contribution in BeliefGraph
                self._belief_graph.record_discussion(name)

                # Check for temporal slips in the action
                self._temporal_checker.check_message(
                    name, action, self.gs.round_number,
                )

                # Check for redirects (BeliefGraph)
                current_target = self._get_current_consensus(discussion_history, alive)
                self._belief_graph.check_redirect(
                    name, action, current_target, alive,
                )

                self.gs.log(name, agent.role, agent.archetype, reasoning, action)
                self._print(name, agent.role, agent.archetype, reasoning, action,
                            personality=getattr(agent, 'personality', ''))
                discussion_history.append(f"{name}: {action}")

        self.gs.phase = GamePhase.DAY_VOTE
        print_phase_header("DAY VOTE", self.gs.round_number)

        # Summary Agent: display narrative summary before voting
        narrative = self._summary.summarize(self.gs)
        print(narrative)

        await self._narrate("Announce voting time. Players must choose who to eliminate.")

        await self._collect_votes(alive, discussion_history)

        eliminated = self.gs.tally_votes()
        tied_players = self.gs.get_tied_players()

        # ----------------------------------------------------------------
        #  Tie-Break Protocol (two stages)
        # ----------------------------------------------------------------
        if not eliminated and tied_players:
            print_vote_tally(self.gs.votes, None)
            await self._narrate(
                f"The vote is tied between {', '.join(tied_players)}! "
                f"They will now state their defence before a decisive re-vote."
            )

            # Stage 1 — Defence Phase: each tied player gets a turn
            print_phase_header("TIE-BREAK: DEFENCE", self.gs.round_number)
            for name in tied_players:
                if name not in self._agents:
                    continue
                agent = self._agents[name]
                reasoning, action = await agent.day_discussion(
                    self.gs, discussion_history + [
                        f"[SYSTEM]: {name}, you are tied for elimination. "
                        f"State your case to the town."
                    ],
                )
                self.gs.log(name, agent.role, agent.archetype, reasoning, action)
                self._print(name, agent.role, agent.archetype, reasoning, action,
                            personality=getattr(agent, 'personality', ''))
                discussion_history.append(f"{name} (DEFENCE): {action}")

            # Stage 2 — Decisive Vote: everyone *except* tied players
            print_phase_header("TIE-BREAK: DECISIVE VOTE", self.gs.round_number)
            self.gs.votes = {}  # reset for re-vote
            decisive_voters = [p for p in alive if p not in tied_players]
            await self._collect_votes(decisive_voters, discussion_history)

            eliminated = self.gs.tally_votes()
            tied_again = self.gs.get_tied_players()

            # No-Kill Fallback: if a second tie occurs, no elimination
            if not eliminated and tied_again:
                eliminated = None

        print_vote_tally(self.gs.votes, eliminated)

        if eliminated:
            eliminated_role = self.gs.players[eliminated].role
            self.gs.eliminate_player(eliminated)
            await self._narrate(
                f"{eliminated} eliminated by vote. Role: {eliminated_role}. React dramatically."
            )
        else:
            await self._narrate("Vote tied. Nobody eliminated. Town is nervous.")

        # Reset per-round BeliefGraph tracking (keep cumulative flags)
        self._belief_graph.reset_round()

    async def _collect_votes(
        self, voters: list[str], discussion_history: list[str],
    ) -> None:
        """Run a vote round for *voters*, populating ``self.gs.votes``."""
        alive = self.gs.get_alive_players()
        for name in voters:
            if name not in self._agents:
                continue
            agent = self._agents[name]
            try:
                reasoning, action = await agent.cast_vote(self.gs, discussion_history)
            except Exception as exc:
                logger.error("[%s] Vote call failed: %s", name, exc)
                reasoning, action = self._fallback_vote(name, alive)

            # Parse belief updates from vote reasoning too
            belief = self._beliefs.get(name)
            if belief and reasoning:
                updates = parse_belief_updates(reasoning)
                for target, prob in updates.items():
                    belief.update(target, prob)

            self.gs.log(name, agent.role, agent.archetype, reasoning, action)
            self._print(name, agent.role, agent.archetype, reasoning, action,
                        personality=getattr(agent, 'personality', ''))
            vote_target = self._parse_vote(action, alive, name)
            if vote_target is None:
                # Fallback: assign a random valid target when vote parsing fails
                # (e.g. self-vote, refusal, or unparseable response)
                eligible = [p for p in alive if p != name]
                if eligible:
                    vote_target = random.choice(eligible)
                    # Include raw text so failures are diagnosable
                    raw_preview = action[:200].replace("\n", " ")
                    print(
                        f"  [!] {name}'s vote was unparseable; "
                        f"random fallback -> {vote_target}\n"
                        f"      Raw action text: \"{raw_preview}\"",
                        file=sys.stderr,
                    )
            if vote_target:
                self.gs.votes[name] = vote_target

                # BeliefGraph: check for late bandwagon
                self._belief_graph.check_late_bandwagon(
                    name, vote_target, reasoning or "",
                    self.gs.votes,
                )

                # BeliefGraph: check for instahammer
                votes_before_current = len(self.gs.votes) - 1
                self._belief_graph.check_instahammer(
                    name,
                    votes_before_current,
                    len(alive),
                )

    async def _run_night_phase(self) -> None:
        if self.gs.check_win_condition():
            return

        self.gs.phase = GamePhase.NIGHT
        # Clear the day-vote elimination so that only the night kill
        # (if any) is visible to the next day's narrator.
        self.gs.eliminated_this_round = None
        print_phase_header("NIGHT", self.gs.round_number)

        # Summary Agent: display narrative summary at night start
        narrative = self._summary.summarize(self.gs)
        print(narrative)

        await self._narrate("Night falls. Town sleeps. Mafia stirs.")

        alive_mafia   = self.gs.get_alive_mafia()
        targets       = self.gs.get_alive_town()
        mafia_actions: list[str] = []
        final_kill:   str | None = None

        for mafia_agent in self.mafia:
            if mafia_agent.name not in alive_mafia:
                continue
            partner_hint = mafia_actions[-1] if mafia_actions else None

            # Syndicate channel: find partner's previous night reasoning
            partner_reasoning = None
            for other in self.mafia:
                if other.name != mafia_agent.name and other.last_night_reasoning:
                    partner_reasoning = other.last_night_reasoning
                    break

            try:
                reasoning, action = await mafia_agent.choose_night_kill(
                    self.gs, partner_hint, partner_reasoning,
                )
            except Exception as exc:
                logger.error("[%s] Night kill call failed: %s", mafia_agent.name, exc)
                reasoning, action = self._fallback_night_kill(mafia_agent.name, targets)
            self.gs.log(mafia_agent.name, "Mafia", mafia_agent.archetype, reasoning, action)
            self._print(
                mafia_agent.name, "Mafia", mafia_agent.archetype,
                reasoning, f"[NIGHT TARGET]: {action}",
                personality=mafia_agent.personality,
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
            alive = self.gs.get_alive_players()
            eligible = [p for p in alive if p != self.detective.name]
            try:
                reasoning, action = await self.detective.choose_investigation_target(self.gs)
            except Exception as exc:
                logger.error("[%s] Investigation call failed: %s", self.detective.name, exc)
                reasoning, action = self._fallback_investigation(self.detective.name, eligible)
            target = self._parse_target(action, eligible)
            if not target and eligible:
                target = random.choice(eligible)
                print(
                    f"  [!] {self.detective.name}'s investigation target was unparseable; "
                    f"random fallback -> {target}",
                    file=sys.stderr,
                )
            if target and target in self.gs.players:
                true_role  = self.gs.players[target].role
                result     = "Mafia" if true_role == "Mafia" else "Innocent"
                self.detective.record_finding(target, result)
                self.gs.detective_findings[target] = result
                self.gs.log(self.detective.name, "Detective", self.detective.archetype, reasoning, action)
                self._print(
                    self.detective.name, "Detective", self.detective.archetype,
                    reasoning, f"[INVESTIGATED]: {target} -> {result}",
                    personality=self.detective.personality,
                )

        if self.doctor.name in self.gs.get_alive_players():
            alive = self.gs.get_alive_players()
            valid = [p for p in alive if p != self.gs.last_protected]
            try:
                reasoning, action = await self.doctor.choose_protection_target(self.gs)
            except Exception as exc:
                logger.error("[%s] Protection call failed: %s", self.doctor.name, exc)
                reasoning, action = self._fallback_protection(self.doctor.name, valid)
            protect_target = self._parse_target(action, alive)
            if not protect_target:
                eligible = [p for p in alive if p != self.gs.last_protected]
                if eligible:
                    protect_target = random.choice(eligible)
                    print(
                        f"  [!] {self.doctor.name}'s protection target was unparseable; "
                        f"random fallback -> {protect_target}",
                        file=sys.stderr,
                    )
            # Server-side consecutive-protection enforcement
            if protect_target and protect_target == self.gs.last_protected:
                eligible = [p for p in alive if p != self.gs.last_protected]
                if eligible:
                    protect_target = random.choice(eligible)
                    print(
                        f"  [!] {self.doctor.name} tried to protect the same player "
                        f"two nights in a row; random fallback -> {protect_target}",
                        file=sys.stderr,
                    )
            if protect_target and protect_target in alive:
                self.gs.doctor_protect_target = protect_target
                self.doctor.last_protected = protect_target
            self.gs.log(self.doctor.name, "Doctor", self.doctor.archetype, reasoning, action)
            self._print(
                self.doctor.name, "Doctor", self.doctor.archetype,
                reasoning, f"[PROTECTING]: {protect_target}",
                personality=self.doctor.personality,
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
        personality: str = "",
    ) -> None:
        display_reasoning = None if self.quiet else reasoning
        print_agent_action(name, role, archetype, display_reasoning, action, not self.debug, personality=personality)

    def _parse_vote(self, action: str, valid_targets: list[str], voter: str) -> str | None:
        """
        Intent-based vote parser with three priority tiers.

        Priority 1: Explicit ``VOTE: {name}`` tags.
        Priority 2: Intent phrases (``I'm voting for …``, ``Staying on …``).
        Priority 3: Last mentioned valid name (avoids parsing addressees).
        Hard filter: A self-vote always returns ``None``.
        """
        text = action.strip()

        # ------------------------------------------------------------------
        # Priority 1 — explicit VOTE: tag
        # ------------------------------------------------------------------
        vote_tag = re.search(r"VOTE:\s*(\w+)", text, re.IGNORECASE)
        if vote_tag:
            tagged = vote_tag.group(1).strip()
            for target in valid_targets:
                if target.lower() == tagged.lower() and target != voter:
                    return target

        # ------------------------------------------------------------------
        # Priority 2 — intent phrases
        # ------------------------------------------------------------------
        intent_patterns = [
            r"(?:I(?:'m| am)\s+voting\s+(?:for\s+)?)",
            r"(?:my\s+vote\s+(?:is\s+(?:for\s+)?|goes?\s+to\s+))",
            r"(?:I\s+vote\s+(?:for\s+)?)",
            r"(?:staying\s+on\s+)",
            r"(?:I(?:'m| am)\s+going\s+with\s+)",
            r"(?:locking\s+(?:in\s+)?(?:on\s+)?)",
            r"(?:voting\s+out\s+)",
            r"(?:I\s+cast\s+(?:my\s+)?vote\s+(?:for\s+)?)",
        ]
        combined = "|".join(intent_patterns)
        intent_match = re.search(
            rf"(?:{combined})(\w+)", text, re.IGNORECASE,
        )
        if intent_match:
            candidate = intent_match.group(1).strip()
            for target in valid_targets:
                if target.lower() == candidate.lower() and target != voter:
                    return target

        # ------------------------------------------------------------------
        # Priority 3 — last mentioned valid name
        # ------------------------------------------------------------------
        last_found: str | None = None
        text_lower = text.lower()
        for target in valid_targets:
            # Find the *last* occurrence index for each target
            idx = text_lower.rfind(target.lower())
            if idx != -1 and target != voter:
                if last_found is None or idx > text_lower.rfind(last_found.lower()):
                    last_found = target

        return last_found  # may be None if nothing matched

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

    @staticmethod
    def _get_current_consensus(
        discussion_history: list[str], alive: list[str],
    ) -> str | None:
        """
        Identify the player most frequently mentioned in accusatory
        context across the discussion history. Used by BeliefGraph
        to detect redirects away from the consensus target.
        """
        if not discussion_history:
            return None
        mention_counts: dict[str, int] = {name: 0 for name in alive}
        accusation_words = {
            "suspect", "vote", "sus", "mafia", "guilty", "suspicious",
            "accuse", "hammer", "lynch",
        }
        for line in discussion_history:
            line_lower = line.lower()
            if not any(w in line_lower for w in accusation_words):
                continue
            speaker = line.split(":", 1)[0].strip() if ":" in line else ""
            for name in alive:
                if name.lower() in line_lower and name != speaker:
                    mention_counts[name] += 1
        if not any(mention_counts.values()):
            return None
        return max(mention_counts, key=lambda k: mention_counts[k])
