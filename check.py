"""
check.py - run before a demo to confirm everything is wired up.

Usage: python check.py
Expected: prints SETUP OK
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Minimum MAF version required (1.0 GA)
_MIN_MAF_VERSION = (1, 0, 0)


def _check_maf_version() -> bool:
    """Verify agent-framework-core is installed at >=1.0.0."""
    try:
        import importlib.metadata
        version_str = importlib.metadata.version("agent-framework-core")
        parts = version_str.split(".")
        version_tuple = tuple(int(p) for p in parts[:3])
        if version_tuple < _MIN_MAF_VERSION:
            print(
                f"\033[91m✗ agent-framework-core {version_str} is too old. "
                f"Minimum required: {'.'.join(str(v) for v in _MIN_MAF_VERSION)}\033[0m"
            )
            print("  Run: pip install --upgrade agent-framework-foundry>=1.0.0")
            return False
        print(f"✓ agent-framework-core {version_str}")
        return True
    except Exception as exc:
        print(f"\033[91m✗ Cannot determine agent-framework-core version: {exc}\033[0m")
        return False


async def main():
    from agent_framework import Agent
    from agent_framework.foundry import FoundryChatClient
    from azure.identity import AzureCliCredential

    # Check MAF version first
    if not _check_maf_version():
        sys.exit(1)

    print(f"Endpoint: {os.environ.get('FOUNDRY_PROJECT_ENDPOINT', 'NOT SET')}")
    print(f"Model:    {os.environ.get('FOUNDRY_MODEL', 'NOT SET')}")
    print("Connecting...")

    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["FOUNDRY_MODEL"],
        credential=AzureCliCredential(),
    )

    # Use Agent() constructor — the MAF-idiomatic way to create agents.
    # This enables tools, middleware, context_providers, compaction.
    agent = Agent(
        client=client,
        name="SetupCheck",
        instructions="Reply with exactly the text: SETUP OK - nothing else.",
    )

    # v1.0 streaming API: use stream=True in run()
    result = ""
    async for chunk in agent.run("Confirm setup.", stream=True):
        if chunk.text:
            result += chunk.text
    print(f"\nResult: {result}")

    if "SETUP OK" in result:
        print("\n✓ Ready to run the game.")
    else:
        print("\n✗ Unexpected output. Check your .env and az login.")


if __name__ == "__main__":
    asyncio.run(main())
