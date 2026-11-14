import unittest
from copy import deepcopy
from unittest.mock import Mock

from cookbook_ocr.adaptation import adapt_recipe
from cookbook_ocr.pipeline import _classify_scaling
from cookbook_ocr.review import review_recipe
from cookbook_ocr.scaling_plan import rebase_scaling_plan, scaling_plan_matches, validate_scaling_plan
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
    def test_recovers_valid_rules_across_provider_attempts_and_flags_other_ingredients(self):
        source = recipe("2", [ingredient("water", "water", "½", "l"), ingredient("flour", "flour", "100", "g"),
                              ingredient("salt", "salt", "2", "g")],
                        [step("Add ½ l water and 100 g flour, then season with 2 g salt.", "water", "flour", "salt")])
        wrong_quote = scaling_rule("salt", evidence=(("/steps/0/instruction", "12 g salt"),))
        respond = Mock(side_effect=[
            {"rules": [scaling_rule("water"), wrong_quote]},
            {"rules": [scaling_rule("flour"), wrong_quote]},
        ])

        with self.assertLogs("cookbook_ocr.pipeline", level="WARNING"):
            plan = _classify_scaling(source, "Luna", respond)
        self.assertEqual(respond.call_count, 2)
        self.assertEqual(plan["status"], "needs_review")
        self.assertEqual([rule["mode"] for rule in plan["rules"]], ["proportional", "proportional", "review"])
        self.assertIn("quote verified text", plan["rules"][2]["reason"])

        result = adapt_recipe(source, servings=3, scaling_plan={"original": plan})
        self.assertEqual([item["quantity"]["value"] for item in result["recipe"]["ingredients"]], ["750", "150", "2"])
        self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"],
                         "Add 750 ml water and 150 g flour, then season with 2 g salt.")
        self.assertEqual([item["path"] for item in result["review_items"]], ["/ingredients/2/quantity"])

    def test_recovers_flattened_whitespace_without_accepting_invented_evidence(self):
        original = "Use 20 ml oil once\nfor the whole recipe."
        source = recipe("4", [ingredient("oil", "oil", "20", "ml")], [step(original, "oil")])
        fixed = scaling_rule("oil", "fixed", evidence=(("/steps/0/instruction", original.replace("\n", " ")),))
        plan = validate_scaling_plan(source, {"rules": [fixed]})
        self.assertEqual(plan["rules"][0]["mode"], "fixed")
        self.assertEqual(plan["rules"][0]["evidence"][0]["quote"], original)

    def test_holds_reviewed_quantity_until_the_linked_step_agrees(self):
        source = recipe("2", [ingredient("onions", "onions", "4"), ingredient("salt", "salt", "2", "g")],
                        [step("Add 4 onions and 2 g salt.", "onions", "salt")])
        quantity = {"path": "/ingredients/0/quantity", "action": "correct", "value": "5"}
        instruction = {"path": "/steps/0/instruction", "action": "correct", "value": "Add 5 onions and 2 g salt."}
        rules = [scaling_rule("onions"), scaling_rule("salt")]

        reviewed = review_recipe(source, [quantity])["recipe"]
        result = adapt_recipe(reviewed, servings=4, scaling_plan=verified_plan(reviewed, rules, rules))
        self.assertEqual([item["quantity"]["value"] for item in result["recipe"]["ingredients"]], ["5", "4"])
        self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], "Add 4 onions and 4 g salt.")
        self.assertEqual(result["scale"]["ingredients"][0]["mode"], "review")
        self.assertEqual([item["path"] for item in result["review_items"]], ["/steps/0/instruction"])

        reviewed = review_recipe(source, [quantity, instruction])["recipe"]
        result = adapt_recipe(reviewed, servings=4, scaling_plan=verified_plan(reviewed, rules, rules))
        self.assertEqual([item["quantity"]["value"] for item in result["recipe"]["ingredients"]], ["10", "4"])
        self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], "Add 10 onions and 4 g salt.")

    def test_title_corrections_reuse_rules_but_yield_corrections_require_revalidation(self):
        source = recipe("2", [ingredient("water", "water", "½", "l")], [step("Add ½ l water.", "water")])
        plan = validate_scaling_plan(source, {"rules": [scaling_rule("water")]})
        revised = review_recipe(source, [{"path": "/title", "action": "correct", "value": "New title"}])["recipe"]
        self.assertTrue(scaling_plan_matches(revised, plan))
        revised["servings"]["value"] = "3"
        self.assertFalse(scaling_plan_matches(revised, plan))

    def test_scales_added_and_corrected_quantities_with_unitless_counts(self):
        text = "Coloque numa panela a água, o açúcar, o cravo e a canela."
        source = recipe("1", [
            ingredient("clove", "cravo"), ingredient("cinnamon", "canela", "2"),
            ingredient("water", "água", "2", "xícaras (chá)"),
        ], [step(text, "clove", "cinnamon", "water")])
        reviewed = review_recipe(source, [
            {"path": "/ingredients/0/quantity", "action": "correct", "value": "1"},
            {"path": "/ingredients/1/quantity", "action": "correct", "value": "1"},
        ])["recipe"]
        luna = [scaling_rule("clove", "review"), scaling_rule("cinnamon", "review"), scaling_rule("water")]
        gemini = [scaling_rule("clove", "review", evidence=(("/ingredients/0/quantity", "1"),)),
                  scaling_rule("cinnamon"), scaling_rule("water")]

        result = adapt_recipe(reviewed, servings=5, scaling_plan=verified_plan(reviewed, luna, gemini))

        self.assertTrue(all(rule["mode"] == "proportional" for rule in result["scale"]["ingredients"]))
        self.assertEqual([row["quantity"]["value"] for row in result["recipe"]["ingredients"]], ["5", "5", "10"])
        self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], text)
        self.assertEqual([row["quantity"]["value"] for row in reviewed["ingredients"]], ["1", "1", "2"])

    def test_preserves_evidenced_review_and_fixed_rules_for_a_reviewed_amount(self):
        for mode, text in (
            ("review", "Add salt to taste."),
            ("fixed", "Use 3 g salt once for the whole recipe."),
        ):
            with self.subTest(mode=mode):
                source = recipe("2", [ingredient("salt", "salt", None, "g")], [step(text, "salt")])
                reviewed = review_recipe(source, [
                    {"path": "/ingredients/0/quantity", "action": "correct", "value": "3"},
                ])["recipe"]
                rules = [scaling_rule("salt", mode, evidence=(("/steps/0/instruction", text),))]
                result = adapt_recipe(reviewed, servings=4, scaling_plan=verified_plan(reviewed, rules, rules))

                self.assertEqual(result["recipe"]["ingredients"][0]["quantity"]["value"], "3")
                self.assertEqual(result["scale"]["ingredients"][0]["mode"], mode)

    def test_offline_rebase_keeps_unaffected_verified_rules(self):
        source = recipe("2", [ingredient("water", "water", "½", "l"), ingredient("salt", "salt", "2", "g")],
                        [step("Add ½ l water.", "water"), step("Stir in 2 g salt.", "salt")])
        rules = [scaling_rule("water"), scaling_rule("salt")]
        plan = verified_plan(source, rules, rules)["original"]
        correction = {"path": "/ingredients/1/quantity", "action": "correct", "value": "3"}
        revised = review_recipe(source, [correction])["recipe"]

        updated = rebase_scaling_plan(source, revised, plan)
        self.assertEqual(updated["recovered_ingredients"], ["salt"])
        self.assertEqual(updated["verification"]["recovered_ingredients"], ["salt"])
        self.assertEqual([rule["mode"] for rule in updated["rules"]], ["proportional", "review"])
        self.assertEqual(updated["verification"]["rules"], updated["rules"])
        result = adapt_recipe(revised, servings=4, scaling_plan={"original": updated})
        self.assertEqual(result["scale"]["verification"]["status"], "completed")
        self.assertEqual([item["quantity"]["value"] for item in result["recipe"]["ingredients"]], ["1", "3"])
        self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], "Add 1 l water.")

    def test_does_not_scale_an_ingredient_with_an_unresolved_linked_instruction(self):
        unresolved = step(None, "oil")
        unresolved["instruction"]["status"] = "needs_review"
        source = recipe("4", [ingredient("oil", "oil", "20", "ml")], [unresolved])
        rule = scaling_rule("oil")

        plan = verified_plan(source, [rule], [rule])
        result = adapt_recipe(source, servings=8, scaling_plan=plan)

        self.assertEqual(plan["original"]["status"], "needs_review")
        self.assertEqual(result["recipe"]["ingredients"][0]["quantity"]["value"], "20")
        self.assertEqual(result["scale"]["ingredients"][0]["mode"], "review")

    def test_agreeing_models_cannot_multiply_a_source_amount_marked_fixed_or_per_batch(self):
        for instruction in ("Use 20 ml oil once for the whole recipe.", "Use 20 ml oil per batch of 4 servings.",
                            "Use 20 ml oil, regardless of servings.", "Use 0.02 l oil once for the whole recipe."):
            with self.subTest(instruction=instruction):
                method = f"{instruction[:-1]}, then add 100 g flour."
                source = recipe("4", [ingredient("oil", "oil", "20", "ml"),
                                      ingredient("flour", "flour", "100", "g")],
                                [step(method, "oil", "flour")])
                rules = [scaling_rule("oil"), scaling_rule("flour")]
                result = adapt_recipe(source, servings=8, scaling_plan=verified_plan(source, rules, rules))
                self.assertEqual(result["status"], "needs_review")
                self.assertEqual(result["scale"]["verification"]["material_conflicts"], [])
                self.assertEqual(result["scale"]["ingredients"][0]["mode"], "review")
                self.assertEqual([item["quantity"]["value"] for item in result["recipe"]["ingredients"]], ["20", "200"])
                self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], method.replace("100 g", "200 g"))

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
