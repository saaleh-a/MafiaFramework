# MafiaFramework

An AI-powered simulation of the social deduction game **Mafia**, built on the [Microsoft Agent Framework (MAF)](https://github.com/microsoft/agent-framework) and [Azure AI Foundry](https://azure.microsoft.com/products/ai-studio). Seven AI agents — each with a unique personality archetype, randomly assigned role, and independently chosen language model — play a full game of Mafia against each other in the terminal.

---

## Table of Contents

- [Overview](#overview)
- [How the Game Works](#how-the-game-works)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Usage](#usage)
- [Configuration](#configuration)
- [Archetypes](#archetypes)
- [Prompt Engineering](#prompt-engineering)
- [Troubleshooting](#troubleshooting)

---

## Overview

MafiaFramework simulates full Mafia games where every participant is an LLM agent. Each game randomises three independent dimensions:

| Dimension     | Description                                                        |
|---------------|--------------------------------------------------------------------|
| **Role**      | Mafia (×2), Detective, Doctor, Villager (×3) — shuffled each game  |
| **Model**     | Each agent is backed by a randomly selected Azure model deployment |
| **Archetype** | One of 13 personality archetypes that shape strategy and voice     |

The same player name gets a different combination every game, producing emergent and unpredictable social dynamics.

---

## How the Game Works

A standard Mafia game loop runs as follows:

### Day Phase
1. **Discussion** — All alive players speak (two rounds of shuffled speaking order). Each agent sees the public game state and the conversation so far.
2. **Voting** — Each player votes to eliminate one other player. A simple plurality eliminates the target; ties result in no elimination.

### Night Phase
3. **Mafia Kill** — The two Mafia agents independently choose a kill target. The second Mafia member sees the first's preference as a coordination hint.
4. **Detective Investigation** — The Detective chooses one player to investigate, learning whether they are Mafia or Innocent.
5. **Doctor Protection** — The Doctor chooses one player to protect (cannot repeat the same player two nights in a row). If the Mafia's target matches the Doctor's protection, the kill is blocked.
6. **Dawn** — Night actions resolve. The Narrator announces the result.

### Win Conditions
- **Town wins** when all Mafia members are eliminated.
- **Mafia wins** when Mafia members equal or outnumber Town players.

An impartial **Narrator** agent (with omniscient knowledge of all roles) announces phase transitions dramatically without leaking hidden information.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                       main.py                           │
│            CLI, argument parsing, game loop              │
└──────────────────────┬──────────────────────────────────┘
                       │
         ┌─────────────▼─────────────┐
         │   engine/game_manager.py  │
         │   Role/Model/Archetype    │
         │   randomisation & setup   │
         └─────────────┬─────────────┘
                       │
         ┌─────────────▼─────────────┐
         │  engine/orchestrator.py   │
         │  Game loop: day/night     │
         │  phases, win detection    │
         └──┬──────────┬─────────┬───┘
            │          │         │
    ┌───────▼──┐ ┌─────▼───┐ ┌──▼────────┐
    │  agents/ │ │ engine/ │ │  prompts/ │
    │ Per-role │ │ State,  │ │ Frameworks│
    │ AI agent │ │ logging │ │ Archetypes│
    │ classes  │ │ display │ │ Builder   │
    └──────────┘ └─────────┘ └───────────┘
            │
    ┌───────▼──────────┐
    │ config/          │
    │ model_registry   │
    │ settings         │
    └──────────────────┘
```

### Key Components

| Module                       | Purpose                                                                                      |
|------------------------------|----------------------------------------------------------------------------------------------|
| `main.py`                    | Entry point. Parses CLI args, validates environment, runs one or more games.                  |
| `engine/orchestrator.py`     | Game loop — runs day discussion, voting, night actions, and win-condition checks.             |
| `engine/game_manager.py`     | Randomises roles, models, and archetypes. Instantiates all agents and builds game state.     |
| `engine/game_state.py`       | Core data model: player state, phase tracking, vote tallying, night action resolution.       |
| `engine/game_log.py`         | Terminal renderer with ANSI colours — banners, agent action boxes, vote tallies, results.    |
| `agents/base.py`             | Shared utilities: response parsing (`REASONING:`/`ACTION:`) and streaming with error handling.|
| `agents/narrator.py`         | Narrator agent — omniscient, impartial game master announcements.                            |
| `agents/mafia.py`            | Mafia agent — day discussion, voting, and night kill target selection.                       |
| `agents/detective.py`        | Detective agent — day discussion, voting, and night investigation.                           |
| `agents/doctor.py`           | Doctor agent — day discussion, voting, and night protection.                                 |
| `agents/villager.py`         | Villager agent — day discussion and voting (no special night ability).                       |
| `config/model_registry.py`   | Model pool definition and Azure Foundry client factory.                                      |
| `config/settings.py`         | Loads environment variables for the Foundry endpoint and default model.                      |
| `prompts/builder.py`         | Assembles system prompts from role goals, frameworks, archetype modifiers, and voice profiles.|
| `prompts/frameworks.py`      | Reusable reasoning frameworks: game theory, Sun Tzu, Machiavelli, Carnegie, behavioural psych.|
| `prompts/archetypes.py`      | 13 personality archetypes with strategy modifiers and distinctive voice profiles.             |
| `check.py`                   | Pre-flight connectivity check — verifies Azure Foundry setup before running a game.          |

---

## Project Structure

```
MafiaFramework/
├── main.py                     # Entry point
├── check.py                    # Connectivity pre-check
├── requirements.txt            # Python dependencies
├── agents/
│   ├── __init__.py
│   ├── base.py                 # Shared streaming + parsing utilities
│   ├── narrator.py             # Narrator agent
│   ├── mafia.py                # Mafia agent
│   ├── detective.py            # Detective agent
│   ├── doctor.py               # Doctor agent
│   └── villager.py             # Villager agent
├── engine/
│   ├── __init__.py
│   ├── game_manager.py         # Game setup & randomisation
│   ├── game_state.py           # State model & game logic
│   ├── orchestrator.py         # Game loop controller
│   └── game_log.py             # Terminal display / ANSI rendering
├── config/
│   ├── __init__.py
│   ├── settings.py             # Environment variable loader
│   └── model_registry.py       # Model pool & client factory
└── prompts/
    ├── __init__.py
    ├── builder.py              # System prompt assembler
    ├── frameworks.py           # Reasoning framework text blocks
    └── archetypes.py           # Personality archetype definitions
```

---

## Prerequisites

- **Python 3.12+**
- **Azure CLI** (`az`) — [Install guide](https://learn.microsoft.com/cli/azure/install-azure-cli)
- **Azure AI Foundry project** with at least one deployed model (e.g. `gpt-4o-mini`)
- An active `az login` session with access to the Foundry project

---

## Setup

1. **Clone the repository:**

   ```bash
   git clone https://github.com/saaleh-a/MafiaFramework.git
   cd MafiaFramework
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

   This installs:
   - `agent-framework-foundry` — Microsoft Agent Framework with Azure Foundry integration
   - `azure-identity` — Azure authentication (supports `az login`)
   - `python-dotenv` — `.env` file loading

3. **Authenticate with Azure:**

   ```bash
   az login
   ```

4. **Configure environment variables:**

   Create a `.env` file in the project root:

   ```dotenv
   FOUNDRY_PROJECT_ENDPOINT=https://<your-resource>.services.ai.azure.com
   FOUNDRY_MODEL=gpt-4o-mini
   FOUNDRY_MODEL_4O=gpt-4o
   ```

   | Variable                   | Required | Description                                                    |
   |----------------------------|----------|----------------------------------------------------------------|
   | `FOUNDRY_PROJECT_ENDPOINT` | Yes      | Your Azure AI Foundry project endpoint URL                     |
   | `FOUNDRY_MODEL`            | No       | Default model deployment name (defaults to `gpt-4o-mini`)      |
   | `FOUNDRY_MODEL_4O`         | No       | Secondary model deployment name (defaults to `FOUNDRY_MODEL`)  |

   > **Important:** The model names must exactly match deployed model names in your Azure AI Foundry project.

5. **Verify connectivity:**

   ```bash
   python check.py
   ```

   Expected output: `SETUP OK` followed by `✓ Ready to run the game.`

---

## Usage

### Run a single game

```bash
python main.py
```

### Command-line options

| Flag              | Description                                          |
|-------------------|------------------------------------------------------|
| `--reveal-roles`  | Show all role assignments (including hidden roles) at the start |
| `--debug`         | Show full agent reasoning without truncation         |
| `--quiet`         | Show action lines only, hide reasoning               |
| `--seed <int>`    | Set random seed for reproducible role/model/archetype assignment |
| `--games <int>`   | Run multiple games and print aggregate win statistics |

### Examples

```bash
# Show all role assignments for debugging
python main.py --reveal-roles

# Full reasoning output (no truncation)
python main.py --debug

# Run 10 games with a fixed seed and print win rates
python main.py --games 10 --seed 42

# Minimal output - actions only
python main.py --quiet
```

### Output Format

Each agent's turn is displayed in a coloured box:

```
┌─ [Alice | Villager | Paranoid] ──────────────────┐
│ REASONING:                                        │
│   (internal thinking - hidden from other agents)  │
│                                                    │
│ ACTION:                                            │
│   Something is wrong. Did anyone else notice that? │
└──────────────────────────────────────────────────-─┘
```

Roles are colour-coded:
- 🔴 **Mafia** — Red
- 🟡 **Detective** — Yellow
- 🟢 **Doctor** — Green
- 🔵 **Villager** — Blue
- ⚪ **Narrator** — White/Bold

---

## Configuration

### Model Pool

Edit `config/model_registry.py` to add or remove models from the pool. Each game randomly assigns one model per player from `AVAILABLE_MODELS`:

```python
AVAILABLE_MODELS = [
    ModelConfig(name="GPT-4o-mini", model_id="gpt-4o-mini", short="4om"),
    ModelConfig(name="GPT-4o",      model_id="gpt-4o",      short="4o "),
]
```

> Every model in the pool **must** have a matching deployment in your Azure AI Foundry project. A missing deployment causes a `DeploymentNotFound` error at runtime.

### Player Names and Role Distribution

Edit `engine/game_manager.py` to change player names or role counts:

```python
PLAYER_NAMES = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace"]

ROLE_DISTRIBUTION = [
    "Mafia", "Mafia",
    "Detective",
    "Doctor",
    "Villager", "Villager", "Villager",
]
```

The number of player names must match the number of roles.

---

## Archetypes

Each player is assigned a random personality archetype that shapes both their strategic behaviour and their speaking voice. There are 13 archetypes defined in `prompts/archetypes.py`:

| Archetype       | Strategy Tendency                                                      | Availability   |
|-----------------|------------------------------------------------------------------------|----------------|
| **Paranoid**    | Perceives threats at twice the actual level                            | All roles      |
| **Overconfident** | First read is final; rarely updates on new information              | All roles      |
| **Impulsive**   | Acts on first instinct; occasionally brilliant, often premature       | All roles      |
| **Passive**     | Requires overwhelming evidence; acts a round later than optimal        | All roles      |
| **Reactive**    | Accusations override strategic calculation; easy to bait               | All roles      |
| **Contrarian**  | Instinctively questions strong consensus, even when correct            | All roles      |
| **Analytical**  | Closest to optimal play; failure mode is predictability                | Non-Villager   |
| **Methodical**  | Evidence-based but slow; anchors on early reads                        | Villager only  |
| **Diplomatic**  | Prioritises group harmony; softens accusations into suggestions        | All roles      |
| **Stubborn**    | Round-one read is load-bearing; treats counter-evidence as misdirection| All roles      |
| **Volatile**    | Position shifts with the last compelling thing heard                    | All roles      |
| **Manipulative**| Engineers group conclusions through leading questions                   | All roles      |
| **Charming**    | Builds specific, genuine-seeming warmth rapidly                        | All roles      |

Each archetype includes:
- A **strategy modifier** that changes how the agent deviates from optimal play
- A **voice profile** with register description, prohibited AI-writing patterns, and example phrases

The same archetype on different roles produces completely different gameplay. A Paranoid Mafia member behaves very differently from a Paranoid Villager.

---

## Prompt Engineering

Agent prompts are assembled in `prompts/builder.py` from layered components:

1. **Role Goal** — What winning looks like for this specific role
2. **Reasoning Frameworks** — Reusable strategic thinking modules:
   - **Game Theory** — Threat ranking, information asymmetry, timing
   - **Sun Tzu** — Deception, intelligence targeting, terrain awareness
   - **Machiavelli** — Political operation, coalition building, appearance management
   - **Carnegie (Execution)** — Social influence, indirect persuasion, challenge absorption
   - **Carnegie (Villager)** — People-reading, trust through interaction, social consensus
   - **Behavioural Psychology** — Cognitive biases, anchoring, loss aversion, narrative coherence
3. **Archetype Strategy Modifier** — Role-specific behavioural deviation
4. **Voice Profile** — Anti-AI-writing constraints and distinctive speaking register

Different roles receive different framework combinations:
- **Mafia**: Game Theory + Sun Tzu + Machiavelli + Carnegie Execution
- **Detective**: Game Theory + Sun Tzu + Social Cover
- **Doctor**: Game Theory + Sun Tzu
- **Villager**: Carnegie Villager + Behavioural Psychology

All agents output in a structured `REASONING:` / `ACTION:` format. Reasoning represents internal thinking; only the action is visible to other agents.

---

## Troubleshooting

### `DeploymentNotFound` (404 Error)

```
openai.NotFoundError: Error code: 404 - {'error': {'message': 'The API deployment
for this resource does not exist.', 'code': 'DeploymentNotFound'}}
```

**Cause:** The model deployment name in your `.env` file (or `model_registry.py`) does not match any active deployment in your Azure AI Foundry project.

**Fix:**
1. Open your Azure AI Foundry project and verify the exact deployment names.
2. Update `FOUNDRY_MODEL` and/or `FOUNDRY_MODEL_4O` in your `.env` to match.
3. If you only have one model deployed, just set `FOUNDRY_MODEL`; `FOUNDRY_MODEL_4O` will automatically fall back to it.
4. If you just created a deployment, wait approximately 5 minutes and retry.
5. Run `python check.py` to verify connectivity before starting a game.

### `FOUNDRY_PROJECT_ENDPOINT is not set`

**Cause:** The `.env` file is missing or does not contain `FOUNDRY_PROJECT_ENDPOINT`.

**Fix:** Create a `.env` file in the project root with your Foundry project endpoint:
```dotenv
FOUNDRY_PROJECT_ENDPOINT=https://<your-resource>.services.ai.azure.com
```

### `az login` / Authentication Errors

**Cause:** The Azure CLI is not authenticated, or the logged-in account lacks access to the Foundry project.

**Fix:**
```bash
az login
# Verify access:
az account show
```

### `check.py` prints `✗ Unexpected output`

**Cause:** The model responded but produced unexpected text, or the model deployment is misconfigured.

**Fix:** Verify that the model deployment in your Foundry project is operational, and that `FOUNDRY_MODEL` in `.env` matches the deployment name exactly.