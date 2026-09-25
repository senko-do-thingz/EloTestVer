import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

IS_PRODUCTION = os.getenv("IS_PRODUCTION", "false").lower() == "true"

if IS_PRODUCTION:
    YES_EMOJI = os.getenv("YES_EMOJI", "<:Yes:1390939379782123673>")
    NO_EMOJI = os.getenv("NO_EMOJI", "<:No:1390939366003707905>")
else:
    YES_EMOJI = os.getenv("YES_EMOJI", "<:Yes:1390939480139104356>")
    NO_EMOJI = os.getenv("NO_EMOJI", "<:No:1390939497306263603>")

PRIVATE_GUILD_ID = int(os.getenv("PRIVATE_GUILD_ID", "1048217847198916618"))


def get_discord_token() -> str:
    token = os.getenv("DISCORD_TOKEN")
    if token is None:
        raise ValueError("No DISCORD_TOKEN found in environment variables")
    return token