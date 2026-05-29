from __future__ import annotations

import re


# Diese Markdown-Zeichen würden Discord-Formatierungen auslösen und müssen
# in Anzeige-Namen escapet werden. Slashes ("/") und Backslashes lassen wir
# bewusst unberührt, weil escape_markdown(as_needed=False) sonst zwischen
# Buchstaben Backslashes einfügt, die Nutzernamen optisch zerstückeln.
_MARKDOWN_ESCAPE_RE = re.compile(r"([*_~`|>])")

# Markdown-aktive Zeichen, die ``normalize_user_display`` mit einem
# Zero-Width-Space neutralisiert (siehe dortigen Inline-Kommentar).
_MARKDOWN_ACTIVE_CHARS = frozenset("_*~`>|")

# Zero-Width-Space (U+200B): unsichtbares Zeichen mit Breite 0. Wird in
# ``normalize_user_display`` direkt vor Markdown-Marker eingefügt, damit
# Discord die Marker als wörtliche Zeichen rendert statt als Formatierung.
_ZERO_WIDTH_SPACE = "\u200b"


def _escape_markdown_minimal(text: str) -> str:
    return _MARKDOWN_ESCAPE_RE.sub(r"\\\1", text)


def escape_display_text(value: object, fallback: str = "Unbekannt") -> str:
    """Liefert einen Anzeigetext, der von Discord nicht als Markdown gerendert wird.

    Verhalten wird über den Toggle ``name_normalization_enabled`` aus
    ``namenconfig.py`` gesteuert (siehe Requirement 9):

    * Toggle **ON** (Default): Markdown-aktive Zeichen werden über
      :func:`normalize_user_display` mit einem Zero-Width-Space (``\\u200b``)
      neutralisiert. Der sichtbare Text bleibt identisch zum Input – kein
      Backslash, keine Italic-/Bold-Umformatierung. Beispiel: ``MFU-_-is_da``
      bleibt sichtbar als ``MFU-_-is_da``.
    * Toggle **OFF**: Klassisches Backslash-Escape via
      :func:`_escape_markdown_minimal` – byte-exakt gleiches Verhalten wie
      vor v2.3.0. Beispiel: ``MFU-_-is_da`` wird zu ``MFU-\\_-is\\_da``.

    ``fallback`` greift, wenn ``value`` ``None``, leer oder rein aus
    Whitespace besteht; im OFF-Pfad wird der Fallback weiterhin durch
    Backslash-Escape geschickt, damit die historische Ausgabe konsistent
    bleibt.
    """
    # Lokaler Import vermeidet einen Zyklus, falls ``feature_config`` selbst
    # über andere ``botcore``-Helfer geladen wird, und erlaubt Tests, den
    # Toggle pro Aufruf zu patchen.
    from botcore.feature_config import name_normalization_enabled

    text = str(value or "").strip()
    if not text:
        text = fallback

    if name_normalization_enabled():
        # Im ON-Pfad übernimmt ``normalize_user_display`` Sanitization +
        # ZWS-Schutz. Wir reichen den bereits getrimmten Text durch und
        # geben den Fallback weiter, damit auch bei leeren Eingaben ein
        # sinnvoller Default herauskommt.
        return normalize_user_display(text, fallback=fallback)

    return _escape_markdown_minimal(text)


def safe_display_name(user: object, fallback: str = "Unbekannt") -> str:
    if isinstance(user, str):
        return escape_display_text(user, fallback=fallback)
    display_name = getattr(user, "display_name", None)
    if display_name:
        return escape_display_text(display_name, fallback=fallback)
    username = getattr(user, "name", None)
    if username:
        return escape_display_text(username, fallback=fallback)
    return escape_display_text(fallback, fallback=fallback)


def safe_user_option_label(user: object, *, prefix: str = "", fallback: str = "Unbekannt", max_len: int = 100) -> str:
    base = safe_display_name(user, fallback=fallback)
    label = f"{prefix}{base}" if prefix else base
    return label[:max_len]


def safe_thread_name(*parts: object, fallback: str = "Thread", max_len: int = 100) -> str:
    cleaned = [str(part or "").strip() for part in parts if str(part or "").strip()]
    if not cleaned:
        cleaned = [fallback]
    name = " ".join(cleaned)
    return name[:max_len]


def normalize_user_display(raw_name: str, *, fallback: str = "") -> str:
    """Neutralisiert Markdown-Zeichen in einem Anzeigenamen ohne Backslashes.

    Verhalten
    ---------
    * Ist ``name_normalization_enabled()`` ``False``, liefert die Funktion
      ``raw_name`` byte-exakt zurück (Pass-Through).
    * Ist ``raw_name`` ``None``, leer oder rein aus Whitespace bestehend,
      wird ``fallback`` zurückgegeben (oder ``""`` wenn ``fallback`` leer
      ist) – Markdown-Verarbeitung entfällt in diesem Fall.
    * Andernfalls werden Steuerzeichen (0x00–0x1F, 0x7F) entfernt und
      jedes Markdown-aktive Zeichen (``_ * ~ ` > |``) bekommt direkt davor
      einen Zero-Width-Space (``\\u200b``) gesetzt.

    Warum Zero-Width-Space?
    -----------------------
    Discord aktiviert Markdown-Marker wie ``_`` oder ``*`` nur, wenn sie
    direkt an einem regulären Wortzeichen ankoppeln. Schiebt man einen
    Zero-Width-Space dazwischen, sieht der Parser ein Nicht-Wortzeichen
    vor dem Marker und rendert ihn als Literal. Da der ZWS keine sichtbare
    Breite hat, bleibt der Name optisch identisch – kein Backslash, keine
    Italic-/Bold-Umformatierung. Mention-Injection (``<@…>``) ist hier
    explizit nicht im Scope.

    Parameter
    ---------
    raw_name:
        Roh-Anzeigename des Nutzers (z. B. aus ``user.display_name``).
    fallback:
        Ersatzwert, falls ``raw_name`` leer/whitespace ist. Standard ``""``.

    Returns
    -------
    Den normalisierten Anzeigenamen oder den Fallback.
    """
    # Lokaler Import bricht eine potentielle Zyklus-Abhängigkeit (das
    # feature_config-Modul kann selbst Logging über andere botcore-Helfer
    # erzeugen) und erlaubt Tests, den Toggle pro Aufruf zu patchen.
    from botcore.feature_config import name_normalization_enabled

    if raw_name is None:
        return fallback or ""

    if not isinstance(raw_name, str):
        # Defensive: andere Typen (z. B. ``int``) zuerst zu ``str`` casten,
        # damit der Pass-Through unten weiterhin byte-exakt arbeitet.
        raw_name = str(raw_name)

    if not raw_name.strip():
        return fallback or ""

    if not name_normalization_enabled():
        # Pass-Through: byte-exakt, keine Sanitization, kein ZWS.
        return raw_name

    # Steuerzeichen (0x00–0x1F, 0x7F) entfernen. Sichtbarer Inhalt bleibt
    # erhalten, der ZWS-Schutz greift unten auf den verbleibenden Zeichen.
    sanitized = "".join(
        ch for ch in raw_name if not (ord(ch) < 0x20 or ord(ch) == 0x7F)
    )

    if not sanitized:
        return fallback or ""

    # Jedem Markdown-aktiven Zeichen einen ZWS voranstellen.
    parts: list[str] = []
    for ch in sanitized:
        if ch in _MARKDOWN_ACTIVE_CHARS:
            parts.append(_ZERO_WIDTH_SPACE)
        parts.append(ch)
    return "".join(parts)
