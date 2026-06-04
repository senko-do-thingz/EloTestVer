import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# test server
YES_EMOJI = "<:Yes:1390939480139104356>"
NO_EMOJI = "<:No:1390939497306263603>"

# production server
# YES_EMOJI = "<:Yes:1390939379782123673>"
# NO_EMOJI = "<:No:1390939366003707905>"


def get_discord_token() -> str:
    token = os.getenv("DISCORD_TOKEN")
    if token is None:
        raise ValueError("No DISCORD_TOKEN found in environment variables")
    return token


# pip install -r requirements.txt