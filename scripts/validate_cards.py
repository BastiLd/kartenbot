from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from karten import karten
from services.card_validation import summarize_validation_issues, validate_cards


def main() -> int:
    issues = validate_cards(karten)
    if issues:
        print("Probleme gefunden:")
        print(summarize_validation_issues(issues, max_items=None))
        return 1
    print(f"karten.py ist valide ({len(karten)} Karten).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
