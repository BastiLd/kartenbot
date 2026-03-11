import asyncio
import time

import discord
from discord.ext import commands

from config import get_bot_token

BOT_START_TIME = time.time()


def build_bot_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.presences = True
    return intents


def build_bot(*, tree_cls) -> commands.Bot:
    return commands.Bot(command_prefix="!", intents=build_bot_intents(), tree_cls=tree_cls)


def run_bot(bot: commands.Bot, *, close_db) -> None:
    token = get_bot_token()
    try:
        bot.run(token)
    finally:
        asyncio.run(close_db())
