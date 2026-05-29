"""Unit-Tests für die Benutzernamens-Normalisierung in ``botcore.name_utils``.

Validates: Requirements 9.1, 9.3, 9.5
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from botcore.name_utils import normalize_user_display, safe_display_name


# Zero-Width-Space Konstante (U+200B); im SUT wird derselbe Code-Point genutzt.
ZWS = "\u200b"

# Pfad zum Modul-Dict, das `name_normalization_enabled()` befragt. ``patch.dict``
# verändert das Dict in-place und stellt den Originalzustand nach dem ``with``
# automatisch wieder her – kein Modul-Reload nötig.
NAMENCONFIG_PATH = "botcore.feature_config.NAMENCONFIG"


def _toggle_on():
    return patch.dict(
        NAMENCONFIG_PATH,
        {"name_normalization_enabled": True, "boss_switch_enabled": True},
    )


def _toggle_off():
    return patch.dict(
        NAMENCONFIG_PATH,
        {"name_normalization_enabled": False, "boss_switch_enabled": True},
    )


class NormalizeUserDisplayTests(unittest.TestCase):
    """Verhalten von ``normalize_user_display`` bei aktivem/inaktivem Toggle."""

    def test_normalize_user_display_inserts_zws_before_markdown_chars(self) -> None:
        """`MFU-_-is_da` → ZWS vor jedem `_`, kein Backslash, sichtbar identisch."""
        with _toggle_on():
            result = normalize_user_display("MFU-_-is_da")

        # Vor jedem `_` muss ein ZWS stehen.
        self.assertIn(f"{ZWS}_", result)
        # Genau zwei Underscores im Input → zwei ZWS im Output.
        self.assertEqual(result.count(ZWS), 2)
        # Kein Backslash-Escape.
        self.assertNotIn("\\", result)
        # Sichtbarer Text (ohne ZWS) bleibt byte-identisch zum Input.
        self.assertEqual(result.replace(ZWS, ""), "MFU-_-is_da")

    def test_normalize_user_display_handles_all_markdown_chars(self) -> None:
        """Alle Markdown-aktiven Zeichen bekommen einen ZWS-Präfix, keine Backslashes."""
        raw = "**bold** ~strike~ pipe|name > quote"
        with _toggle_on():
            result = normalize_user_display(raw)

        self.assertNotIn("\\", result)
        # Sichtbarer Inhalt unverändert.
        self.assertEqual(result.replace(ZWS, ""), raw)

        # Jedes Markdown-aktive Zeichen muss einen ZWS direkt davor haben.
        active_chars = "_*~`>|"
        expected_zws_count = sum(1 for ch in raw if ch in active_chars)
        self.assertEqual(result.count(ZWS), expected_zws_count)

        # Stichprobe: kein Markdown-aktives Zeichen ohne vorangestellten ZWS.
        for idx, ch in enumerate(result):
            if ch in active_chars:
                self.assertGreater(idx, 0, "Markdown-Zeichen ohne ZWS am Anfang")
                self.assertEqual(
                    result[idx - 1],
                    ZWS,
                    f"Markdown-Zeichen {ch!r} an Index {idx} ohne ZWS-Präfix",
                )

    def test_normalize_user_display_pass_through_when_toggle_off(self) -> None:
        """Toggle OFF → Input wird byte-exakt zurückgegeben."""
        raw = "MFU-_-is_da"
        with _toggle_off():
            result = normalize_user_display(raw)

        self.assertEqual(result, raw)
        self.assertNotIn(ZWS, result)
        self.assertNotIn("\\", result)

    def test_normalize_user_display_empty_input_returns_fallback(self) -> None:
        """Leer / Whitespace / None → Fallback (oder leer)."""
        with _toggle_on():
            # Leerer String mit Fallback.
            self.assertEqual(normalize_user_display("", fallback="Unbekannt"), "Unbekannt")
            # Whitespace-only mit Fallback.
            self.assertEqual(normalize_user_display("   ", fallback="Unbekannt"), "Unbekannt")
            # None mit Fallback.
            self.assertEqual(normalize_user_display(None, fallback="Unbekannt"), "Unbekannt")  # type: ignore[arg-type]
            # Ohne Fallback → leerer String.
            self.assertEqual(normalize_user_display(""), "")
            self.assertEqual(normalize_user_display("   "), "")
            self.assertEqual(normalize_user_display(None), "")  # type: ignore[arg-type]

    def test_normalize_user_display_strips_control_chars(self) -> None:
        """Steuerzeichen (0x00–0x1F, 0x7F) werden entfernt, kein Crash."""
        with _toggle_on():
            result = normalize_user_display("a\x00b\x1fc")

        # Steuerzeichen sind raus, Buchstaben bleiben.
        self.assertEqual(result, "abc")
        self.assertNotIn("\x00", result)
        self.assertNotIn("\x1f", result)
        self.assertNotIn(ZWS, result)  # keine Markdown-Zeichen → kein ZWS


class SafeDisplayNameTests(unittest.TestCase):
    """Verhalten von ``safe_display_name`` mit/ohne Toggle."""

    def test_safe_display_name_uses_zws_when_toggle_on(self) -> None:
        """Toggle ON: Output enthält ZWS, keine Backslashes."""
        user = SimpleNamespace(display_name="MFU-_-is_da")
        with _toggle_on():
            result = safe_display_name(user)

        self.assertIn(ZWS, result)
        self.assertNotIn("\\", result)
        # Sichtbarer Inhalt entspricht dem Original-Display-Namen.
        self.assertEqual(result.replace(ZWS, ""), "MFU-_-is_da")

    def test_safe_display_name_falls_back_to_backslash_when_toggle_off(self) -> None:
        """Toggle OFF: Legacy-Verhalten mit Backslash-Escape (`MFU-\\_-is\\_da`)."""
        user = SimpleNamespace(display_name="MFU-_-is_da")
        with _toggle_off():
            result = safe_display_name(user)

        self.assertEqual(result, "MFU-\\_-is\\_da")
        self.assertNotIn(ZWS, result)


if __name__ == "__main__":
    unittest.main()
