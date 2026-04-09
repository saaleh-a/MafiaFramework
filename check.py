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

    agent = client.as_agent(
        name="SetupCheck",
        instructions="Reply with exactly the text: SETUP OK - nothing else.",
    )

    # v1.0 streaming API: use stream=True in run() (not deprecated run_stream())
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
