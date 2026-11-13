import unittest
from copy import deepcopy
from fractions import Fraction

from cookbook_ocr.adaptation import adapt_recipe
from cookbook_ocr.quantities import quantity_matches, scale_quantity
from cookbook_ocr.review import review_recipe, reviewed_step_links
from cookbook_ocr.scaling_plan import validate_scaling_plan
from cookbook_ocr.vegan import validate_vegan_plan


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


def vegan_plan(source, changes, review_items=()):
    plan = validate_vegan_plan(source, {
        "status": "ready", "reason_code": None, "reason": None,
        "changes": [
            {"path": path, "before": before, "after": after, "reason": "Use a plant-based technique or ingredient."}
            for path, before, after in changes
        ],
        "review_items": [{"path": path, "reason": reason} for path, reason in review_items],
    })
    if plan["status"] == "ready":
        plan["verification"] = {
            "provider": "gemini", "status": "clear", "reason_code": None, "reason": None, "findings": [],
            "approved_substitutions": [
                {"path": change["path"], "ingredient": change["after"]} for change in plan["changes"]
                if change["path"].startswith("/ingredients/") and change["path"].endswith("/name")
            ],
        }
    return plan


def scaling_rule(ingredient_id, mode="proportional", *, reason=None, batch_servings=None, evidence=()):
    return {
        "ingredient_id": ingredient_id, "mode": mode,
        "reason": reason or "Scale with the servings.", "batch_servings": batch_servings,
        "evidence": [{"path": path, "quote": quote} for path, quote in evidence],
    }


def scaling_plan(source, *rules, variant="original"):
    return {variant: validate_scaling_plan(source, {"rules": list(rules)})}


class AdaptationTests(unittest.TestCase):
    def test_scales_portuguese_contractions_and_traditional_spoon_units(self):
        for unit, plural in (("colher de sopa", "colheres de sopa"), ("colher (sopa)", "colheres (sopa)")):
            with self.subTest(unit=unit):
                source = recipe("4", [
                    ingredient("flour", "farinha", "100", "g"),
                    ingredient("sugar", "açúcar", "1", "colher (sopa)"),
                ], [step(f"Adicione 100 g da farinha e 1 {unit} do açúcar.", "flour", "sugar")])
                result = adapt_recipe(source, servings=8, scaling_plan=scaling_plan(
                    source, scaling_rule("flour"), scaling_rule("sugar"),
                ))
                self.assertEqual(result["status"], "applied")
                self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"],
                                 f"Adicione 200 g da farinha e 2 {plural} do açúcar.")
        self.assertTrue(quantity_matches("2", "colheres (sopa)", "2", "colheres de sopa"))

    def test_scales_indefinite_article_with_an_explicit_unit(self):
        source = recipe("4", [ingredient("flour", "flour", "1", "cup")], [step("Add a cup of flour.", "flour")])
        result = adapt_recipe(source, servings=8, scaling_plan=scaling_plan(source, scaling_rule("flour")))
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], "Add 2 cups of flour.")

    def test_warns_about_unmatched_measures_without_rewriting_other_parts(self):
        for written in ("Add one cup of flour.", "Add 1 cup of sifted flour."):
            with self.subTest(written=written):
                source = recipe("4", [ingredient("flour", "flour", "2", "cups")], [
                    step(written, "flour"), step("Add 1 cup of flour.", "flour"),
                ])
                result = adapt_recipe(source, servings=8, scaling_plan=scaling_plan(source, scaling_rule("flour")))
                self.assertEqual(result["status"], "needs_review")
                self.assertEqual(result["recipe"]["steps"], source["steps"])
                self.assertEqual({item["path"] for item in result["review_items"]},
                                 {"/steps/0/instruction", "/steps/1/instruction"})

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
        plan = scaling_plan(source, scaling_rule("milk"), scaling_rule("flour"), scaling_rule("onions"))
        result = adapt_recipe(source, servings=4, scaling_plan=plan)
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

    def test_scales_exact_step_measures_when_ocr_omits_a_link_or_wraps_a_word(self):
        source = recipe("1", [
            ingredient("wine", "Rot-\nwein", "½", "l"),
            ingredient("water", "Wasser", "½", "l"),
        ], [
            step("Wasser: ½ l; dann ½ l Rot-\nwein dazugießen.", "water"),
        ])
        plan = scaling_plan(source, scaling_rule("wine"), scaling_rule("water"))

        five = adapt_recipe(source, servings=5, scaling_plan=plan)["recipe"]
        six = adapt_recipe(source, servings=6, scaling_plan=plan)["recipe"]

        self.assertEqual(five["steps"][0]["instruction"]["value"], "Wasser: 2½ l; dann 2½ l Rot-\nwein dazugießen.")
        self.assertEqual(six["steps"][0]["instruction"]["value"], "Wasser: 3 l; dann 3 l Rot-\nwein dazugießen.")
        self.assertEqual([item["quantity"]["value"] for item in five["ingredients"]], ["2½", "2½"])
        self.assertEqual([item["quantity"]["value"] for item in six["ingredients"]], ["3", "3"])
        self.assertEqual(six["steps"][0]["ingredient_ids"], ["water"])

    def test_does_not_guess_unlinked_step_measure_with_multiple_matching_ingredients(self):
        source = recipe("1", [
            ingredient("first", "milk", "1", "cup"),
            ingredient("second", "milk", "1", "cup"),
        ], [step("Add 1 cup milk.")])
        plan = scaling_plan(source, scaling_rule("first"), scaling_rule("second"))
        result = adapt_recipe(source, servings=4, scaling_plan=plan)
        self.assertEqual(result["recipe"]["steps"][0], source["steps"][0])
        self.assertEqual([item["path"] for item in result["review_items"]], ["/steps/0/instruction"])

    def test_preserves_an_explicitly_removed_step_link(self):
        source = recipe("1", [ingredient("milk", "milk", "1", "cup")], [step("Add 1 cup milk.", "milk")])
        review = review_recipe(source, [{"path": "/steps/0/ingredient_ids", "action": "correct", "value": []}])
        reviewed = review["recipe"]
        result = adapt_recipe(reviewed, servings=4, scaling_plan=scaling_plan(reviewed, scaling_rule("milk")),
                              reviewed_step_links=reviewed_step_links(review["decisions"]))
        self.assertEqual(result["recipe"]["steps"][0], reviewed["steps"][0])

    def test_name_followed_by_colon_does_not_turn_a_duration_into_an_ingredient_count(self):
        source = recipe("1", [ingredient("onions", "Zwiebeln", "4")], [
            step("Zwiebeln: 4 Minuten dünsten.", "onions", duration="4 Minuten"),
        ])
        result = adapt_recipe(source, servings=5, scaling_plan=scaling_plan(source, scaling_rule("onions")))
        self.assertEqual(result["recipe"]["ingredients"][0]["quantity"]["value"], "20")
        self.assertEqual(result["recipe"]["steps"], source["steps"])

    def test_requires_positive_original_yield_when_unknown_or_ambiguous(self):
        for servings in (None, "4–6", "0"):
            with self.subTest(servings=servings):
                source = recipe(servings, [], [])
                with self.assertRaisesRegex(ValueError, "--original-servings"):
                    adapt_recipe(source, servings=6)
                with self.assertRaisesRegex(ValueError, "positive"):
                    adapt_recipe(source, servings=6, original_servings=0)
                result = adapt_recipe(source, servings=6, original_servings=4, scaling_plan=scaling_plan(source))
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
        plan = scaling_plan(source, scaling_rule("salt", "review", reason="Season to taste."),
                            scaling_rule("pepper", "review", reason="The source gives no quantity."),
                            scaling_rule("oil"), scaling_rule("oats", "review", reason="Resolve the disputed amount."))
        result = adapt_recipe(source, servings=4, scaling_plan=plan)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], source["steps"][0]["instruction"]["value"])
        quantities = [item["quantity"]["value"] for item in result["recipe"]["ingredients"]]
        self.assertEqual(quantities, ["to taste", None, "4", None])
        self.assertEqual(
            {item["path"] for item in result["review_items"]},
            {"/ingredients/0/quantity", "/ingredients/1/quantity", "/ingredients/3", "/steps/0/instruction"},
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
        plan = scaling_plan(source, scaling_rule("salt", "review", reason="Check the seasoning by taste."),
                            scaling_rule("pepper", "review", reason="Check the seasoning by taste."),
                            scaling_rule("oil", "review", reason="Check the oil level in the frying vessel."),
                            scaling_rule("olive-oil"), scaling_rule("potatoes"))
        result = adapt_recipe(source, servings=4, scaling_plan=plan)
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
        plan = scaling_plan(source, scaling_rule("pan-oil", "review", reason="Check the pan coverage.",
                                               evidence=(("/ingredients/0/preparation", "for greasing"),)))
        result = adapt_recipe(source, servings=4, scaling_plan=plan)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["recipe"]["ingredients"][0]["quantity"]["value"], "10")
        self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], "Use 10 ml canola oil.")

    def test_vegan_and_scaling_update_ingredient_and_instructions_together(self):
        source = recipe("2", [
            ingredient("bacon", "bacon", "200", "g"),
            ingredient("butter", "butter", "50", "g"),
            ingredient("salt", "salt", "2", "g"),
        ], [
            step("Render 200 g bacon in 50 g butter.", "bacon", "butter"),
            step("Add 2 g salt.", "salt"),
        ])
        plan = vegan_plan(source, [
            ("/ingredients/0/name", "bacon", "smoked tofu"),
            ("/ingredients/1/name", "butter", "vegan butter"),
            ("/steps/0/instruction", "Render 200 g bacon in 50 g butter.",
             "Brown 200 g smoked tofu in 50 g vegan butter."),
        ])
        vegan_recipe = adapt_recipe(source, vegan=True, vegan_plan=plan)["recipe"]
        scale_plan = scaling_plan(vegan_recipe, scaling_rule("bacon"), scaling_rule("butter"),
                                  scaling_rule("salt"), variant="vegan")
        result = adapt_recipe(source, servings=3, vegan=True, vegan_plan=plan, scaling_plan=scale_plan)
        bacon, butter, _ = result["recipe"]["ingredients"]
        self.assertEqual(bacon["name"]["value"], "smoked tofu")
        self.assertEqual(butter["name"]["value"], "vegan butter")
        instruction = result["recipe"]["steps"][0]["instruction"]["value"].casefold()
        self.assertIn("300 g smoked tofu", instruction)
        self.assertIn("75 g vegan butter", instruction)
        self.assertNotIn("render", instruction)
        self.assertEqual(result["recipe"]["steps"][0]["ingredient_ids"], ["bacon", "butter"])
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["recipe"]["steps"][1]["instruction"]["value"], "Add 3 g salt.")

    def test_vegan_adaptation_rejects_a_partial_plan_with_a_remaining_animal_ingredient(self):
        source = recipe("2", [ingredient("bacon", "bacon"), ingredient("eggs", "eggs")], [
            step("Brown bacon.", "bacon"), step("Add eggs.", "eggs"),
        ])
        plan = vegan_plan(source, [
            ("/ingredients/0/name", "bacon", "smoked tofu"),
            ("/steps/0/instruction", "Brown bacon.", "Brown smoked tofu."),
        ], [("/ingredients/1/name", "Check how the eggs function in this recipe.")])
        self.assertEqual(plan["status"], "needs_review")
        self.assertEqual(plan["changes"], [])
        with self.assertRaisesRegex(ValueError, "Check how the eggs function"):
            adapt_recipe(source, vegan=True, vegan_plan=plan)

    def test_vegan_sauerbraten_in_english_keeps_substitutions_consistent(self):
        source = recipe(None, [
            ingredient("fat", "butter"),
            ingredient("drippings", "beef drippings"),
            ingredient("wine", "red wine"),
        ], [step("Add butter, beef drippings and red wine.", "fat", "drippings", "wine")])
        source["title"] = field("Sauerbraten")
        plan = vegan_plan(source, [
            ("/ingredients/0/name", "butter", "vegan butter"),
            ("/ingredients/1/name", "beef drippings", "plant-based pan juices"),
            ("/ingredients/2/name", "red wine", "vegan red wine"),
            ("/steps/0/instruction", "Add butter, beef drippings and red wine.",
             "Add vegan butter, plant-based pan juices and vegan red wine."),
        ])
        result = adapt_recipe(source, vegan=True, vegan_plan=plan)
        adapted = result["recipe"]
        self.assertEqual(adapted["ingredients"][0]["name"]["value"], "vegan butter")
        self.assertEqual(
            adapted["steps"][0]["instruction"]["value"],
            "Add vegan butter, plant-based pan juices and vegan red wine.",
        )
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(
            {item["path"] for item in result["review_items"]},
            {f"/ingredients/{index}/quantity" for index in range(3)},
        )
        for original, replacement in zip(source["ingredients"], adapted["ingredients"]):
            self.assertEqual(replacement["quantity"], original["quantity"])
            self.assertEqual(replacement["unit"], original["unit"])

    def test_vegan_scaling_preserves_disputed_quantity_and_scales_other_measures(self):
        source = recipe("2", [
            ingredient("fat", "butter"),
            ingredient("onions", "onions", "2"),
        ], [step("Sauté 2 onions in butter.", "onions", "fat")])
        source["ingredients"][0]["quantity"].update(
            status="needs_review", evidence={"source": "A", "page": "1", "quote": "[unreadable] butter"},
            review_item_ids=[1],
        )
        plan = vegan_plan(source, [
            ("/ingredients/0/name", "butter", "vegan butter"),
            ("/steps/0/instruction", "Sauté 2 onions in butter.", "Sauté 2 onions in vegan butter."),
        ])
        self.assertEqual(plan["status"], "ready")
        vegan = adapt_recipe(source, vegan=True, vegan_plan=plan)["recipe"]
        scale = scaling_plan(vegan, scaling_rule("fat", "review"), scaling_rule("onions"), variant="vegan")
        result = adapt_recipe(source, servings=4, vegan=True, vegan_plan=plan, scaling_plan=scale)
        fat, onions = result["recipe"]["ingredients"]
        self.assertEqual(fat["name"]["value"], "vegan butter")
        self.assertEqual(fat["quantity"], source["ingredients"][0]["quantity"])
        self.assertEqual(fat["unit"], source["ingredients"][0]["unit"])
        self.assertEqual(onions["quantity"]["value"], "4")
        self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], "Sauté 4 onions in vegan butter.")
        self.assertIn("/ingredients/0/quantity", {item["path"] for item in result["review_items"]})

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

    def test_scales_mixed_fraction_total_divided_across_linked_steps(self):
        source = recipe("2", [ingredient("flour", "flour", "1½", "cups")], [
            step("Add 1 cup flour and mix for 2 minutes.", "flour", duration="2 minutes"),
            step("Add the remaining ½ cup flour.", "flour", condition="Once ½ cup flour is ready"),
        ])
        result = adapt_recipe(source, servings=6, scaling_plan=scaling_plan(source, scaling_rule("flour")))
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["recipe"]["ingredients"][0]["quantity"]["value"], "4½")
        first, second = result["recipe"]["steps"]
        self.assertEqual(first["instruction"]["value"], "Add 3 cups flour and mix for 2 minutes.")
        self.assertEqual(first["duration"]["value"], "2 minutes")
        self.assertEqual(second["instruction"]["value"], "Add the remaining 1½ cups flour.")
        self.assertEqual(second["condition"]["value"], "Once 1½ cups flour is ready")
        self.assertEqual(source["steps"][1]["instruction"]["value"], "Add the remaining ½ cup flour.")

    def test_reconciles_split_amounts_in_compatible_metric_units(self):
        source = recipe("2", [ingredient("milk", "milk", "1", "l")], [
            step("Pour in 250 ml milk.", "milk"),
            step("Add ¾ l milk.", "milk"),
        ])
        result = adapt_recipe(source, servings=4, scaling_plan=scaling_plan(source, scaling_rule("milk")))
        self.assertEqual(result["status"], "applied")
        self.assertEqual(
            [item["instruction"]["value"] for item in result["recipe"]["steps"]],
            ["Pour in 500 ml milk.", "Add 1½ l milk."],
        )

    def test_leaves_all_step_amounts_when_allocations_are_ambiguous(self):
        for second_amount in ("1 cup", "2 cups", "250 g"):
            with self.subTest(second_amount=second_amount):
                source = recipe("2", [ingredient("flour", "flour", "2", "cups")], [
                    step("Add 2 cups flour.", "flour"),
                    step(f"Add {second_amount} flour.", "flour"),
                ])
                result = adapt_recipe(source, servings=4, scaling_plan=scaling_plan(source, scaling_rule("flour")))
                self.assertEqual(result["recipe"]["ingredients"][0]["quantity"]["value"], "4")
                self.assertEqual(result["recipe"]["steps"], source["steps"])
                self.assertEqual(result["status"], "needs_review")
                self.assertEqual(
                    {item["path"] for item in result["review_items"]},
                    {"/steps/0/instruction", "/steps/1/instruction"},
                )

    def test_uses_fixed_and_per_batch_rules_across_serving_counts(self):
        method = "Use 20 ml oil per batch of 4 servings, then mix in 200 g flour."
        fixed_method = "Use 50 ml water once for the whole recipe."
        source = recipe("4", [
            ingredient("oil", "oil", "20", "ml"),
            ingredient("flour", "flour", "200", "g"),
            ingredient("water", "water", "50", "ml"),
        ], [step(method, "oil", "flour"), step(fixed_method, "water")])
        plan = scaling_plan(source,
                            scaling_rule("oil", "per_batch", reason="Measure once per batch.", batch_servings=4,
                                         evidence=(("/steps/0/instruction", "20 ml oil per batch of 4 servings"),)),
                            scaling_rule("flour"),
                            scaling_rule("water", "fixed", reason="Measure once for the recipe.",
                                         evidence=(("/steps/1/instruction", fixed_method),)))
        for servings, oil, flour, batches in ((2, "20", "100", 1), (6, "40", "300", 2), (9, "60", "450", 3)):
            with self.subTest(servings=servings):
                result = adapt_recipe(source, servings=servings, scaling_plan=plan)
                self.assertEqual([item["quantity"]["value"] for item in result["recipe"]["ingredients"]],
                                 [oil, flour, "50"])
                oil_rule, _, water_rule = result["scale"]["ingredients"]
                self.assertEqual(oil_rule["multiplier"], str(batches))
                self.assertEqual(oil_rule["batches"], batches)
                self.assertEqual(oil_rule["per_batch_quantity"], "20")
                self.assertEqual(water_rule["multiplier"], "1")
                self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], method)
                self.assertIn("/steps/0/instruction", {item["path"] for item in result["review_items"]})
                self.assertEqual(result["recipe"]["steps"][1]["instruction"]["value"], fixed_method)

    def test_unsupported_nonlinear_classifications_hold_original_quantities(self):
        method = "Cook in batches of 4 servings. Add 20 ml oil and 50 ml water."
        source = recipe("4", [ingredient("oil", "oil", "20", "ml"), ingredient("water", "water", "50", "ml")],
                        [step(method, "oil", "water")])
        plan = scaling_plan(source,
                            scaling_rule("oil", "per_batch", reason="Assume a batch amount.", batch_servings=4,
                                         evidence=(("/steps/0/instruction", method),)),
                            scaling_rule("water", "fixed", reason="Assume a fixed amount.",
                                         evidence=(("/ingredients/1/quantity", "50"),)))
        self.assertEqual([rule["mode"] for rule in plan["original"]["rules"]], ["review", "review"])
        result = adapt_recipe(source, servings=9, scaling_plan=plan)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["recipe"]["ingredients"], source["ingredients"])
        self.assertEqual(result["recipe"]["steps"], source["steps"])

    def test_scaling_rejects_invented_evidence_and_plans_bound_to_stale_amounts(self):
        source = recipe("4", [ingredient("oil", "oil", "20", "ml")], [step("Add 20 ml oil.", "oil")])
        with self.assertRaisesRegex(ValueError, "quote verified text"):
            scaling_plan(source, scaling_rule("oil", "fixed", reason="Measure once for the recipe.",
                                             evidence=(("/steps/0/instruction", "Add 20 ml oil once for the whole recipe."),)))
        plan = scaling_plan(source, scaling_rule("oil"))
        source["ingredients"][0]["quantity"] = field("30", status="reviewed")
        with self.assertRaisesRegex(ValueError, "current scaling plan"):
            adapt_recipe(source, servings=9, scaling_plan=plan)

    def test_vegan_roast_uses_a_plant_sauce_and_adjusts_meat_only_steps(self):
        source = recipe(None, [ingredient("roast", "Bratenstücke")], [
            step("gießt man es kalt darauf.", "roast",
                 condition="Soll der Braten nach 8 Tagen zubereitet werden"),
            step("Für die Soße schwitzt man den Bratenfond mit Mehl an.", "roast"),
            step("Man legt den Braten eine Stunde vor dem Anrichten in die Soße.", "roast",
                 duration="eine Stunde"),
        ])
        plan = vegan_plan(source, [
            ("/ingredients/0/name", "Bratenstücke", "Seitan"),
            ("/steps/0/instruction", "gießt man es kalt darauf.", "Gieße die kalte Marinade über den Seitan."),
            ("/steps/0/condition", "Soll der Braten nach 8 Tagen zubereitet werden", None),
            ("/steps/1/instruction", "Für die Soße schwitzt man den Bratenfond mit Mehl an.",
             "Für die Soße schwitzt man den pflanzlichen Bratensatz mit Mehl an."),
            ("/steps/2/instruction", "Man legt den Braten eine Stunde vor dem Anrichten in die Soße.",
             "Den Seitan vor dem Anrichten in der Soße erwärmen."),
            ("/steps/2/duration", "eine Stunde", None),
        ])
        adapted = adapt_recipe(source, vegan=True, vegan_plan=plan)["recipe"]
        self.assertIsNone(adapted["servings"]["value"])
        self.assertIn("Seitan", adapted["steps"][0]["instruction"]["value"])
        self.assertIsNone(adapted["steps"][0]["condition"]["value"])
        self.assertIn("pflanzlichen Bratensatz", adapted["steps"][1]["instruction"]["value"])
        self.assertIsNone(adapted["steps"][2]["duration"]["value"])
        self.assertEqual(source["steps"][2]["duration"]["value"], "eine Stunde")

    def test_vegan_plan_cannot_be_used_after_source_changes(self):
        source = recipe("2", [ingredient("meat", "beef")], [step("Brown the beef.", "meat")])
        plan = vegan_plan(source, [
            ("/ingredients/0/name", "beef", "seitan"),
            ("/steps/0/instruction", "Brown the beef.", "Brown the seitan."),
        ])
        source["ingredients"][0]["name"]["value"] = "lamb"
        with self.assertRaisesRegex(ValueError, "plan"):
            adapt_recipe(source, vegan=True, vegan_plan=plan)

    def test_vegan_plan_withholds_numeric_measure_change(self):
        source = recipe("2", [ingredient("milk", "milk", "2", "cups")], [step("Add 2 cups milk.", "milk")])
        plan = vegan_plan(source, [("/steps/0/instruction", "Add 2 cups milk.", "Add 2 tablespoons oat milk.")])
        self.assertEqual(plan["status"], "needs_review")
        self.assertEqual(plan["changes"], [])
        with self.assertRaisesRegex(ValueError, "change a measure"):
            adapt_recipe(source, vegan=True, vegan_plan=plan)
        self.assertEqual(source["steps"][0]["instruction"]["value"], "Add 2 cups milk.")

    def test_vegan_plan_withholds_an_invented_amount_when_source_omits_it(self):
        source = recipe("2", [ingredient("milk", "milk")], [step("Add milk.", "milk")])
        plan = vegan_plan(source, [
            ("/ingredients/0/name", "milk", "oat milk"),
            ("/steps/0/instruction", "Add milk.", "Add 2 cups oat milk."),
        ])
        self.assertEqual(plan["status"], "needs_review")
        self.assertEqual(plan["changes"], [])
        with self.assertRaisesRegex(ValueError, "change a measure"):
            adapt_recipe(source, vegan=True, vegan_plan=plan)
