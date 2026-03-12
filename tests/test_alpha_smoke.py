import unittest

from botcore.alpha_smoke import EXPECTED_ALPHA_COMMANDS, flatten_command_names, run_alpha_smoke_checks

import bot


class AlphaSmokeTests(unittest.TestCase):
    def test_expected_alpha_commands_registered(self) -> None:
        command_names = flatten_command_names(bot.bot.tree.get_commands())
        self.assertTrue(EXPECTED_ALPHA_COMMANDS.issubset(command_names))

    def test_alpha_smoke_checks_pass(self) -> None:
        results = run_alpha_smoke_checks()
        self.assertTrue(all(result.ok for result in results), msg=[result.details for result in results if not result.ok])
