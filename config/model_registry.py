"""
model_registry.py
-----------------
Pool of available models. Each agent gets a randomly assigned model per game.
All clients use FoundryChatClient from agent_framework.foundry (current API,
replacing the deprecated AzureOpenAIResponsesClient from agent_framework.azure).

Env vars follow MAF conventions:
  FOUNDRY_PROJECT_ENDPOINT  - https://<resource>.services.ai.azure.com
  FOUNDRY_MODEL             - your deployment name (e.g. gpt-4o-mini)
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ModelConfig:
    name: str       # display name in terminal
    model_id: str   # deployment name in Foundry
    short: str      # 3-char label for tables


# Resolve deployment name from environment.
_primary_model = os.environ.get("FOUNDRY_MODEL", "gpt-4o-mini")

# Add/remove entries to match what you've actually deployed.
# All must exist in your Foundry project.
#
# Display names are derived from the actual model_id so the
# assignment table never lies about which deployment is in use.
def _display_name(model_id: str) -> str:
    """Derive a human-friendly display name from a deployment id."""
    return model_id.upper()   # e.g. "gpt-4o-mini" → "GPT-4O-MINI"


AVAILABLE_MODELS: list[ModelConfig] = [
    ModelConfig(
        name=_display_name(_primary_model),
        model_id=_primary_model,
        short="4om",
    ),
]

# Uncomment to add more deployments when available:
# ModelConfig(name="GPT-4.1-mini", model_id="gpt-4.1-mini", short="41m"),


def make_client(model: ModelConfig):
    """
    Returns a MAF client for the given ModelConfig.
    Uses FoundryChatClient from agent_framework.foundry (current API).
    AzureCliCredential relies on `az login` — no API keys stored.
    """
    from agent_framework.foundry import FoundryChatClient
    from azure.identity import AzureCliCredential

    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise EnvironmentError(
            "FOUNDRY_PROJECT_ENDPOINT is not set. "
            "Copy .env.example to .env and fill in your Azure AI Foundry project endpoint."
        )

    return FoundryChatClient(
        project_endpoint=endpoint,
        model=model.model_id,
        credential=AzureCliCredential(),
    )


def validate_environment() -> list[str]:
    """
    Check that required environment variables are set.
    Returns a list of warning/error messages (empty if everything looks good).
    """
    issues: list[str] = []

    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        issues.append(
            "FOUNDRY_PROJECT_ENDPOINT is not set. "
            "Set it in your .env file to your Azure AI Foundry project endpoint."
        )

    model = os.environ.get("FOUNDRY_MODEL")
    if not model:
        issues.append(
            "FOUNDRY_MODEL is not set. Defaulting to 'gpt-4o-mini'. "
            "Set it in .env to match your deployment name."
        )

    for cfg in AVAILABLE_MODELS:
        if not cfg.model_id:
            issues.append(f"Model '{cfg.name}' has an empty model_id.")

    return issues
