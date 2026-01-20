import asyncio

from bot import create_bot
from db import close_db


async def main() -> None:
    bot = create_bot()
    try:
        commands = bot.tree.get_commands()
        print(f"Verfuegbare Commands: {len(commands)}")
        for cmd in commands:
            print(f"- /{cmd.name}: {cmd.description}")
    finally:
        await bot.close()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
