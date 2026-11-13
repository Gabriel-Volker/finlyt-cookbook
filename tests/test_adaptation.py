import unittest
from copy import deepcopy
from fractions import Fraction

from cookbook_ocr.adaptation import adapt_recipe
from cookbook_ocr.quantities import scale_quantity


def field(value, status=None):
    return {
        "value": value,
        "status": status or ("stated" if value is not None else "missing"),
        "evidence": {"source": "A", "page": "1", "quote": value} if value else None,
        "review_item_ids": [],
    }


def ingredient(key, name, quantity=None, unit=None, preparation=None):
    return {
        "id": key, "name": field(name), "quantity": field(quantity), "unit": field(unit),
        "preparation": field(preparation), "optional_when": field(None),
    }


def step(instruction, *ingredient_ids, duration=None, condition=None):
    return {
        "instruction": field(instruction), "duration": field(duration), "condition": field(condition),
        "ingredient_ids": list(ingredient_ids), "tool_ids": [],
    }


def recipe(servings, ingredients, steps):
    return {
        "title": field("Test recipe"), "servings": field(servings),
        "ingredients": ingredients, "steps": steps, "tools": [],
    }


class AdaptationTests(unittest.TestCase):
    def test_scale_updates_only_linked_measures_and_yield_in_steps(self):
        source = recipe("Serves 2", [
            ingredient("milk", "milk", "½", "l", preparation="warm"),
            ingredient("flour", "flour", "500", "g"),
            ingredient("onions", "onions", "2", preparation="sliced"),
        ], [
            step("Mix 500 ml of warm milk, 500 g flour and 2 sliced onions. Bake for 2 minutes at 200 °C.",
                 "milk", "flour", "onions", duration="2 minutes", condition="Once 500 ml of warm milk is ready"),
            step("Divide into 2 servings."),
        ])
        original = deepcopy(source)
        result = adapt_recipe(source, servings=4)
        self.assertEqual(source, original)
        self.assertEqual(result["scale"]["original_servings_source"], "recipe")
        self.assertEqual(result["recipe"]["servings"]["value"], "4")
        milk, flour, onions = result["recipe"]["ingredients"]
        self.assertEqual((milk["quantity"]["value"], milk["unit"]["value"]), ("1", "l"))
        self.assertEqual((flour["quantity"]["value"], flour["unit"]["value"]), ("1", "kg"))
        self.assertEqual(onions["quantity"]["value"], "4")
        first, second = result["recipe"]["steps"]
        self.assertEqual(
            first["instruction"]["value"],
            "Mix 1 l of warm milk, 1 kg flour and 4 sliced onions. Bake for 2 minutes at 200 °C.",
        )
        self.assertEqual(first["duration"]["value"], "2 minutes")
        self.assertEqual(first["condition"]["value"], "Once 1 l of warm milk is ready")
        self.assertEqual(second["instruction"]["value"], "Divide into 4 servings.")
        self.assertEqual(first["instruction"]["status"], "derived")
        self.assertIsNone(first["instruction"]["evidence"])
        self.assertTrue(source["steps"][0]["instruction"]["evidence"])

    def test_requires_positive_original_yield_when_unknown_or_ambiguous(self):
        for servings in (None, "4–6", "0"):
            with self.subTest(servings=servings):
                source = recipe(servings, [], [])
                with self.assertRaisesRegex(ValueError, "--original-servings"):
                    adapt_recipe(source, servings=6)
                with self.assertRaisesRegex(ValueError, "positive"):
                    adapt_recipe(source, servings=6, original_servings=0)
                result = adapt_recipe(source, servings=6, original_servings=4)
                self.assertEqual(result["scale"]["original_servings_source"], "user")
                self.assertEqual(result["scale"]["factor"], "3/2")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            adapt_recipe(recipe("4 Personen", [], []), servings=6, original_servings=5)

    def test_does_not_assign_unknown_measures_or_edit_a_nonmatching_step_amount(self):
        source = recipe("2", [
            ingredient("salt", "salt", "to taste"),
            ingredient("pepper", "pepper"),
            ingredient("oil", "oil", "2", "tbsp"),
            ingredient("oats", "oats", None, "g"),
        ], [
            step("Add 1 tbsp oil and season with salt.", "oil", "salt"),
        ])
        source["ingredients"][3]["quantity"] = field(None, "needs_review")
        result = adapt_recipe(source, servings=4)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], source["steps"][0]["instruction"]["value"])
        quantities = [item["quantity"]["value"] for item in result["recipe"]["ingredients"]]
        self.assertEqual(quantities, ["to taste", None, "4", None])
        self.assertEqual(
            {item["path"] for item in result["review_items"]},
            {"/ingredients/0/quantity", "/ingredients/3", "/steps/0/instruction"},
        )

    def test_leaves_numeric_seasoning_and_frying_oil_for_manual_review(self):
        source = recipe("2", [
            ingredient("salt", "sea salt", "2", "tsp"),
            ingredient("pepper", "Pfeffer", "1", "g"),
            ingredient("oil", "oil", "1", "l"),
            ingredient("olive-oil", "olive oil", "50", "ml"),
            ingredient("potatoes", "potatoes", "400", "g"),
        ], [
            step("Season 400 g potatoes with 2 tsp sea salt and 1 g Pfeffer. "
                 "Fry them in 1 l oil at 180 °C for 12 minutes.",
                 "salt", "pepper", "oil", "potatoes", duration="12 minutes"),
            step("Mix in 50 ml olive oil.", "olive-oil"),
        ])
        result = adapt_recipe(source, servings=4)
        adapted = result["recipe"]
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(
            [item["quantity"]["value"] for item in adapted["ingredients"]],
            ["2", "1", "1", "100", "800"],
        )
        self.assertEqual(
            adapted["steps"][0]["instruction"]["value"],
            "Season 800 g potatoes with 2 tsp sea salt and 1 g Pfeffer. "
            "Fry them in 1 l oil at 180 °C for 12 minutes.",
        )
        self.assertEqual(adapted["steps"][1]["instruction"]["value"], "Mix in 100 ml olive oil.")
        self.assertEqual(
            {item["path"] for item in result["review_items"]},
            {f"/ingredients/{i}/quantity" for i in range(3)},
        )

    def test_flags_pan_oil_from_extracted_purpose(self):
        source = recipe("2", [
            ingredient("pan-oil", "canola oil", "10", "ml", preparation="for greasing"),
        ], [step("Use 10 ml canola oil.", "pan-oil")])
        result = adapt_recipe(source, servings=4)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["recipe"]["ingredients"][0]["quantity"]["value"], "10")
        self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], "Use 10 ml canola oil.")

    def test_vegan_and_scaling_update_ingredient_and_instructions_together(self):
        source = recipe("2", [
            ingredient("bacon", "bacon", "200", "g"),
            ingredient("butter", "butter", "50", "g"),
            ingredient("eggs", "eggs", "2"),
        ], [
            step("Render 200 g bacon in 50 g butter.", "bacon", "butter"),
            step("Add 2 eggs.", "eggs"),
        ])
        result = adapt_recipe(source, servings=3, vegan=True)
        bacon, butter, _ = result["recipe"]["ingredients"]
        self.assertEqual(bacon["name"]["value"], "smoked tofu")
        self.assertEqual(butter["name"]["value"], "vegan butter")
        instruction = result["recipe"]["steps"][0]["instruction"]["value"].casefold()
        self.assertIn("300 g smoked tofu", instruction)
        self.assertIn("75 g vegan butter", instruction)
        self.assertNotIn("render", instruction)
        self.assertEqual(result["recipe"]["steps"][0]["ingredient_ids"], ["bacon", "butter"])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["recipe"]["steps"][1]["instruction"]["value"], "Add 3 eggs.")

    def test_numeric_units_ranges_and_exact_fractions(self):
        cases = [
            ("½", "l", Fraction(3, 2), ("750", "ml")),
            ("0,75", "kg", Fraction(2), ("1,5", "kg")),
            ("250", "g", Fraction(6), ("1.5", "kg")),
            ("2–3", "kg", Fraction(1, 4), ("500–750", "g")),
            ("1 1/2", "cups", Fraction(1, 2), ("3/4", "cups")),
            ("1", "cup", Fraction(1, 3), ("1/3", "cup")),
            ("as needed", "g", Fraction(2), None),
            ("1.000", "g", Fraction(2), None),
        ]
        for quantity, unit, factor, expected in cases:
            with self.subTest(quantity=quantity, unit=unit, factor=factor):
                self.assertEqual(scale_quantity(quantity, unit, factor), expected)

    def test_vegan_roast_uses_a_plant_sauce_and_adjusts_meat_only_steps(self):
        source = recipe(None, [ingredient("roast", "Bratenstücke")], [
            step("gießt man es kalt darauf.", "roast",
                 condition="Soll der Braten nach 8 Tagen zubereitet werden"),
            step("Für die Soße schwitzt man den Bratenfond mit Mehl an.", "roast"),
            step("Man legt den Braten eine Stunde vor dem Anrichten in die Soße.", "roast",
                 duration="eine Stunde"),
        ])
        adapted = adapt_recipe(source, vegan=True)["recipe"]
        self.assertIsNone(adapted["servings"]["value"])
        self.assertIn("Seitan", adapted["steps"][0]["instruction"]["value"])
        self.assertIsNone(adapted["steps"][0]["condition"]["value"])
        self.assertIn("pflanzlichen Bratensatz", adapted["steps"][1]["instruction"]["value"])
        self.assertIsNone(adapted["steps"][2]["duration"]["value"])
        self.assertEqual(source["steps"][2]["duration"]["value"], "eine Stunde")
