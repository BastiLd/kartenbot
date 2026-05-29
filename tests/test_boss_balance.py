"""Integrationstests für die Boss-Balance-Werte v2.3.0 (Req. 16-20).

Prüft die Damage-Ranges und bedingten Effekt-Felder direkt gegen die
Daten in ``mission_enemies.py`` sowie die konfigurierten Lakei-3-Werte.
"""

import unittest

import mission_enemies


def _find(encounters, name):
    for enc in encounters:
        if enc["name"] == name:
            return enc
    raise AssertionError(f"Encounter {name!r} nicht gefunden")


def _attack(encounter, attack_name):
    for atk in encounter["attacks"]:
        if atk["name"] == attack_name:
            return atk
    raise AssertionError(f"Attacke {attack_name!r} nicht in {encounter['name']!r}")


class MaestroBalanceTests(unittest.TestCase):
    def setUp(self):
        self.boss = _find(mission_enemies.get_operation_broken_timeline_encounters(), "Maestro")

    def test_tyrannenschlag_range(self):
        self.assertEqual(_attack(self.boss, "Tyrannen-Schlag")["damage"], [14, 20])

    def test_trophaeensaal_bonus_is_10(self):
        eff = _attack(self.boss, "Trophäensaal-Raub")["effects"][0]
        self.assertEqual(eff["amount"], 10)

    def test_gamma_eruption_range(self):
        self.assertEqual(_attack(self.boss, "Gamma-Eruption")["damage"], [26, 35])


class ModokBalanceTests(unittest.TestCase):
    def setUp(self):
        self.encs = mission_enemies.get_operation_technischer_kollaps_encounters()
        self.boss = _find(self.encs, "M.O.D.O.K.")

    def test_gedankenstrahl_range(self):
        self.assertEqual(_attack(self.boss, "Gedankenstrahl")["damage"], [12, 20])

    def test_berechnete_heilung_conditional(self):
        heal = _attack(self.boss, "Berechnete Heilung")
        self.assertEqual(heal["heal"], [15, 15])
        self.assertEqual(heal["heal_if_player_used_cd_last_round"], 30)

    def test_gehirn_explosion(self):
        self.assertEqual(_attack(self.boss, "Gehirn-Explosion")["damage"], [25, 25])

    def test_lakei3_weakened(self):
        mech = _find(self.encs, "Schwerer Kampf-Mech")
        self.assertEqual(mech["hp"], 102)
        self.assertEqual(_attack(mech, "Rammstoß")["damage"], [14, 18])
        self.assertEqual(_attack(mech, "Gatling-Kanone")["damage"], [20, 24])


class GreenGoblinBalanceTests(unittest.TestCase):
    def setUp(self):
        self.encs = mission_enemies.get_operation_gruener_terror_encounters()
        self.boss = _find(self.encs, "Green Goblin")

    def test_goblin_handschuh_range(self):
        self.assertEqual(_attack(self.boss, "Goblin-Handschuh")["damage"], [14, 18])

    def test_gleiter_ramme_recoil_6(self):
        eff = _attack(self.boss, "Gleiter-Ramme")["effects"][0]
        self.assertEqual(eff["type"], "counter_flat")
        self.assertEqual(eff["damage"], 6)

    def test_kuerbisbomben_3x8(self):
        atk = _attack(self.boss, "Kürbisbomben-Teppich")
        self.assertEqual(atk["multi_hit"]["hits"], 3)
        self.assertEqual(atk["multi_hit"]["per_hit_damage"], [8, 8])

    def test_lakei3_weakened(self):
        gleiter = _find(self.encs, "Prototyp-Kampfgleiter")
        self.assertEqual(gleiter["hp"], 98)
        self.assertEqual(_attack(gleiter, "MG-Sperrfeuer")["damage"], [14, 18])


class KingpinBalanceTests(unittest.TestCase):
    def setUp(self):
        self.boss = _find(mission_enemies.get_operation_goldener_kaefig_encounters(), "Kingpin")

    def test_stockhieb_range(self):
        self.assertEqual(_attack(self.boss, "Stockhieb")["damage"], [13, 17])

    def test_zermalmer_player_hp_conditional(self):
        atk = _attack(self.boss, "Zermalmender Griff")
        self.assertEqual(atk["damage"], [38, 38])
        self.assertEqual(atk["reduced_damage_if_player_hp_at_least"], {"hp": 60, "damage": 26})

    def test_sumo_ansturm_clears_defenses(self):
        eff = _attack(self.boss, "Sumo-Ansturm")["effects"][0]
        self.assertEqual(eff["type"], "clear_negative_effects")


class AgathaBalanceTests(unittest.TestCase):
    def setUp(self):
        self.encs = mission_enemies.get_operation_hexenfeuer_encounters()
        self.boss = _find(self.encs, "Agatha Harkness")

    def test_chaos_energie_ball(self):
        self.assertEqual(_attack(self.boss, "Chaos-Energie-Ball")["damage"], [11, 11])

    def test_darkhold_fluch_heal_negation(self):
        atk = _attack(self.boss, "Darkhold-Fluch")
        self.assertEqual(atk["damage"], [10, 10])
        self.assertEqual(atk["effects"][0]["type"], "next_player_heal_negation")

    def test_lila_illusion_counter(self):
        effs = _attack(self.boss, "Lila Illusion")["effects"]
        types = {e["type"] for e in effs}
        self.assertIn("evade", types)
        self.assertIn("counter_flat", types)

    def test_lakei3_weakened(self):
        waechter = _find(self.encs, "Wächter des Dunkelbuchs")
        self.assertEqual(waechter["hp"], 98)


if __name__ == "__main__":
    unittest.main()
