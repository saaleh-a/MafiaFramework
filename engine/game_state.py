from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class GamePhase(Enum):
    DAY_DISCUSSION = "DAY DISCUSSION"
    DAY_VOTE       = "DAY VOTE"
    NIGHT          = "NIGHT"
    GAME_OVER      = "GAME OVER"


@dataclass
class PlayerState:
    name: str
    role: str          # "Mafia" | "Detective" | "Doctor" | "Villager"
    archetype: str     # one of the 13 archetypes
    is_alive: bool     = True
    is_revealed: bool  = False


@dataclass
class LogEntry:
    phase: GamePhase
    round_number: int
    agent_name: str
    role: str
    archetype: str
    reasoning: str | None
    action: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class GameState:
    players: dict[str, PlayerState]
    phase: GamePhase = GamePhase.DAY_DISCUSSION
    round_number: int = 1
    votes: dict[str, str] = field(default_factory=dict)
    night_kill_target: str | None = None
    doctor_protect_target: str | None = None
    last_protected: str | None = None
    detective_findings: dict[str, str] = field(default_factory=dict)
    eliminated_this_round: str | None = None
    winner: str | None = None
    game_log: list[LogEntry] = field(default_factory=list)

    def get_alive_players(self) -> list[str]:
        return [n for n, p in self.players.items() if p.is_alive]

    def get_alive_mafia(self) -> list[str]:
        return [n for n, p in self.players.items() if p.is_alive and p.role == "Mafia"]

    def get_alive_town(self) -> list[str]:
        return [n for n, p in self.players.items() if p.is_alive and p.role != "Mafia"]

    def check_win_condition(self) -> str | None:
        mafia = self.get_alive_mafia()
        town  = self.get_alive_town()
        if len(mafia) == 0:
            return "Town"
        if len(mafia) >= len(town):
            return "Mafia"
        return None

    def get_public_state_summary(self) -> str:
        alive = self.get_alive_players()
        dead  = [
            f"{n} ({p.role})"
            for n, p in self.players.items()
            if not p.is_alive and p.is_revealed
        ]
        lines = [
            f"Round {self.round_number} | Phase: {self.phase.value}",
            f"Alive: {', '.join(alive)}",
        ]
        if dead:
            lines.append(f"Eliminated: {', '.join(dead)}")
        return "\n".join(lines)

    def get_omniscient_state_summary(self) -> str:
        lines = [f"Round {self.round_number} | Phase: {self.phase.value}"]
        for n, p in self.players.items():
            status = "ALIVE" if p.is_alive else "DEAD"
            lines.append(f"  {n}: {p.role} [{p.archetype}] [{status}]")
        return "\n".join(lines)

    def tally_votes(self) -> str | None:
        if not self.votes:
            return None
        counts: dict[str, int] = {}
        for target in self.votes.values():
            counts[target] = counts.get(target, 0) + 1
        max_votes = max(counts.values())
        leaders   = [n for n, c in counts.items() if c == max_votes]
        return leaders[0] if len(leaders) == 1 else None

    def eliminate_player(self, name: str) -> None:
        if name in self.players:
            self.players[name].is_alive   = False
            self.players[name].is_revealed = True
            self.eliminated_this_round     = name

    def apply_night_actions(self) -> tuple[str | None, bool]:
        if self.night_kill_target is None:
            return None, False
        if self.night_kill_target == self.doctor_protect_target:
            return None, True
        self.eliminate_player(self.night_kill_target)
        return self.night_kill_target, False

    def reset_round_state(self) -> None:
        self.votes                 = {}
        self.last_protected        = self.doctor_protect_target
        self.night_kill_target     = None
        self.doctor_protect_target = None
        # Note: eliminated_this_round is intentionally NOT cleared here.
        # It is cleared at the start of each night phase so the next
        # day's narrator can still read who was killed overnight.

    def log(self, agent_name: str, role: str, archetype: str, reasoning: str | None, action: str) -> None:
        self.game_log.append(LogEntry(
            phase=self.phase,
            round_number=self.round_number,
            agent_name=agent_name,
            role=role,
            archetype=archetype,
            reasoning=reasoning,
            action=action,
        ))
