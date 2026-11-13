import unittest
from copy import deepcopy
from fractions import Fraction

from cookbook_ocr.capacity import review_vessel_capacity


def tool(name, quote=None):
    return {
        "id": "vessel",
        "name": {"value": name, "evidence": {"quote": quote} if quote else None},
    }


def step(instruction, *tool_ids):
    return {"instruction": {"value": instruction}, "tool_ids": list(tool_ids)}


def reviews_for(recipe, factor=Fraction(2)):
    items = []
    review_vessel_capacity(recipe, factor, lambda path, reason: items.append((path, reason)))
    return items


class CapacityTests(unittest.TestCase):
    def test_larger_batch_flags_stated_volume_at_linked_tool(self):
        recipe = {
            "tools": [tool("pot", "Pour the mixture into a 1 1/2 L pot.")],
            "steps": [step("Pour the mixture into a 1 1/2 L pot.", "vessel")],
        }
        original = deepcopy(recipe)
        items = reviews_for(recipe)
        self.assertEqual(len(items), 1)
        path, reason = items[0]
        self.assertEqual(path, "/tools/0/name")
        self.assertIn("capacity (1 1/2 L)", reason)
        self.assertEqual(recipe, original)

    def test_flags_dimensions_for_a_pan_mentioned_only_in_a_step(self):
        recipe = {
            "tools": [],
            "steps": [step("Bake in a 20 × 30 cm pan at 180 °C.")],
        }
        [(path, reason)] = reviews_for(recipe)
        self.assertEqual(path, "/steps/0/instruction")
        self.assertIn("dimensions (20 × 30 cm)", reason)

    def test_uses_step_reference_when_the_vessel_size_is_not_in_tool_name(self):
        recipe = {
            "tools": [tool("panela")],
            "steps": [step("Coloque o caldo numa panela de 1500 mL e cozinhe.")],
        }
        [(path, reason)] = reviews_for(recipe)
        self.assertEqual(path, "/tools/0/name")
        self.assertIn("capacity (1500 mL)", reason)

    def test_german_pan_warning_does_not_infer_capacity_from_contents(self):
        recipe = {
            "tools": [tool("Topf")],
            "steps": [step("Den Topf mit 2 l Wasser füllen.", "vessel")],
        }
        [(path, reason)] = reviews_for(recipe)
        self.assertEqual(path, "/tools/0/name")
        self.assertIn("fits the larger batch", reason)
        self.assertNotIn("2 l", reason)

    def test_no_warning_when_batch_does_not_increase_or_vessel_is_absent(self):
        recipe = {"tools": [tool("pot")], "steps": [step("Use the pot.", "vessel")]}
        for factor in (Fraction(1), Fraction(1, 2)):
            self.assertEqual(reviews_for(recipe, factor), [])
        recipe = {"tools": [tool("whisk")], "steps": [step("Whisk in the pan juices.", "vessel")]}
        self.assertEqual(reviews_for(recipe), [])

    def test_explicit_same_sized_batches_do_not_warn(self):
        recipe = {
            "tools": [tool("skillet")],
            "steps": [step("Cook in the skillet in same-sized batches.", "vessel")],
        }
        self.assertEqual(reviews_for(recipe), [])

    def test_batching_the_skillet_does_not_exempt_a_separate_saucepan(self):
        recipe = {
            "tools": [tool("skillet")],
            "steps": [step("Fry in the skillet in same-sized batches; heat the sauce in a saucepan.", "vessel")],
        }
        [(path, _)] = reviews_for(recipe)
        self.assertEqual(path, "/steps/0/instruction")
