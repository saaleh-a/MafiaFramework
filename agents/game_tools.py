"""
agents/game_tools.py
---------------------
MAF @tool-decorated functions that agents can call to submit
structured game actions (votes, night targets, investigation targets).

Using MAF's native tool-calling means the model returns a structured
function call instead of free-form text that we have to regex-parse.
The framework handles the JSON schema generation, invocation, and
result injection automatically.

These tools are wired into agents via the `tools=` parameter on
Agent() — the MAF-idiomatic way to give agents callable actions.
"""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field


@tool(name="cast_vote", approval_mode="never_require")
def cast_vote(
    target: Annotated[str, Field(description="The exact name of the player you are voting to eliminate. Must be from the valid targets list.")],
    reasoning: Annotated[str, Field(description="Brief explanation for why you are voting for this player.")] = "",
) -> str:
    """Cast your vote to eliminate a player during the day phase. You MUST call this tool to vote."""
    return f"VOTE: {target}"


@tool(name="choose_target", approval_mode="never_require")
def choose_target(
    target: Annotated[str, Field(description="The exact name of the player you are targeting. Must be from the valid targets list.")],
    reasoning: Annotated[str, Field(description="Brief explanation for your choice.")] = "",
) -> str:
    """Choose a player to target during the night phase (for kill, investigation, or protection)."""
    return f"TARGET: {target}"
