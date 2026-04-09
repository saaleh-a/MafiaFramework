import os
from dotenv import load_dotenv

load_dotenv()

# Env vars matching the MAF FoundryChatClient conventions
FOUNDRY_PROJECT_ENDPOINT: str = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
FOUNDRY_MODEL: str = os.environ.get("FOUNDRY_MODEL", "gpt-4o-mini")
