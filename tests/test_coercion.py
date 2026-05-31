import unittest

from services import coercion


class MaybeIntFloatTests(unittest.TestCase):
    def test_maybe_int_handles_common_types(self) -> None:
        self.assertEqual(coercion._maybe_int(True), 1)
        self.assertEqual(coercion._maybe_int(5), 5)
        self.assertEqual(coercion._maybe_int(5.9), 5)
        self.assertEqual(coercion._maybe_int("7"), 7)
        self.assertIsNone(coercion._maybe_int("abc"))
        self.assertIsNone(coercion._maybe_int(None))

    def test_maybe_float_handles_common_types(self) -> None:
        self.assertEqual(coercion._maybe_float(True), 1.0)
        self.assertEqual(coercion._maybe_float(2), 2.0)
        self.assertEqual(coercion._maybe_float("3.5"), 3.5)
        self.assertIsNone(coercion._maybe_float("x"))
        self.assertIsNone(coercion._maybe_float(None))


class ContainerCoercionTests(unittest.TestCase):
    def test_dict_and_list_fallback_to_empty(self) -> None:
        self.assertEqual(coercion._dict_str_any({"a": 1}), {"a": 1})
        self.assertEqual(coercion._dict_str_any("nope"), {})
        self.assertEqual(coercion._list_any([1, 2]), [1, 2])
        self.assertEqual(coercion._list_any("nope"), [])

    def test_int_keyed_dicts_parse_and_default(self) -> None:
        self.assertEqual(coercion._int_keyed_dict({"1": "a", "x": "b"}), {1: "a"})
        self.assertEqual(coercion._int_keyed_bool_dict({"1": 0, "2": 5}), {1: False, 2: True})
        self.assertEqual(coercion._int_keyed_int_dict({"1": "9", "2": "bad"}), {1: 9, 2: 0})
        self.assertEqual(coercion._int_keyed_float_dict({"1": "1.5", "2": "bad"}), {1: 1.5, 2: 0.0})

    def test_nested_int_keyed_dicts(self) -> None:
        self.assertEqual(
            coercion._nested_int_keyed_dict({"1": {"2": "x"}, "bad": {}}),
            {1: {2: "x"}},
        )
        self.assertEqual(
            coercion._nested_int_keyed_int_dict({"1": {"2": "5", "3": "bad"}}),
            {1: {2: 5, 3: 0}},
        )

    def test_json_clone_roundtrips_and_falls_back(self) -> None:
        original = {"a": [1, 2], "b": "c"}
        clone = coercion._json_clone(original)
        self.assertEqual(clone, original)
        self.assertIsNot(clone, original)
        sentinel = object()
        self.assertIs(coercion._json_clone(sentinel), sentinel)


class DamageRangeTests(unittest.TestCase):
    def test_range_pair(self) -> None:
        self.assertEqual(coercion._range_pair([3, 7]), (3, 7))
        self.assertEqual(coercion._range_pair(4), (4, 4))
        self.assertEqual(coercion._range_pair("bad", default_min=1, default_max=2), (1, 2))

    def test_coerce_damage_input(self) -> None:
        self.assertEqual(coercion._coerce_damage_input([3, 7]), [3, 7])
        self.assertEqual(coercion._coerce_damage_input(5), 5)
        self.assertEqual(coercion._coerce_damage_input("bad", default=9), 9)

    def test_random_int_from_range_respects_bounds(self) -> None:
        for _ in range(50):
            value = coercion._random_int_from_range([2, 5])
            self.assertGreaterEqual(value, 2)
            self.assertLessEqual(value, 5)
        # inverted bounds are normalised
        self.assertEqual(coercion._random_int_from_range([4, 4]), 4)


if __name__ == "__main__":
    unittest.main()
