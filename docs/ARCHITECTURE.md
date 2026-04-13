# Architecture

A complete technical reference to MafiaFramework's system architecture — every module, every data flow, every integration point.

---

## TL;DR

MafiaFramework is a four-layer async Python application: **CLI → Engine → Agents → Config/Prompts**. `main.py` parses arguments and runs games. `engine/game_manager.py` randomises 4 dimensions (role, model, archetype, personality) with a 3-tier constraint system and creates all agents. `engine/orchestrator.py` (1,220 lines) runs the game loop — day discussion, voting with tie-break, night actions — coordinating 11 agents through MAF's `Agent.run()` with streaming, middleware, and context providers. Each agent wraps a MAF `Agent` with role-specific logic, a `SuspicionState` for structured belief tracking, and a 5-layer middleware stack (session resilience → rate limiting → slang enforcement → output parsing → belief extraction). Prompts are assembled from 9 layers in `prompts/builder.py`. State lives in `engine/game_state.py` dataclasses. Cross-game memory persists to disk as JSON. Rate limiting uses a global `asyncio.Semaphore` with exponential backoff. The entire system runs on 3 dependencies: `agent-framework-foundry`, `azure-identity`, `python-dotenv`.

---

## ELI5

Think of MafiaFramework like a theatre production. The **director** (`main.py`) decides to put on a show. The **casting department** (`game_manager.py`) picks which actors play which roles and gives them costumes. The **stage manager** (`orchestrator.py`) runs the actual show — telling actors when to speak, collecting their votes, and announcing who gets eliminated. Each **actor** (agent) has a script template, but they improvise their actual lines using an AI brain. They keep a diary of who they think is suspicious (`belief_state.py`). If an actor freezes on stage (API error), the stage manager gives them a default line. If the theatre's power flickers (session expires), the stage manager quickly rebuilds the set from memory. After each show, the actors write down what they learned for next time (`memory.py`).

---

## Table of Contents

- [System Overview](#system-overview)
- [Directory Structure](#directory-structure)
- [Module Reference](#module-reference)
  - [Entry Point](#entry-point-mainpy)
  - [Engine Layer](#engine-layer)
  - [Agent Layer](#agent-layer)
  - [Prompt Layer](#prompt-layer)
  - [Config Layer](#config-layer)
  - [Test Suite](#test-suite)
- [Data Flow Diagrams](#data-flow-diagrams)
- [State Management](#state-management)
- [Middleware Architecture](#middleware-architecture)
- [Context Provider Architecture](#context-provider-architecture)
- [Concurrency Model](#concurrency-model)
- [Error Handling Architecture](#error-handling-architecture)
- [Dependencies](#dependencies)

---

## System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                          main.py                                 │
│              CLI parsing, game loop, statistics                   │
└──────────────────────────┬───────────────────────────────────────┘
                           │
         ┌─────────────────▼──────────────────┐
         │      engine/game_manager.py        │
         │  Role/Model/Archetype/Personality  │
         │  randomisation + constraint system │
         └─────────────────┬──────────────────┘
                           │
         ┌─────────────────▼──────────────────┐
         │      engine/orchestrator.py        │
         │  Game loop: discussion → voting →  │
         │  night actions → belief sync →     │
         │  win detection (1,220 lines)       │
         └──┬──────────┬──────────┬───────────┘
            │          │          │
    ┌───────▼──┐ ┌─────▼───┐ ┌───▼─────────┐
    │ agents/  │ │engine/  │ │  prompts/   │
    │ Per-role │ │State,   │ │ Frameworks  │
    │ wrappers │ │logging, │ │ Archetypes  │
    │ Belief   │ │display  │ │ Personality │
    │ Memory   │ │         │ │ Builder     │
    │ Tools    │ │         │ │             │
    │ Middleware│ │         │ │             │
    └────┬─────┘ └─────────┘ └─────────────┘
         │
    ┌────▼─────────────┐
    │ config/          │
    │ Model registry   │
    │ Settings (env)   │
    └──────────────────┘
```

---

## Directory Structure

```
MafiaFramework/
├── main.py                          # Entry point (136 lines)
├── check.py                         # Pre-flight connectivity check (53 lines)
├── requirements.txt                 # 3 dependencies
├── .env                             # Azure credentials (gitignored values)
├── .gitignore                       # Excludes __pycache__/ and memory/
│
├── agents/                          # Agent layer
│   ├── __init__.py
│   ├── base.py                      # Shared utilities (596 lines)
│   ├── belief_state.py              # Belief tracking (650+ lines)
│   ├── summary.py                   # Narrative summaries
│   ├── providers.py                 # MAF ContextProviders (217 lines)
│   ├── middleware.py                # MAF middleware stack (541 lines)
│   ├── rate_limiter.py              # Rate limiting (194 lines)
│   ├── game_tools.py                # @tool functions (40 lines)
│   ├── memory.py                    # Cross-game memory (188 lines)
│   ├── narrator.py                  # Narrator agent (35 lines)
│   ├── mafia.py                     # Mafia agent (129 lines)
│   ├── detective.py                 # Detective agent (117 lines)
│   ├── doctor.py                    # Doctor agent (86 lines)
│   └── villager.py                  # Villager agent (67 lines)
│
├── engine/                          # Engine layer
│   ├── __init__.py
│   ├── game_manager.py              # Game setup factory (339 lines)
│   ├── game_state.py                # State model (167 lines)
│   ├── orchestrator.py              # Game loop (1,220 lines)
│   └── game_log.py                  # Terminal display (176 lines)
│
├── config/                          # Configuration layer
│   ├── __init__.py
│   ├── model_registry.py            # Model pool + client factory (101 lines)
│   └── settings.py                  # Environment variable loader (71 lines)
│
├── prompts/                         # Prompt engineering layer
│   ├── __init__.py
│   ├── builder.py                   # System prompt assembler (300+ lines)
│   ├── frameworks.py                # 9 reasoning frameworks (300+ lines)
│   ├── archetypes.py                # 13 archetypes + constraints (500+ lines)
│   └── personalities.py             # 8 personalities (200+ lines)
│
├── tests/                           # Test suite
│   ├── __init__.py
│   └── test_refactor.py             # 329 tests, 84 classes (3,718 lines)
│
├── docs/                            # Documentation
│   ├── METHODOLOGY.md
│   ├── ARCHITECTURE.md              # (this file)
│   └── ONBOARDING.md
│
└── memory/                          # Cross-game learnings (gitignored)
    ├── detective_learnings.json
    ├── doctor_learnings.json
    ├── mafia_learnings.json
    ├── villager_learnings.json
    └── global_patterns.json
```

---

## Module Reference

### Entry Point: `main.py`

**Lines:** 136  
**Purpose:** CLI parsing, environment validation, game execution loop.

**Functions:**

| Function                       | Purpose                                                    |
|--------------------------------|------------------------------------------------------------|
| `_configure_console_encoding()`| Sets UTF-8 output for Windows box-drawing characters       |
| `run_one_game(debug, quiet, reveal_roles, demo)` | Creates game setup, runs orchestrator, returns winner |
| `main(debug, quiet, reveal_roles, games, seed, demo)` | Validates env, runs N games, aggregates stats |

**CLI Arguments:**

| Flag             | Type  | Default | Description                                              |
|------------------|-------|---------|----------------------------------------------------------|
| `--debug`        | bool  | False   | Show full reasoning (no truncation)                      |
| `--quiet`        | bool  | False   | Action lines only                                        |
| `--reveal-roles` | bool  | False   | Show all role assignments at start                       |
| `--games`        | int   | 1       | Run N games, print aggregate statistics                  |
| `--seed`         | int   | None    | Random seed for reproducible games                       |
| `--demo`         | bool  | False   | Restrict to demo-safe personality subset                 |

**Flow:**
1. `_configure_console_encoding()` → UTF-8 setup
2. `validate_environment()` → Check Azure credentials
3. For each game: `run_one_game()` → `create_game()` → `MafiaGameOrchestrator.run_game()` → winner
4. Print aggregate statistics if `--games > 1`

---

### Engine Layer

#### `engine/game_manager.py` (339 lines)

**Purpose:** Game setup factory. Randomises all four dimensions and enforces constraints.

**Key Types:**

```python
@dataclass
class GameSetup:
    game_state: GameState
    narrator: NarratorAgent
    mafia_agents: list[MafiaAgent]
    detective: DetectiveAgent
    doctor: DoctorAgent
    villagers: list[VillagerAgent]
    assignments: list[dict]
    memory_store: GameMemoryStore
```

**Constants:**

| Constant                              | Value/Type                     | Purpose                                          |
|---------------------------------------|--------------------------------|--------------------------------------------------|
| `PLAYER_NAMES`                        | 11 names (Alice..Kate)         | Fixed player name pool                           |
| `PERSONALITY_EXCLUSIONS`              | dict[str, list[str]]           | Role → banned personalities                     |
| `ARCHETYPE_PERSONALITY_EXCLUSIONS`    | dict[str, list[str]]           | Archetype → banned personalities (Tier 1+3)     |
| `ROLE_ARCHETYPE_PERSONALITY_EXCLUSIONS` | dict[tuple, set]             | (Role, Archetype) → banned personalities (Tier 2) |
| `CONSENSUS_PERSONALITIES`             | set of 4                       | Capped at 1/game                                |
| `INDEPENDENT_ARCHETYPES`              | set of 4                       | Floor of 2/game                                 |
| `_PERSONALITY_FREQUENCY_CAP`          | 2                              | Max occurrences per personality                  |
| `_CONSENSUS_PERSONALITY_CAP`          | 1                              | Max for consensus personalities                  |

**Key Functions:**

| Function                          | Purpose                                                           |
|-----------------------------------|-------------------------------------------------------------------|
| `create_game(narrator_model, demo)` | Main factory — builds complete GameSetup                        |
| `_build_role_distribution(n)`     | Returns role list based on player count                          |
| `_recommended_mafia_count(n)`     | ≤10→2, ≤15→3, 15+→4 Mafia                                      |
| `_pick_archetype(role)`           | Role-appropriate archetype (Villager uses restricted pool)       |
| `_pick_personality(demo)`         | Random from full or demo subset                                  |
| `_pick_personality_constrained(role, counts, demo, archetype)` | Apply all 3 tiers + caps     |
| `print_assignments(setup, reveal)` | Display role table                                              |

**Constraint Application Order:**
1. Role-personality exclusions (`PERSONALITY_EXCLUSIONS`)
2. Archetype-personality exclusions (`ARCHETYPE_PERSONALITY_EXCLUSIONS`) — Tier 1+3
3. Role-archetype-personality exclusions (`ROLE_ARCHETYPE_PERSONALITY_EXCLUSIONS`) — Tier 2
4. Frequency cap (≤2 per personality, ≤1 for consensus)
5. Cap relaxation if all exhausted (preserves hard exclusions, drops frequency limit)

---

#### `engine/game_state.py` (167 lines)

**Purpose:** Core data model — all mutable game state.

**Enums:**

```python
class GamePhase(Enum):
    DAY_DISCUSSION = "DAY_DISCUSSION"
    DAY_VOTE = "DAY_VOTE"
    NIGHT = "NIGHT"
    GAME_OVER = "GAME_OVER"
```

**Dataclasses:**

```python
@dataclass
class PlayerState:
    name: str
    role: str                    # "Mafia" | "Detective" | "Doctor" | "Villager"
    archetype: str               # One of 13 archetypes
    personality: str             # One of 8 personalities
    is_alive: bool = True
    is_revealed: bool = False    # Set when eliminated
    eliminated_round: int | None = None

@dataclass
class LogEntry:
    phase: GamePhase
    round_number: int
    agent_name: str
    role: str
    archetype: str
    reasoning: str | None
    action: str
    timestamp: datetime

@dataclass
class GameState:
    players: dict[str, PlayerState]
    phase: GamePhase = DAY_DISCUSSION
    round_number: int = 1
    votes: dict[str, str] = field(default_factory=dict)
    night_kill_target: str | None = None
    doctor_protect_target: str | None = None
    last_protected: str | None = None
    detective_findings: dict[str, str] = field(default_factory=dict)
    evasion_scores: dict[str, int] = field(default_factory=dict)
    eliminated_this_round: str | None = None
    winner: str | None = None
    game_log: list[LogEntry] = field(default_factory=list)
```

**Key Methods:**

| Method                        | Returns        | Purpose                                           |
|-------------------------------|----------------|---------------------------------------------------|
| `get_alive_players()`         | list[str]      | Names of living players                           |
| `get_alive_mafia()`           | list[str]      | Names of living Mafia                             |
| `get_alive_town()`            | list[str]      | Names of living non-Mafia                         |
| `check_win_condition()`       | str \| None    | "Town", "Mafia", or None                          |
| `get_public_state_summary()`  | str            | Safe summary (dead roles only)                    |
| `get_omniscient_state_summary()` | str         | Full summary (all roles — Narrator only)          |
| `tally_votes()`               | str \| None    | Plurality winner or None on tie                   |
| `get_tied_players()`          | list[str]      | Players tied for most votes                       |
| `get_vote_weight(voter)`      | int            | 1 or MAFIA_DETECTIVE_VOTE_WEIGHT for Detective    |
| `get_weighted_vote_counts(votes)` | dict       | Target → weighted count                           |
| `eliminate_player(name)`      | None           | Mark dead, revealed, record round                 |
| `apply_night_actions()`       | tuple          | (killed_player, was_protected)                    |
| `reset_round_state()`         | None           | Clear votes and night targets                     |
| `log(...)`                    | None           | Append LogEntry to game_log                       |

---

#### `engine/orchestrator.py` (1,220 lines)

**Purpose:** The game loop controller. Coordinates all agents through day/night phases with belief tracking, vote coordination, and error recovery.

**Class: `MafiaGameOrchestrator`**

**Constructor State:**

| Attribute                          | Type                           | Purpose                                     |
|------------------------------------|--------------------------------|---------------------------------------------|
| `game_state`                       | GameState                      | Mutable game state                          |
| `_agents`                          | dict[str, Agent]               | Name → agent wrapper                        |
| `_beliefs`                         | dict[str, SuspicionState]      | Per-player belief state                     |
| `_belief_graph`                    | BeliefGraph                    | Scum-tell detector                          |
| `_temporal_checker`                | TemporalConsistencyChecker     | Temporal slip detector                      |
| `_vote_parse_failures`             | dict[str, int]                 | Per-voter parse failure count               |
| `_current_vote_shortlist`          | list[str]                      | Pressure wagon targets                      |
| `_current_vote_recommendations`    | dict[str, str]                 | Per-voter vote recommendation               |
| `_last_vote_warnings`              | list[str]                      | Warnings from last vote                     |
| `narrator`                         | NarratorAgent                  | Game master                                 |
| `summary_agent`                    | SummaryAgent                   | Narrative summary generator                 |
| `debug`, `quiet`                   | bool                           | Display flags                               |
| `memory_store`                     | GameMemoryStore \| None        | Cross-game learning store                   |

**Core Game Loop:**

```
run_game()
├── print_game_banner()
├── narrator.announce("Game start")
├── LOOP:
│   ├── _run_day_phase()
│   │   ├── Set phase = DAY_DISCUSSION
│   │   ├── summary_agent.summarize() → narrative
│   │   ├── narrator.announce(day_opening)
│   │   ├── FOR each alive player (2 rounds, shuffled):
│   │   │   ├── _sync_provider_state()
│   │   │   ├── summary_agent.compress_discussion_history()
│   │   │   ├── agent.day_discussion() → (reasoning, action)
│   │   │   ├── Parse BELIEF_UPDATEs → update SuspicionState
│   │   │   ├── Apply overconfidence gate
│   │   │   ├── belief_graph.record_discussion()
│   │   │   ├── temporal_checker.check_message()
│   │   │   ├── Check redirects + evasion
│   │   │   └── Log + print
│   │   ├── Set phase = DAY_VOTE
│   │   ├── _sync_vote_guidance()
│   │   ├── _collect_votes() → warnings
│   │   ├── tally_votes()
│   │   ├── IF TIE: tie-break protocol
│   │   │   ├── Defence phase (tied players speak)
│   │   │   └── Decisive re-vote (non-tied players)
│   │   ├── IF DETECTIVE ELIMINATED: reveal window → re-vote
│   │   ├── Print vote tally
│   │   ├── eliminate_player()
│   │   └── reset_round_state()
│   │
│   ├── check_win_condition()
│   │
│   ├── _run_night_phase()
│   │   ├── Set phase = NIGHT
│   │   ├── narrator.announce(night)
│   │   ├── FOR each alive Mafia:
│   │   │   ├── Get teammate actions/reasonings (Syndicate)
│   │   │   ├── agent.choose_night_kill() → target
│   │   │   └── Record as final_kill
│   │   ├── IF Detective alive:
│   │   │   ├── agent.choose_investigation_target() → target
│   │   │   └── Record finding (true role)
│   │   ├── IF Doctor alive:
│   │   │   ├── agent.choose_protection_target() → target
│   │   │   └── Record protection
│   │   ├── apply_night_actions() → (killed, protected?)
│   │   └── narrator.announce(night_result)
│   │
│   └── increment round_number
│
├── print_game_over()
└── record_game_outcome()
```

**Vote Parsing Priority:**
1. Explicit `VOTE:` tag
2. Intent phrases ("voting for", "staying on", "locking in on")
3. Last mentioned valid player name
4. Self-votes always filtered to None

**Fallback Methods:**

| Method                    | Trigger                  | Behaviour                                     |
|---------------------------|--------------------------|------------------------------------------------|
| `_fallback_discussion()`  | Discussion API failure   | "I'll listen for now"                         |
| `_fallback_vote()`        | Vote API failure         | Highest-suspicion from belief state           |
| `_fallback_night_kill()`  | Kill API failure         | Lowest-suspicion Town (biggest threat)         |
| `_fallback_investigation()` | Investigation failure  | Most suspicious unchecked player              |
| `_fallback_protection()`  | Protection failure       | Self if suspicion > 0.3, else random ally     |

---

#### `engine/game_log.py` (176 lines)

**Purpose:** Terminal rendering with ANSI colour codes.

**ANSI Constants:** `RED`, `YELLOW`, `GREEN`, `BLUE`, `CYAN`, `WHITE`, `BOLD`, `DIM`, `ITALIC`, `RESET`

**Role Colour Mapping:**
| Role      | Colour         |
|-----------|----------------|
| Mafia     | RED            |
| Detective | YELLOW         |
| Doctor    | GREEN          |
| Villager  | BLUE           |
| Narrator  | WHITE + BOLD   |

**Functions:**

| Function                     | Purpose                                           |
|------------------------------|---------------------------------------------------|
| `print_game_banner(players)` | Title with player list                            |
| `print_model_archetype_table(assignments)` | Player/model/archetype/personality table |
| `print_phase_header(phase, round)` | Centred phase announcement                  |
| `print_agent_action(...)` | Coloured box with reasoning + action                 |
| `print_vote_tally(votes, result, weighted, warnings)` | Vote display + warnings |
| `print_night_result(killed, protected, role)` | Night outcome                     |
| `print_game_over(winner, game_state)` | Final results with all roles             |

---

### Agent Layer

#### `agents/base.py` (596 lines)

**Purpose:** Shared utilities for all agent types — streaming, parsing, retry, error recovery.

**Key Functions:**

| Function                                    | Purpose                                                        |
|---------------------------------------------|----------------------------------------------------------------|
| `format_discussion_prompt(history, name)`   | Formats discussion history (excludes agent's own messages)     |
| `format_vote_prompt(...)`                   | Vote prompt with exact output requirements                     |
| `parse_reasoning_action(text)`              | Splits on `ACTION:` marker, strips leaked reasoning            |
| `run_agent_stream(agent, prompt, session, prefer_non_stream)` | Core agent call with full error recovery |
| `_extract_tool_result(text)`                | Extracts `VOTE:` or `TARGET:` from response                   |
| `_serialize_agent_response(response)`       | Converts AgentResponse to parseable text                       |
| `_collapse_repeated_passage(text, min)`     | Collapses streaming retry duplicates                           |
| `_recursive_strip_marker(text, marker)`     | Removes all case-insensitive marker occurrences                |

**`run_agent_stream()` Flow:**
1. Acquire rate limiter semaphore
2. Attempt streaming call via `agent.run()` or `agent.run_stream()`
3. Parse response → check for refusal patterns
4. Check for empty action → retry
5. Check corporate-speak → retry with hint
6. On session expiration → rebuild from InMemoryHistoryProvider
7. On unrecoverable error → call `_handle_api_error()`
8. Return `(reasoning, action, refreshed_session)`

**Constants:**
| Constant             | Value | Purpose                                  |
|----------------------|-------|------------------------------------------|
| `_VOTE_BAN_REMINDER` | str   | Runtime reminder against vote-in-discussion |
| `_REFUSAL_PATTERNS`  | list  | Regex patterns for content-filter refusals |
| `_MAX_RETRIES`       | 2     | Max retry attempts                        |
| `MAX_REASONING_CHARS`| 500   | Display truncation limit                  |

---

#### `agents/belief_state.py` (650+ lines)

**Purpose:** Structured belief tracking, scum-tell detection, temporal consistency.

**Classes:**

**`SuspicionState`** — Per-agent suspicion tracker:
| Method                            | Purpose                                             |
|-----------------------------------|-----------------------------------------------------|
| `initialize(names, num_mafia)`    | Uniform prior                                       |
| `update(name, probability)`       | Clamped to [0.01, 0.99]                             |
| `check_staleness()`               | Detects belief loops (unchanged >2 rounds)           |
| `is_frustrated`                   | True if stale too long                              |
| `get_certainty(name)`             | Returns suspicion for player                        |
| `get_top_suspect()`               | (name, probability) of highest suspicion            |
| `remove_player(name)`             | Removes eliminated player                           |
| `summary()`                       | Text for prompt injection                           |
| `should_reveal_identity(...)`     | Last Stand Protocol check                                 |
| `get_last_stand_level(...)`       | Returns "soft_hint", "hard_claim", "full_reveal", None |

**`BeliefGraph`** — Scum-tell detector:
| Method                             | Purpose                                             |
|------------------------------------|-----------------------------------------------------|
| `record_discussion(name)`          | Track who spoke                                     |
| `get_quiet_players(alive, thresh)` | Speakers below threshold                            |
| `check_evasion(...)`               | Flag question dodging                               |
| `check_late_bandwagon(...)`        | Flag thin vote agreements                           |
| `check_redirect(...)`              | Flag deflection from consensus target               |
| `check_instahammer(...)`           | Flag early decisive votes                           |
| `get_flags_for_prompt()`           | Formatted flags for injection                       |
| `reset_round()`                    | Clear per-round tracking                            |

**`TemporalConsistencyChecker`** — Temporal slip detector:
| Method                          | Purpose                                                |
|---------------------------------|--------------------------------------------------------|
| `check_message(name, text, round)` | Detect impossible temporal references                |
| `get_slips_for_prompt()`        | Formatted warnings for injection                      |

**Constants:**
| Constant                       | Value  | Purpose                               |
|--------------------------------|--------|----------------------------------------|
| `SELF_PRESERVATION_THRESHOLD`  | 0.45   | Last Stand Protocol base threshold           |
| `LAST_STAND_SOFT_HINT_THRESHOLD`     | 0.35   | Graduated: soft hint                   |
| `LAST_STAND_HARD_CLAIM_THRESHOLD`    | 0.45   | Graduated: hard claim                  |
| `LAST_STAND_FULL_REVEAL_THRESHOLD`   | 0.55   | Graduated: full reveal                 |
| `LAST_STAND_RED_CHECK_ADJUSTMENT`    | 0.10   | Lower thresholds when holding red check |
| `STRONG_EVIDENCE_THRESHOLD`    | 0.15   | Overconfident/Stubborn update threshold |
| `WEAK_EVIDENCE_THRESHOLD`      | 0.05   | Volatile/Reactive update threshold     |

---

#### `agents/providers.py` (217 lines)

**Purpose:** MAF `ContextProvider` implementations that inject dynamic per-turn context.

**`BeliefStateProvider`** (ContextProvider):
Injects before every agent call:
- Suspicion state summary
- Archetype override note
- Vote format reinforcement (if previous parse failed)
- Scum-tell flags from BeliefGraph
- Temporal slip warnings
- Last Stand Protocol graduated reveal instructions
- Vote coordination note (during DAY_VOTE phase)

**`CrossGameMemoryProvider`** (ContextProvider):
Injects before every agent call:
- 5 most recent role-specific learnings
- 5 most recent global patterns

---

#### `agents/middleware.py` (541 lines)

**Purpose:** MAF middleware stack — session resilience, rate limiting, output processing.

See [Middleware Architecture](#middleware-architecture) for full details.

---

#### `agents/rate_limiter.py` (194 lines)

**Purpose:** Two-tier rate limiting with exponential backoff.

**Global Semaphore:** `get_global_semaphore()` returns a lazy-initialised `asyncio.Semaphore` with limit `MAFIA_MAX_CONCURRENT_CALLS` (default 5, max 10).

**Error Classification:**

| Error Type        | Action         | Reasoning                           |
|-------------------|----------------|-------------------------------------|
| 429 Rate Limit    | Retry          | Temporary; backoff resolves it      |
| 5xx Server Error  | Fail fast      | Server-side issue; retry unlikely to help |
| Timeout           | Retry once     | Network transience                  |
| Other             | Raise          | Unexpected error                    |

**Backoff:** Exponential with jitter — base 1s, multiplier 2×, cap 8s, random jitter ±0.5s.

**`RetryStats`:** Tracks total calls, retries, failures, and average backoff delay per session.

---

#### `agents/game_tools.py` (40 lines)

**Purpose:** MAF `@tool`-decorated functions for structured game actions.

```python
@tool(approval_mode="never_require")
def cast_vote(target: str, reasoning: str) -> str:
    """Cast a vote to eliminate a player during the day phase."""
    return f"VOTE: {target}"

@tool(approval_mode="never_require")  
def choose_target(target: str, reasoning: str) -> str:
    """Choose a target for a night action."""
    return f"TARGET: {target}"
```

---

#### `agents/memory.py` (188 lines)

**Purpose:** Persistent cross-game memory.

**`Learning`** (dataclass):
| Field          | Type   | Purpose                          |
|----------------|--------|----------------------------------|
| `insight`      | str    | What was learned                 |
| `context`      | str    | Situation that produced it       |
| `role`         | str    | Which role generated it          |
| `round_number` | int    | When in the game                 |
| `outcome`      | str    | "correct" \| "incorrect" \| "unknown" |
| `timestamp`    | str    | ISO 8601                         |

**`GameMemoryStore`:**
| Method                         | Purpose                                      |
|--------------------------------|----------------------------------------------|
| `load()`                       | Load from disk                               |
| `save()`                       | Persist to JSON (max 50 per role)            |
| `add_learning(learning)`       | Record new learning                          |
| `get_memory_prefix(role)`      | Formatted prompt text (5 role + 5 global)    |
| `record_game_outcome(...)`     | Auto-record game result                      |

---

#### `agents/summary.py`

**Purpose:** SummaryAgent generates low-cognitive-load narrative summaries per phase.

**Key Methods:**
| Method                            | Purpose                                               |
|-----------------------------------|-------------------------------------------------------|
| `summarize(game_state)`           | Bulleted narrative: round, alive, eliminations, target, evidence, next action |
| `compress_discussion_history(history, game_state)` | Progressive compression (full → summarised → eliminations-only) |
| `_get_current_target(entries, alive, round)` | Recency-weighted target (1.0 / 0.3 / 0.05) |
| `_get_recent_eliminations(state)` | Most recent eliminated player with role               |
| `_get_main_evidence(entries)`     | Strongest evidence from discussion                    |
| `_get_vote_summary(votes)`        | Compact tally                                        |
| `_summarize_key_accusations(entries)` | Extract accusations from older rounds             |

**Progressive Compression:**
| Round | Compression Level                                     |
|-------|-------------------------------------------------------|
| 1–2   | Full history                                         |
| 3–4   | Summarised key accusations + full current round       |
| 5+    | Only eliminations, role reveals, current round        |

---

#### Role-Specific Agent Wrappers

All four player roles follow the same pattern:

```python
class [Role]Agent:
    role = "[Role]"
    name: str
    archetype: str
    personality: str
    agent: Agent        # MAF Agent instance
    session: AgentSession

    def __init__(self, name, archetype, personality, client):
        # Build system prompt via prompts/builder.py
        # Register ContextProviders: BeliefStateProvider, CrossGameMemoryProvider, InMemoryHistoryProvider
        # Register Middleware (5 layers)
        # Register Tools: cast_vote [+ choose_target for power roles]
        # Set SlidingWindowStrategy compaction (keep_last_groups=20)
```

**Role-specific differences:**

| Role       | Extra State                        | Night Action                          | Special Methods                     |
|------------|------------------------------------|---------------------------------------|-------------------------------------|
| Mafia      | `partner_names`, `last_night_reasoning` | `choose_night_kill()` with Syndicate channel | Stores reasoning for next night |
| Detective  | `findings`, `reveal_vote_used`     | `choose_investigation_target()`       | `reveal_vote_window()`             |
| Doctor     | `last_protected`                   | `choose_protection_target()`          | No-repeat constraint               |
| Villager   | (none)                             | (none)                                | (none)                             |

---

### Prompt Layer

#### `prompts/builder.py` (300+ lines)

**Purpose:** System prompt assembler — 9-layer construction.

**Framework Routing Tables:**

| Table                             | Maps                            | Purpose                                    |
|-----------------------------------|---------------------------------|--------------------------------------------|
| `ARCHETYPE_FRAMEWORK_EXTRAS`      | archetype → framework names     | Extra reasoning lenses per archetype       |
| `PERSONALITY_FRAMEWORK_EXTRAS`    | personality → framework names   | Extra lenses per personality               |
| `ROLE_ARCHETYPE_FRAMEWORK_EXTRAS` | (role, archetype) → frameworks  | Role-specific archetype boosts             |
| `ROLE_PERSONALITY_FRAMEWORK_EXTRAS`| (role, personality) → frameworks | Role-specific personality boosts          |

**Build Functions:**

| Function                     | Output                                |
|------------------------------|---------------------------------------|
| `build_mafia_prompt(...)`    | Complete Mafia system prompt          |
| `build_detective_prompt(...)` | Complete Detective system prompt     |
| `build_doctor_prompt(...)`   | Complete Doctor system prompt         |
| `build_villager_prompt(...)` | Complete Villager system prompt        |
| `build_narrator_prompt()`   | Simple omniscient narrator prompt      |

---

#### `prompts/frameworks.py` (300+ lines)

**9 Reasoning Frameworks:**

| Framework          | Core Teaching                                                   |
|--------------------|-----------------------------------------------------------------|
| Game Theory        | Threat assessment, information asymmetry, timing                |
| Sun Tzu            | Deception economy, intelligence targeting                       |
| Machiavelli        | Coalition building, appearance as reality                       |
| Carnegie Execution | Social influence, indirect persuasion                           |
| Carnegie Villager  | Trust-building, people-reading                                  |
| Behavioural Psych  | Cognitive biases: anchoring, herding, overconfidence             |
| Strategic Glossary | Mafia terminology (Busing, Wagon, Instahammer)                  |
| Incentive Reasoning| Who benefits from each elimination?                             |
| Self-Critique      | Tunneling check, circular reasoning check                       |

---

#### `prompts/archetypes.py` (500+ lines)

**13 Archetypes** with per-archetype:
- `strategy_modifier` — How reasoning deviates
- `voice.prohibited` — Phrases never used
- `voice.register` — How this archetype sounds
- `voice.examples` — 2–3 example phrases

**Global Constraints:**
| Constant               | Purpose                                    | Size    |
|------------------------|--------------------------------------------|---------|
| `NEGATIVE_CONSTRAINTS` | Banned AIism phrases                       | 80+     |
| `CORPORATE_WORDS`      | Words triggering corporate-speak retry     | 20      |
| `GENZ_REGISTER`        | Gen Z + MLE slang terms                    | ~80     |
| `ANTI_AI_STRUCTURE`    | Structural anti-patterns to avoid          | 11      |
| `GROUNDING_CONSTRAINT` | First-person perspective requirement       | 1       |
| `CONVERSATIONAL_RULE`  | Conversation flow rules                    | 8       |
| `CORPORATE_PENALTY`    | Runtime penalty for boardroom language     | 1       |

---

#### `prompts/personalities.py` (200+ lines)

**8 Personalities** with per-personality:
- `register` — Energy, cadence, sentence rhythm
- `voice_markers` — `sentence_length`, `evidence_relationship`, `deflection_style`
- `prohibited` — Phrases never used
- `examples` — 5 in-game dialogue lines
- `when_accused` — 3 lines for when directly targeted
- `late_game_shift` — Behaviour change in rounds 3+
- `role_note` — Mafia vs Town differences
- `performance_note` — How personality wraps archetype strategy

---

### Config Layer

#### `config/model_registry.py` (101 lines)

**`ModelConfig`** (frozen dataclass):
| Field     | Type | Purpose                              |
|-----------|------|--------------------------------------|
| `name`    | str  | Display name ("GPT-4O-MINI")        |
| `model_id`| str  | Deployment name in Foundry           |
| `short`   | str  | 3-char label for tables              |

**Key Functions:**
| Function               | Purpose                                           |
|------------------------|---------------------------------------------------|
| `make_client(model)`   | Returns `FoundryChatClient` with AzureCliCredential |
| `validate_environment()` | Checks for required env vars                    |

---

#### `config/settings.py` (71 lines)

**Environment Variables:**

| Variable                           | Default | Max  | Purpose                           |
|------------------------------------|---------|------|-----------------------------------|
| `FOUNDRY_PROJECT_ENDPOINT`         | (req.)  | —    | Azure AI Foundry endpoint         |
| `FOUNDRY_MODEL`                    | gpt-5.4-mini | — | Model deployment name            |
| `MAFIA_MAX_CONCURRENT_CALLS`       | 5       | 10   | Global semaphore limit            |
| `MAFIA_RATE_LIMIT_RETRIES`         | 3       | 5    | 429 retry attempts                |
| `MAFIA_BACKOFF_BASE_DELAY`         | 1.0     | —    | Exponential backoff base (sec)    |
| `MAFIA_ENABLE_STREAMING_FALLBACK`  | false   | —    | Retry streaming as non-streaming  |
| `MAFIA_DETECTIVE_VOTE_WEIGHT`      | 2       | 5    | Detective vote multiplier         |
| `MAFIA_VOTE_CONFIDENCE_THRESHOLD`  | 0.45    | —    | Min certainty for belief override |
| `MAFIA_CONSENSUS_SHORTLIST_SIZE`   | 3       | 5    | Pressure wagon size               |
| `MAFIA_EVASION_BONUS`              | 0.08    | —    | Suspicion boost for evasion       |
| `MAFIA_SESSION_IDLE_THRESHOLD`     | 20.0    | —    | Proactive refresh idle time (sec) |
| `MAFIA_SESSION_REFRESH_THRESHOLD`  | 25.0    | —    | Cumulative backoff refresh (sec)  |

---

### Test Suite

**File:** `tests/test_refactor.py`  
**Lines:** 3,718  
**Tests:** 329  
**Classes:** 84  

All tests run without Azure credentials or model deployments:

```bash
python -m unittest tests.test_refactor -v
```

**Coverage Areas:**

| Category                    | Classes | Tests | Key Coverage                                           |
|-----------------------------|---------|-------|--------------------------------------------------------|
| Vote parsing                | 3       | 16    | Self-vote prevention, intent parsing, tie-break logic  |
| Personality constraints     | 7       | 24    | Tier 1/2/3 bans, frequency caps, cap relaxation       |
| Action parsing              | 4       | 16    | REASONING/ACTION split, tool trace normalisation       |
| Belief state                | 7       | 35    | Suspicion, staleness, Last Stand Protocol, overconfidence    |
| Session resilience          | 8       | 30    | Session recovery, rate limiting, health monitoring     |
| Game state                  | 7       | 40    | Win conditions, elimination, night actions, voting     |
| Prompt structure            | 5       | 18    | Discussion rules, voice markers, slang register        |
| Memory                      | 1       | 5     | GameMemoryStore load/save/inject                       |
| Summary                     | 6       | 20    | Recency weighting, compression, evidence extraction    |
| Rate limiter                | 3       | 12    | Error classification, backoff, retry stats             |
| Game manager                | 5       | 18    | Archetype/personality picks, player names, tools       |
| Settings                    | 2       | 8     | Environment parsing, configuration validation          |
| Framework/archetype/personality | 3   | 10    | Structure validation, framework routing                |
| MAF API compliance          | 4       | 11    | Import paths, dependency versions, provider signatures |
| Middleware                  | 2       | 8     | Registration, class hierarchy                          |
| Other                       | 17      | 61    | Discussion formatting, narrator, console setup, etc.   |

---

## Data Flow Diagrams

### Agent Call Flow

```
Orchestrator
    │
    ├── _sync_provider_state()
    │       ├── BeliefStateProvider.state ← suspicion + archetype + graph + temporal
    │       └── CrossGameMemoryProvider.state ← store + role
    │
    ├── agent.day_discussion() / cast_vote() / choose_*()
    │       │
    │       └── run_agent_stream(agent, prompt, session)
    │               │
    │               ├── rate_limited_call() → acquire semaphore
    │               │
    │               ├── agent.run(session) or agent.run_stream(session)
    │               │       │
    │               │       └── Middleware chain (outermost → innermost):
    │               │               1. ResilientSessionMiddleware
    │               │               2. RateLimitMiddleware
    │               │               3. corporate_speak_middleware
    │               │               4. ReasoningActionMiddleware
    │               │               5. BeliefUpdateMiddleware
    │               │               │
    │               │               └── ContextProviders inject before call:
    │               │                       ├── BeliefStateProvider
    │               │                       └── CrossGameMemoryProvider
    │               │
    │               ├── parse_reasoning_action() → (reasoning, action)
    │               ├── Check refusal / empty / corporate → retry
    │               └── Return (reasoning, action, session)
    │
    ├── Parse BELIEF_UPDATEs → update SuspicionState
    ├── Apply overconfidence gate
    ├── Record to BeliefGraph + TemporalChecker
    └── Log + display
```

### Belief State Flow

```
Initialisation          Turn N                    Turn N+1
─────────────          ──────                    ────────
Uniform prior  →  Agent reasons  →  BELIEF_UPDATE parsed  →  SuspicionState updated
(num_mafia /       in REASONING      from reasoning text       │
 num_alive)        block                                       ├→ Staleness check
                                                               ├→ Overconfidence gate
                                                               ├→ Vote recommendation
                                                               └→ Last Stand Protocol check
```

---

## Middleware Architecture

### Registration Order

Every player agent registers middleware in this exact order:

```python
agent.add_middleware(ResilientSessionMiddleware())      # 1 — outermost
agent.add_middleware(RateLimitMiddleware())              # 2
agent.add_middleware(corporate_speak_middleware)          # 3
agent.add_middleware(ReasoningActionMiddleware())         # 4
agent.add_middleware(BeliefUpdateMiddleware())            # 5 — innermost
```

### Execution Order

MAF middleware executes outermost-first for pre-processing, innermost-first for post-processing:

```
Request  →  ResilientSession  →  RateLimit  →  corporate_speak  →  ReasoningAction  →  BeliefUpdate  →  Agent
Response ←  ResilientSession  ←  RateLimit  ←  corporate_speak  ←  ReasoningAction  ←  BeliefUpdate  ←  Agent
```

### Middleware Details

| Middleware                  | Type       | Pre-Call                           | Post-Call                                    |
|-----------------------------|-----------|-------------------------------------|----------------------------------------------|
| `ResilientSessionMiddleware` | Class     | (none)                             | Catches `previous_response_not_found`, rebuilds session |
| `RateLimitMiddleware`        | Class     | Check idle time, proactive refresh | Catches 429, exponential backoff + retry     |
| `corporate_speak_middleware` | Decorator | (none)                             | Check for 3+ corporate words, re-invoke      |
| `ReasoningActionMiddleware`  | Class     | (none)                             | Parse REASONING/ACTION, store on metadata    |
| `BeliefUpdateMiddleware`     | Class     | (none)                             | Extract BELIEF_UPDATE tags, store on metadata |

---

## Context Provider Architecture

### BeliefStateProvider

**Source ID:** `"belief"`

**Injected Content (per turn):**

```
[Current suspicion state summary]
[Archetype override note: "Your archetype is X — this shapes your reasoning, not your goals"]
[Vote format reinforcement if previous parse failed]
[Scum-tell flags from BeliefGraph]
[Temporal slip warnings from TemporalConsistencyChecker]
[Last Stand Protocol instructions if suspicion exceeds threshold]
[Vote coordination note if in DAY_VOTE phase]
```

### CrossGameMemoryProvider

**Source ID:** `"memory"`

**Injected Content (per turn):**

```
[Up to 5 role-specific learnings from previous games]
[Up to 5 global pattern observations from previous games]
```

### State Synchronisation

Before every agent call, the orchestrator calls `_sync_provider_state()` which populates `session.state` with:

```python
session.state["belief"] = {
    "suspicion": SuspicionState,
    "archetype": str,
    "graph": BeliefGraph,
    "temporal": TemporalConsistencyChecker,
    "all_beliefs": dict[str, SuspicionState],
    "role": str,
    "name": str,
    "phase": GamePhase,
    "vote_shortlist": list[str],
    "recommended_vote": str,
    "evasion_scores": dict[str, int],
    "vote_parse_failures": dict[str, int],
    "findings": dict[str, str],  # Detective only
}

session.state["memory"] = {
    "store": GameMemoryStore,
    "role": str,
}
```

---

## Concurrency Model

### Global Rate Limiting

A single `asyncio.Semaphore` limits concurrent API calls across all agents:

```python
_semaphore = asyncio.Semaphore(MAFIA_MAX_CONCURRENT_CALLS)  # default 5
```

### Sequential Agent Execution

Within each phase, agents execute **sequentially**, not in parallel. This is deliberate:
- Discussion must be sequential (each agent sees prior speakers)
- Voting is sequential (vote coordination depends on current tallies)
- Night actions are sequential (Mafia coordination depends on partner choice)

The semaphore prevents concurrent calls only across retry attempts and session recovery operations, not across agents.

### Async Architecture

The entire game loop is `async`:
- `main()` → `asyncio.run()`
- `run_one_game()` → `await orchestrator.run_game()`
- `run_game()` → `await _run_day_phase()` → `await agent.day_discussion()`
- Each agent call → `await run_agent_stream()` → `await agent.run(session)`

---

## Error Handling Architecture

### Four-Phase Recovery

```
Phase 1: Streaming retry (2× max)
    ├── Corporate-speak detection → retry with slang hint
    ├── Refusal detection → retry with softened prompt
    └── Empty action → retry
    
Phase 2: Non-streaming fallback
    └── If streaming fails, try agent.run() instead of run_stream()
    
Phase 3: Session reconstruction
    ├── Detect "previous_response_not_found"
    ├── Extract history from InMemoryHistoryProvider
    ├── Create fresh AgentSession with transferred state
    ├── Inject compressed history summary
    └── Retry call
    
Phase 4: Graceful degradation
    └── Per-phase fallback methods (belief-based defaults)
```

### Error Classification

| Error                           | Response                          |
|---------------------------------|-----------------------------------|
| Content filter refusal          | Retry with softened prompt        |
| Empty action                    | Retry with explicit format hint   |
| Corporate-speak                 | Retry with slang enforcement      |
| 429 Rate limit                  | Exponential backoff + retry       |
| Session expired                 | Rebuild from local history        |
| 5xx Server error                | Fail fast → graceful degradation  |
| DeploymentNotFound              | Fatal error (bad configuration)   |
| Timeout                         | Retry once → graceful degradation |

---

## Dependencies

### Runtime Dependencies

| Package                     | Version      | Purpose                              |
|-----------------------------|--------------|--------------------------------------|
| `agent-framework-foundry`   | ≥1.0.0,<2.0  | Microsoft Agent Framework            |
| `azure-identity`            | latest       | Azure authentication (AzureCliCredential) |
| `python-dotenv`             | latest       | .env file loading                    |

### Standard Library Usage

| Module       | Used For                                        |
|--------------|-------------------------------------------------|
| `asyncio`    | Async game loop, semaphore, event loop          |
| `re`         | Regex parsing (votes, beliefs, refusals)        |
| `logging`    | Application logging                             |
| `json`       | Memory persistence, state serialisation         |
| `pathlib`    | File path management                            |
| `dataclasses`| All data model classes                          |
| `enum`       | GamePhase enum                                  |
| `datetime`   | Timestamps for log entries and learnings        |
| `hashlib`    | Session ID generation                           |
| `sys`        | Console encoding, exit codes                    |
| `os`         | Environment variable access                     |
| `random`     | Game randomisation (roles, archetypes, etc.)    |

### No API Keys

The system uses `AzureCliCredential` exclusively — no API keys are stored in code, environment variables, or configuration files. Authentication is handled via `az login`.
