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


# Add/remove entries to match what you've actually deployed.
# All must exist in your Foundry project.
AVAILABLE_MODELS: list[ModelConfig] = [
    ModelConfig(
        name="GPT-4o-mini",
        model_id=os.environ.get("FOUNDRY_MODEL", "gpt-4o-mini"),
        short="4om",
    ),
    ModelConfig(
        name="GPT-4o",
        model_id=os.environ.get("FOUNDRY_MODEL_4O", "gpt-4o"),
        short="4o ",
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
