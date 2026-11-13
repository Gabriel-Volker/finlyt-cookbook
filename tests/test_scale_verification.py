import unittest
from copy import deepcopy

from cookbook_ocr.adaptation import adapt_recipe
from cookbook_ocr.scaling_plan import validate_scaling_plan
from test_adaptation import ingredient, recipe, scaling_rule, step


def verified_plan(source, luna_rules, gemini_rules):
    plan = validate_scaling_plan(source, {"rules": luna_rules})
    plan["verification"] = {
        "provider": "gemini",
        "status": "completed",
        "rules": validate_scaling_plan(source, {"rules": gemini_rules})["rules"],
    }
    return {"original": plan}


class ScalingVerificationTests(unittest.TestCase):
    def test_differs_at_new_yield_and_leaves_other_ingredients_scalable(self):
        oil_text = "Use 20 ml oil once for the whole recipe."
        source = recipe("4", [
            ingredient("oil", "oil", "20", "ml"),
            ingredient("flour", "flour", "200", "g"),
        ], [step(f"{oil_text} Add 200 g flour.", "oil", "flour")])
        fixed_oil = scaling_rule("oil", "fixed", evidence=(("/steps/0/instruction", oil_text),))
        gemini_flour = scaling_rule("flour", evidence=(("/servings", "4"),))
        plan = verified_plan(source, [scaling_rule("oil"), scaling_rule("flour")],
                             [fixed_oil, gemini_flour])
        original_plan = deepcopy(plan)

        baseline = adapt_recipe(source, servings=4, scaling_plan=plan)
        self.assertEqual(baseline["scale"]["verification"]["material_conflicts"], [])
        self.assertEqual(baseline["review_items"], [])
        self.assertEqual(baseline["recipe"], source)

        result = adapt_recipe(source, servings=8, scaling_plan=plan)
        self.assertEqual(result["scale"]["verification"]["material_conflicts"], ["oil"])
        oil, flour = result["recipe"]["ingredients"]
        self.assertEqual((oil["quantity"]["value"], flour["quantity"]["value"]), ("20", "400"))
        self.assertEqual([item["instruction"]["value"] for item in result["recipe"]["steps"]],
                         [f"{oil_text} Add 400 g flour."])
        rule = result["scale"]["ingredients"][0]
        self.assertEqual(rule["mode"], "review")
        self.assertIsNone(rule["multiplier"])
        self.assertEqual(rule["verification"]["luna_outcome"]["quantity"], "40")
        self.assertEqual(rule["verification"]["gemini_outcome"]["quantity"], "20")
        self.assertEqual(plan, original_plan)

    def test_same_total_can_require_different_batch_preparation(self):
        method = "Use 20 ml oil per batch of 4 servings."
        source = recipe("4", [ingredient("oil", "oil", "20", "ml")], [step(method, "oil")])
        batched_oil = scaling_rule("oil", "per_batch", batch_servings=4,
                                   evidence=(("/steps/0/instruction", method),))
        plan = verified_plan(source, [scaling_rule("oil")], [batched_oil])
        result = adapt_recipe(source, servings=8, scaling_plan=plan)

        rule = result["scale"]["ingredients"][0]["verification"]
        self.assertEqual(rule["luna_outcome"]["quantity"], rule["gemini_outcome"]["quantity"])
        self.assertEqual(rule["gemini_outcome"]["batches"], 2)
        self.assertEqual(rule["gemini_outcome"]["per_batch_quantity"], "20")
        self.assertTrue(rule["materially_different"])
        self.assertEqual(result["recipe"]["ingredients"][0]["quantity"]["value"], "20")
        self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], method)
        self.assertEqual(len(result["review_items"]), 1)

    def test_zero_total_does_not_hide_a_fixed_vs_batch_difference(self):
        once = "Use 0 ml oil once for the whole recipe."
        each = "Use 0 ml oil per batch of 4 servings."
        source = recipe("4", [ingredient("oil", "oil", "0", "ml")],
                        [step(once, "oil"), step(each, "oil")])
        fixed_oil = scaling_rule("oil", "fixed", evidence=(("/steps/0/instruction", once),))
        batched_oil = scaling_rule("oil", "per_batch", batch_servings=4,
                                   evidence=(("/steps/1/instruction", each),))
        plan = verified_plan(source, [fixed_oil], [batched_oil])
        result = adapt_recipe(source, servings=8, scaling_plan=plan)
        verification = result["scale"]["ingredients"][0]["verification"]
        self.assertEqual((verification["luna_outcome"]["quantity"], verification["gemini_outcome"]["quantity"]),
                         ("0", "0"))
        self.assertTrue(verification["materially_different"])
        self.assertEqual(result["scale"]["verification"]["material_conflicts"], ["oil"])

    def test_fixed_and_one_partial_batch_are_equivalent_at_same_amount(self):
        once = "Use 20 ml oil once for the whole recipe."
        each = "Use 20 ml oil per batch of 4 servings."
        source = recipe("4", [ingredient("oil", "oil", "20", "ml")],
                        [step(once, "oil"), step(each, "oil")])
        fixed_oil = scaling_rule("oil", "fixed", evidence=(("/steps/0/instruction", once),))
        batched_oil = scaling_rule("oil", "per_batch", batch_servings=4,
                                   evidence=(("/steps/1/instruction", each),))
        plan = verified_plan(source, [fixed_oil], [batched_oil])
        result = adapt_recipe(source, servings=2, scaling_plan=plan)

        self.assertEqual(result["scale"]["verification"]["material_conflicts"], [])
        self.assertEqual(result["review_items"], [])
        self.assertFalse(result["scale"]["ingredients"][0]["verification"]["materially_different"])

    def test_review_only_differs_when_other_rule_changes_the_outcome(self):
        method = "Use 20 ml oil once for the whole recipe."
        source = recipe("4", [ingredient("oil", "oil", "20", "ml")], [step(method, "oil")])
        fixed_oil = scaling_rule("oil", "fixed", evidence=(("/steps/0/instruction", method),))
        review_oil = scaling_rule("oil", "review", reason="Check the depth of oil.")
        proportional_oil = scaling_rule("oil")
        for luna, gemini, conflicts, warnings in (
            (fixed_oil, review_oil, [], 0),
            (review_oil, fixed_oil, [], 1),
            (review_oil, review_oil, [], 1),
            (proportional_oil, review_oil, ["oil"], 1),
        ):
            with self.subTest(luna=luna["mode"], gemini=gemini["mode"]):
                plan = verified_plan(source, [luna], [gemini])
                result = adapt_recipe(source, servings=8, scaling_plan=plan)
                self.assertEqual(result["scale"]["verification"]["material_conflicts"], conflicts)
                self.assertEqual(result["recipe"]["ingredients"][0]["quantity"]["value"], "20")
                self.assertEqual(len(result["review_items"]), warnings)

    def test_invalid_saved_verification_leaves_luna_rule_usable(self):
        source = recipe("4", [ingredient("flour", "flour", "200", "g")], [step("Add 200 g flour.", "flour")])
        plan = verified_plan(source, [scaling_rule("flour")], [scaling_rule("flour")])
        plan["original"]["verification"]["rules"].clear()

        result = adapt_recipe(source, servings=8, scaling_plan=plan)

        self.assertEqual(result["scale"]["verification"]["status"], "unavailable")
        self.assertEqual(result["recipe"]["ingredients"][0]["quantity"]["value"], "400")
        self.assertEqual(result["review_items"], [])
