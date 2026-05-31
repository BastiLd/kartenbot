"""Drift guard for the CommandApi whitelist.

The command modules under ``botcommands/`` reach back into ``bot.py`` through a
facade (``PlayerFacade`` / ``GameplayFacade`` / ``AdminFacade``) whose attribute
access is delegated to ``CommandApi._items``. That ``_items`` dict is built from
an explicit whitelist in ``botcore/command_api.py:build_command_api``.

If a command module starts using ``module.<name>`` / ``api.<name>`` but the name
is missing from the whitelist, the bot raises ``AttributeError`` at runtime when
the button/command is used — not at import time. That exact gap shipped as a
live ``/verbessern`` crash (``_filter_owned_cards_for_current_mode``).

This test statically parses each command module, finds the facade parameter, and
asserts every attribute accessed on it is present in the built ``CommandApi``.
Any future drift fails here instead of in production.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import bot as bot_module

BOTCOMMANDS_DIR = Path(bot_module.__file__).resolve().parent / "botcommands"

COMMAND_MODULES = (
    "player_commands.py",
    "gameplay_commands.py",
    "admin_commands.py",
)


def _facade_param_names(tree: ast.Module) -> set[str]:
    """Return the parameter names annotated with a ``*Facade`` type."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for arg in node.args.args:
            annotation = arg.annotation
            ann_name = ""
            if isinstance(annotation, ast.Name):
                ann_name = annotation.id
            elif isinstance(annotation, ast.Attribute):
                ann_name = annotation.attr
            if ann_name.endswith("Facade"):
                names.add(arg.arg)
    return names


def _accessed_attrs(tree: ast.Module, facade_names: set[str]) -> set[str]:
    """All attributes accessed on a facade variable, e.g. ``module.<attr>``."""
    attrs: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in facade_names
        ):
            attrs.add(node.attr)
    return attrs


class CommandApiParityTests(unittest.TestCase):
    def test_every_facade_access_is_whitelisted(self) -> None:
        available = set(bot_module._command_api._items)
        problems: list[str] = []

        for filename in COMMAND_MODULES:
            path = BOTCOMMANDS_DIR / filename
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            facade_names = _facade_param_names(tree)
            self.assertTrue(
                facade_names,
                f"{filename}: no *Facade parameter found — test assumption broken",
            )

            for attr in sorted(_accessed_attrs(tree, facade_names)):
                # Facade internals are not delegated to CommandApi.
                if attr in {"_api", "_items"}:
                    continue
                if attr not in available:
                    problems.append(f"{filename}: module/api.{attr}")

        self.assertEqual(
            problems,
            [],
            "Command modules access names missing from the CommandApi whitelist "
            "(add them to build_command_api in botcore/command_api.py):\n  "
            + "\n  ".join(problems),
        )


if __name__ == "__main__":
    unittest.main()
