from __future__ import annotations

from typing import Any


class _ModuleFacade:
    """
    Runtime-safe wrapper around the old `sys.modules[__name__]` pattern.

    The command modules used to receive the entire `bot.py` module and access many
    attributes via `module.*`. We keep behavior identical by delegating attribute
    access to the underlying module, while giving commands a dedicated object
    that can be narrowed over time.
    """

    def __init__(self, api: object) -> None:
        self._api = api

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - delegation
        return getattr(self._api, name)


class PlayerFacade(_ModuleFacade):
    """Facade for `botcommands/player_commands.py`."""


class GameplayFacade(_ModuleFacade):
    """Facade for `botcommands/gameplay_commands.py`."""


class AdminFacade(_ModuleFacade):
    """Facade for `botcommands/admin_commands.py`."""

