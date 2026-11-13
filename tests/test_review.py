import unittest
from copy import deepcopy

from cookbook_ocr.review import review_recipe
from test_adaptation import field, ingredient, recipe, step


class ReviewTests(unittest.TestCase):
    def test_can_correct_a_stated_quantity_and_fill_a_missing_field(self):
        source = recipe(None, [ingredient("sugar", "sugar", "1", "cup")], [step("Mix the sugar.", "sugar")])
        original = deepcopy(source)
        reviewed = review_recipe(source, [
            {"path": "/ingredients/0/quantity", "action": "correct", "value": "2"},
            {"path": "/servings", "action": "correct", "value": "4"},
            {"path": "/steps/0/duration", "action": "correct", "value": "1 minute"},
        ])

        self.assertEqual(source, original)
        self.assertEqual(reviewed["status"], "reviewed")
        self.assertEqual(reviewed["recipe"]["servings"]["value"], "4")
        quantity = reviewed["recipe"]["ingredients"][0]["quantity"]
        self.assertEqual((quantity["value"], quantity["status"]), ("2", "reviewed"))
        self.assertEqual(quantity["evidence"], source["ingredients"][0]["quantity"]["evidence"])
        self.assertEqual(reviewed["decisions"][0]["before"], "1")
        self.assertEqual(reviewed["recipe"]["steps"][0]["duration"]["value"], "1 minute")

    def test_optional_field_can_be_cleared_and_required_fields_cannot(self):
        source = recipe("4", [ingredient("sugar", "sugar", "1", "cup")], [step("Mix the sugar.", "sugar")])
        source["ingredients"][0]["quantity"]["status"] = "needs_review"
        reviewed = review_recipe(source, [
            {"path": "/ingredients/0/quantity", "action": "correct", "value": None},
        ])
        self.assertEqual(reviewed["unresolved_paths"], [])
        self.assertIsNone(reviewed["recipe"]["ingredients"][0]["quantity"]["value"])
        with self.assertRaisesRegex(ValueError, "Enter the value"):
            review_recipe(source, [{"path": "/ingredients/0/name", "action": "correct", "value": None}])

    def test_step_references_can_be_corrected_using_existing_entries(self):
        source = recipe("4", [ingredient("sugar", "sugar"), ingredient("salt", "salt")], [step("Stir in the salt.", "sugar")])
        source["tools"] = [{"id": "pan", "name": field("pan")}]
        reviewed = review_recipe(source, [
            {"path": "/steps/0/ingredient_ids", "action": "correct", "value": ["salt"]},
            {"path": "/steps/0/tool_ids", "action": "correct", "value": ["pan"]},
        ])
        self.assertEqual(reviewed["recipe"]["steps"][0]["ingredient_ids"], ["salt"])
        self.assertEqual(reviewed["recipe"]["steps"][0]["tool_ids"], ["pan"])
        self.assertEqual(reviewed["decisions"][0]["before"], ["sugar"])
        self.assertEqual(source["steps"][0]["ingredient_ids"], ["sugar"])
        with self.assertRaisesRegex(ValueError, "existing recipe entries"):
            review_recipe(source, [{"path": "/steps/0/tool_ids", "action": "correct", "value": ["salt"]}])

    def test_confirm_cannot_change_an_existing_value(self):
        source = recipe("Serves 4", [], [])
        reviewed = review_recipe(source, [{"path": "/servings", "action": "confirm", "value": "Serves 4"}])
        self.assertEqual(reviewed["recipe"]["servings"]["status"], "reviewed")
        with self.assertRaisesRegex(ValueError, "Use correct"):
            review_recipe(source, [{"path": "/servings", "action": "confirm", "value": "5"}])
