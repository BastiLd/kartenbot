# Kompatibilitäts-Wrapper: Die eigentliche DB-Implementierung liegt in
# services/db.py. Dieses Modul re-exportiert sie nur, damit ältere Importe wie
# ``from db import db_context`` weiter funktionieren. Hier NICHTS Neues ergänzen –
# Änderungen gehören nach services/db.py.
from services.db import DB_PATH, close_db, connect_db, db_context, init_db

__all__ = [
    "DB_PATH",
    "close_db",
    "connect_db",
    "db_context",
    "init_db",
]
