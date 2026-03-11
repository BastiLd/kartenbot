from .admin_commands import register_admin_commands
from .gameplay_commands import register_gameplay_commands
from .player_commands import register_player_commands

__all__ = [
    "register_admin_commands",
    "register_gameplay_commands",
    "register_player_commands",
]
