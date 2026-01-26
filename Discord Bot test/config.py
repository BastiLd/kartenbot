import os
from pathlib import Path


def _load_dotenv() -> None:
    if os.getenv("BOT_TOKEN"):
        return
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")  # remove simple quotes
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

def _load_token_file() -> None:
    if os.getenv("BOT_TOKEN"):
        return
    for filename in ("bot_token.txt", "token.txt"):
        token_path = Path(filename)
        if not token_path.exists():
            continue
        token = token_path.read_text(encoding="utf-8").strip().strip("\"'")
        if token:
            os.environ["BOT_TOKEN"] = token
            return


_load_token_file()

BOT_TOKEN = os.getenv("BOT_TOKEN")


def get_bot_token() -> str:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set. Configure it via env, .env, or bot_token.txt/token.txt.")
    return token
