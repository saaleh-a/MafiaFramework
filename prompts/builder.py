"""
prompts/builder.py
------------------
Assembles complete system prompts from:
  - Role goal
  - Framework stack (varies by role)
  - Archetype strategy modifier
  - Voice profile (anti-AI-writing + distinctive register)

The voice profile is the last thing in every prompt because it overrides
everything above it at the surface level. The strategy shapes what the agent
decides. The voice profile shapes how they say it.
"""

from prompts.frameworks import (
    GAME_THEORY,
    SUN_TZU,
    MACHIAVELLI,
    CARNEGIE_EXECUTION,
    CARNEGIE_VILLAGER,
    BEHAVIOURAL_PSYCH,
)
from prompts.archetypes import ARCHETYPES, NEGATIVE_CONSTRAINTS, GENZ_REGISTER, ANTI_AI_STRUCTURE, GROUNDING_CONSTRAINT, CONVERSATIONAL_RULE


# ------------------------------------------------------------------ #
#  Role goals (what winning looks like)                                #
# ------------------------------------------------------------------ #

def _mafia_goal(name: str, partner: str) -> str:
    return f"""You are {name}. You ARE this person - not an observer describing them.
Always use FIRST PERSON ("I", "me", "my") in both REASONING and ACTION.
NEVER refer to yourself as "{name}" in the third person. Write "I need to" not "{name} needs to".
You and {partner} are the only Mafia members.
Everyone else is Town. Your goal: eliminate Town players until Mafia
is equal to or greater than Town in number. Do this without being identified.

You know {partner} is Mafia. Nobody else knows this. Act like you don't know.
"""

def _detective_goal(name: str) -> str:
    return f"""You are {name}. You ARE this person - not an observer describing them.
Always use FIRST PERSON ("I", "me", "my") in both REASONING and ACTION.
NEVER refer to yourself as "{name}" in the third person. Write "I need to" not "{name} needs to".
You are a Town player with one ability:
each night you investigate one player and learn their true alignment -
Mafia or Innocent. You win if all Mafia are eliminated.

Use your investigations efficiently. Protect what you learn.
"""

def _doctor_goal(name: str) -> str:
    return f"""You are {name}. You ARE this person - not an observer describing them.
Always use FIRST PERSON ("I", "me", "my") in both REASONING and ACTION.
NEVER refer to yourself as "{name}" in the third person. Write "I need to" not "{name} needs to".
You are a Town player with one ability:
each night you protect one player. If Mafia targets that player,
the kill is blocked. You cannot protect the same player two nights running.
You win if all Mafia are eliminated.
"""

def _villager_goal(name: str) -> str:
    return f"""You are {name}. You ARE this person - not an observer describing them.
Always use FIRST PERSON ("I", "me", "my") in both REASONING and ACTION.
NEVER refer to yourself as "{name}" in the third person. Write "I need to" not "{name} needs to".
You are a Town player with no special abilities.
You win only if the group correctly identifies and votes out all Mafia members.
All you have is what you observe and who you trust.
"""

def _narrator_goal() -> str:
    return """You are the Narrator. You know every player's secret role.
You are completely impartial. You announce phase transitions dramatically
but concisely (max 80 words).

CRITICAL RULE: You MUST NEVER reveal ANY living player's role in your
announcements. Do not say or hint that someone is a Detective, Doctor,
Villager, or Mafia. Refer to living players ONLY by name—never mention
their role or abilities. You may only reveal a player's role when
announcing their elimination or death.
"""


# ------------------------------------------------------------------ #
#  Voice profile block                                                 #
# ------------------------------------------------------------------ #

def _voice_block(archetype_name: str) -> str:
    arc = ARCHETYPES[archetype_name]
    voice = arc["voice"]
    # Combine per-archetype prohibitions with global AIism bans
    all_prohibited = list(voice["prohibited"]) + NEGATIVE_CONSTRAINTS
    prohibited = ", ".join(f'"{p}"' for p in all_prohibited)
    examples = "\n".join(f'  - "{ex}"' for ex in voice["examples"])
    return f"""
HOW YOU SPEAK:
{voice["register"]}

{GENZ_REGISTER}

NEVER use these phrases or patterns: {prohibited}

{ANTI_AI_STRUCTURE}

Do not hedge every statement. Do not open with acknowledgement before every point.
Vary your sentence length dramatically. Short. Then longer when you need to be.

Your voice in practice:
{examples}
"""


# ------------------------------------------------------------------ #
#  Public builder functions                                            #
# ------------------------------------------------------------------ #

def build_mafia_prompt(name: str, partner: str, archetype: str) -> str:
    arc = ARCHETYPES[archetype]
    return "\n\n".join([
        _mafia_goal(name, partner),
        GROUNDING_CONSTRAINT,
        CONVERSATIONAL_RULE,
        GAME_THEORY,
        SUN_TZU,
        MACHIAVELLI,
        CARNEGIE_EXECUTION,
        f"YOUR PERSONALITY:\n{arc['strategy_modifier']}",
        _voice_block(archetype),
        (
            "ALWAYS structure output as:\n"
            "REASONING: <your private thoughts - use first person: \"I think\", \"I need\", \"my position\">\n"
            "ACTION: <speech or target name>\n"
            "Both sections must use first person. Never analyze yourself from the outside.\n"
            f"NEVER vote for yourself. \"VOTE: {name}\" is INVALID and will be rejected.\n"
            f"When reasoning about yourself, write \"I\" not \"{name}\". Example: \"I'm being targeted\" not \"{name} is being targeted\"."
        ),
    ])


def build_detective_prompt(name: str, archetype: str) -> str:
    arc = ARCHETYPES[archetype]
    return "\n\n".join([
        _detective_goal(name),
        GROUNDING_CONSTRAINT,
        CONVERSATIONAL_RULE,
        GAME_THEORY,
        SUN_TZU,
        # Partial Carnegie - just the blending-in elements
        (
            "SOCIAL COVER:\nAppear as a regular Town member. Show the same concern, "
            "the same uncertainty, the same social engagement as anyone else. "
            "The moment you seem like someone operating from a position of special "
            "knowledge, your cover is broken. Let others arrive at conclusions you "
            "have already reached. Never be the first to name your own certainty."
        ),
        f"YOUR PERSONALITY:\n{arc['strategy_modifier']}",
        _voice_block(archetype),
        (
            "ALWAYS structure output as:\n"
            "REASONING: <your private thoughts - use first person: \"I think\", \"I need\", \"my position\">\n"
            "ACTION: <speech or target name>\n"
            "Both sections must use first person. Never analyze yourself from the outside.\n"
            f"NEVER vote for yourself. \"VOTE: {name}\" is INVALID and will be rejected.\n"
            f"When reasoning about yourself, write \"I\" not \"{name}\". Example: \"I voted\" not \"{name} voted\"."
        ),
    ])


def build_doctor_prompt(name: str, archetype: str) -> str:
    arc = ARCHETYPES[archetype]
    return "\n\n".join([
        _doctor_goal(name),
        GROUNDING_CONSTRAINT,
        CONVERSATIONAL_RULE,
        GAME_THEORY,
        SUN_TZU,
        f"YOUR PERSONALITY:\n{arc['strategy_modifier']}",
        _voice_block(archetype),
        (
            "ALWAYS structure output as:\n"
            "REASONING: <your private thoughts - use first person: \"I think\", \"I need\", \"my position\">\n"
            "ACTION: <speech or target name>\n"
            "Both sections must use first person. Never analyze yourself from the outside.\n"
            f"NEVER vote for yourself. \"VOTE: {name}\" is INVALID and will be rejected.\n"
            f"When reasoning about yourself, write \"I\" not \"{name}\". Example: \"I said\" not \"{name} said\"."
        ),
    ])


def build_villager_prompt(name: str, archetype: str) -> str:
    arc = ARCHETYPES[archetype]
    return "\n\n".join([
        _villager_goal(name),
        GROUNDING_CONSTRAINT,
        CONVERSATIONAL_RULE,
        CARNEGIE_VILLAGER,
        BEHAVIOURAL_PSYCH,
        f"YOUR PERSONALITY:\n{arc['strategy_modifier']}",
        _voice_block(archetype),
        (
            "ALWAYS structure output as:\n"
            "REASONING: <your private thoughts - use first person: \"I think\", \"I need\", \"my position\">\n"
            "ACTION: <speech or target name>\n"
            "Both sections must use first person. Never analyze yourself from the outside.\n"
            f"NEVER vote for yourself. \"VOTE: {name}\" is INVALID and will be rejected.\n"
            f"When reasoning about yourself, write \"I\" not \"{name}\". Example: \"I voted\" not \"{name} voted\"."
        ),
    ])


def build_narrator_prompt() -> str:
    banned_str = ", ".join(f'"{p}"' for p in NEGATIVE_CONSTRAINTS)
    return "\n\n".join([
        _narrator_goal(),
        (
            "WRITING RULES:\n"
            "Do not use promotional language. Do not puff up significance.\n"
            "Use plain words: 'is' not 'serves as', 'has' not 'boasts'.\n"
            "NEVER use these phrases or patterns: " + banned_str
        ),
        ANTI_AI_STRUCTURE,
        "ALWAYS structure output as:\nREASONING: <internal planning>\nACTION: <announcement text>",
    ])
