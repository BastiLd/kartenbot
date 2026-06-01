"""Nebenläufigkeits-Tests für die atomaren Guthaben-Operationen (Audit B1).

Vor dem Fix nutzten ``spend_infinitydust``/``spend_units`` ein
SELECT-dann-UPDATE-Muster. Liefen zwei Abbuchungen verschränkt (z. B. schnelles
Doppel-Klicken), konnten beide denselben Stand lesen und das Guthaben doppelt
ausgeben oder negativ werden lassen. Diese Tests feuern viele Abbuchungen
gleichzeitig ab und prüfen, dass nie mehr ausgegeben wird als vorhanden ist.
"""

import asyncio
import unittest

from db import close_db, db_context, init_db
from services.user_data import (
    add_infinitydust,
    add_units,
    get_infinitydust,
    get_units,
    spend_infinitydust,
    spend_units,
)

# Hohe, unwahrscheinlich kollidierende Test-IDs.
DUST_UID = 999_000_101
UNITS_UID = 999_000_102


async def _reset(table: str, user_id: int) -> None:
    async with db_context() as db:
        await db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        await db.commit()


class DustAtomicityTests(unittest.TestCase):
    def test_concurrent_spend_infinitydust_never_overspends(self) -> None:
        async def _run() -> None:
            await init_db()
            try:
                await _reset("user_infinitydust", DUST_UID)
                await add_infinitydust(DUST_UID, 100)
                self.assertEqual(await get_infinitydust(DUST_UID), 100)

                # 50 gleichzeitige Abbuchungen à 10 -> höchstens 10 dürfen klappen.
                results = await asyncio.gather(
                    *(spend_infinitydust(DUST_UID, 10) for _ in range(50))
                )

                self.assertEqual(sum(1 for ok in results if ok), 10)
                self.assertEqual(await get_infinitydust(DUST_UID), 0)
                self.assertGreaterEqual(await get_infinitydust(DUST_UID), 0)
            finally:
                await _reset("user_infinitydust", DUST_UID)
                await close_db()

        asyncio.run(_run())

    def test_concurrent_spend_units_never_overspends(self) -> None:
        async def _run() -> None:
            await init_db()
            try:
                await _reset("user_units", UNITS_UID)
                await add_units(UNITS_UID, 100)
                self.assertEqual(await get_units(UNITS_UID), 100)

                results = await asyncio.gather(
                    *(spend_units(UNITS_UID, 10) for _ in range(50))
                )

                self.assertEqual(sum(1 for ok in results if ok), 10)
                self.assertEqual(await get_units(UNITS_UID), 0)
                self.assertGreaterEqual(await get_units(UNITS_UID), 0)
            finally:
                await _reset("user_units", UNITS_UID)
                await close_db()

        asyncio.run(_run())

    def test_spend_more_than_balance_is_rejected(self) -> None:
        async def _run() -> None:
            await init_db()
            try:
                await _reset("user_infinitydust", DUST_UID)
                await add_infinitydust(DUST_UID, 5)
                self.assertFalse(await spend_infinitydust(DUST_UID, 10))
                self.assertEqual(await get_infinitydust(DUST_UID), 5)
            finally:
                await _reset("user_infinitydust", DUST_UID)
                await close_db()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
