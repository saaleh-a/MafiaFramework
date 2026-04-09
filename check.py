"""
check.py - run before a demo to confirm everything is wired up.

Usage: python check.py
Expected: prints SETUP OK
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()


async def main():
    from agent_framework import Agent
    from agent_framework.foundry import FoundryChatClient
    from azure.identity import AzureCliCredential

    print(f"Endpoint: {os.environ.get('FOUNDRY_PROJECT_ENDPOINT', 'NOT SET')}")
    print(f"Model:    {os.environ.get('FOUNDRY_MODEL', 'NOT SET')}")
    print("Connecting...")

    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["FOUNDRY_MODEL"],
        credential=AzureCliCredential(),
    )

    agent = Agent(
        client=client,
        instructions="Reply with exactly the text: SETUP OK - nothing else.",
    )

    result = await agent.run("Confirm setup.")
    print(f"\nResult: {result}")

    if "SETUP OK" in str(result):
        print("\n✓ Ready to run the game.")
    else:
        print("\n✗ Unexpected output. Check your .env and az login.")


if __name__ == "__main__":
    asyncio.run(main())
