import json
import unittest
from copy import deepcopy
from unittest.mock import Mock

from cookbook_ocr.adaptation import adapt_recipe
from cookbook_ocr.pipeline import plan_vegan_recipe
from cookbook_ocr.providers import ProviderError
from cookbook_ocr.vegan import (
    rebind_vegan_plan,
    validate_vegan_plan,
    vegan_plan_matches,
    vegan_plan_usable,
    vegan_source_review,
)
from cookbook_ocr.vegan_verification import verify_vegan_plan
from test_adaptation import field, ingredient, recipe, step


def proposal(changes=()):
    return {
        "status": "ready", "reason_code": None, "reason": None,
        "changes": list(changes), "review_items": [],
    }


def clear_verdict(*ingredients):
    return {
        "status": "clear", "reason_code": None, "reason": None, "findings": [],
        "approved_substitutions": [
            {"path": f"/ingredients/{index}/name", "ingredient": ingredient}
            for index, ingredient in enumerate(ingredients)
        ],
    }


def finding_verdict(*findings):
    return {
        "status": "needs_review", "reason_code": "verification_failed",
        "reason": "A proposed replacement cannot be used in this recipe.",
        "findings": list(findings), "approved_substitutions": [],
    }


class VeganVerificationTests(unittest.TestCase):
    def setUp(self):
        self.recipe = recipe("4", [ingredient("butter", "butter", "100", "g")], [
            step("Cream the solid butter with sugar until fluffy.", "butter"),
        ])
        self.gemini = Mock()

    def plan(self, replacement):
        return validate_vegan_plan(self.recipe, proposal([
            {"path": "/ingredients/0/name", "before": "butter", "after": replacement,
             "reason": "Use a plant-based fat."},
            {"path": "/steps/0/instruction", "before": "Cream the solid butter with sugar until fluffy.",
             "after": f"Cream the {replacement} with sugar until fluffy.", "reason": "Use the replacement fat."},
        ]))

    def issue(self):
        return {
            "path": "/ingredients/0/name", "kind": "cooking_function",
            "reason": "Neutral oil cannot be creamed with sugar as a solid fat to aerate the batter.",
            "evidence": [
                {"path": "/steps/0/instruction", "quote": "Cream the solid butter with sugar until fluffy."},
                {"path": "/ingredients/0/name", "quote": "neutral oil"},
            ],
        }

    def test_planner_reconsiders_a_stated_timing_before_abstaining(self):
        source = recipe("4", [ingredient("beef", "beef")], [
            step("If serving after 2 days, cover the beef in marinade.", "beef",
                 condition="If serving after 2 days"),
        ])
        blocked = {
            "status": "needs_review", "reason_code": "insufficient_information",
            "reason": "The meat-specific timing is not suitable for a plant roast.", "changes": [],
            "review_items": [{"path": "/steps/0/condition", "reason": "Check whether the timing can change."}],
        }
        adapted = proposal([
            {"path": "/ingredients/0/name", "before": "beef", "after": "seitan roast",
             "reason": "Use an established plant-based roast."},
            {"path": "/steps/0/instruction", "before": "If serving after 2 days, cover the beef in marinade.",
             "after": "Brush the seitan roast with the marinade before searing.", "reason": "Keep the marinade flavor."},
            {"path": "/steps/0/condition", "before": "If serving after 2 days", "after": None,
             "reason": "The plant roast does not require the long soak."},
        ])
        luna = Mock()
        luna.respond.side_effect = [json.dumps(blocked), json.dumps(adapted)]
        self.assertEqual(plan_vegan_recipe(luna, source)["status"], "ready")
        self.assertIn("stated timing is not missing", luna.respond.call_args.kwargs["input"][0]["content"])

        luna.respond.side_effect = [json.dumps(blocked), ProviderError("Unavailable")]
        self.assertEqual(plan_vegan_recipe(luna, source)["review_items"], blocked["review_items"])

    def test_planner_retries_a_proposal_that_keeps_animal_ingredient_in_a_step(self):
        source = self.plan("vegan baking block")
        changes = source["changes"]
        invalid = proposal([changes[0], {**changes[1], "after": "Cream the butter with the vegan baking block."}])
        luna = Mock()
        luna.respond.side_effect = [json.dumps(invalid), json.dumps(proposal(changes))]
        with self.assertLogs("cookbook_ocr.pipeline", level="WARNING"):
            plan = plan_vegan_recipe(luna, self.recipe)
        self.assertEqual(plan["status"], "ready")
        self.assertIn("/steps/0/instruction", luna.respond.call_args.kwargs["input"][0]["content"])

    def test_accepts_inflected_replacement_name_in_a_linked_german_step(self):
        before = "Mit Rotwein ablöschen."
        source = recipe("4", [ingredient("wine", "Rotwein")], [step(before, "wine")])
        changes = [
            {"path": "/ingredients/0/name", "before": "Rotwein", "after": "vegan gekennzeichneter Rotwein",
             "reason": "Use wine confirmed vegan."},
            {"path": "/steps/0/instruction", "before": before, "after": "Mit vegan gekennzeichnetem Rotwein ablöschen.",
             "reason": "Use the confirmed wine."},
        ]
        self.assertEqual(validate_vegan_plan(source, proposal(changes))["status"], "ready")
        changes[1]["after"] = "Mit vegan gekennzeichnetem Rotwein und Rotwein ablöschen."
        self.assertEqual(validate_vegan_plan(source, proposal(changes))["status"], "needs_review")

    def test_preserves_a_workable_substitute_without_review(self):
        source = self.plan("vegan baking block")
        verdict = clear_verdict("vegan baking block")
        self.gemini.respond_structured.return_value = verdict

        verified = verify_vegan_plan(self.gemini, self.recipe, source)

        self.assertEqual(verified["verification"], {"provider": "gemini", **verdict})
        self.assertEqual(verified["changes"], source["changes"])
        self.assertEqual(verified["review_items"], [])
        self.assertTrue(vegan_plan_usable(self.recipe, verified))
        adapted = adapt_recipe(self.recipe, vegan=True, vegan_plan=verified)
        self.assertEqual(adapted["recipe"]["ingredients"][0]["name"]["value"], "vegan baking block")
        prompt, schema, sent = self.gemini.respond_structured.call_args.args
        self.assertIn("Accept different workable ingredients and techniques", prompt)
        self.assertEqual(sent["recipe"], self.recipe)
        self.assertEqual(sent["changes"], source["changes"])
        self.assertEqual(sent["substitutions_to_approve"], verdict["approved_substitutions"])
        self.assertEqual(sent["ingredients_to_check"], verdict["approved_substitutions"])
        approvals_schema = schema["properties"]["approved_substitutions"]
        self.assertEqual(approvals_schema["maxItems"], 1)
        self.assertEqual(approvals_schema["items"]["properties"]["path"]["enum"], ["/ingredients/0/name"])

    def test_blocks_a_concrete_issue_without_mutating_the_proposal(self):
        source = self.plan("neutral oil")
        original = deepcopy(source)
        finding = self.issue()
        self.gemini.respond_structured.return_value = finding_verdict(finding, finding)

        verified = verify_vegan_plan(self.gemini, self.recipe, source)

        self.assertEqual(source, original)
        self.assertEqual(verified["verification"]["findings"], [finding])
        self.assertEqual(verified["verification"]["status"], "needs_review")
        self.assertEqual(verified["status"], "unavailable")
        self.assertEqual(verified["changes"], [])
        self.assertEqual(verified["review_items"], [{"path": finding["path"], "reason": finding["reason"]}])
        with self.assertRaisesRegex(ValueError, "Neutral oil cannot be creamed"):
            adapt_recipe(self.recipe, vegan=True, vegan_plan=verified)

    def test_can_verify_ingredient_identity_without_resolving_the_measure(self):
        for quantity, unit, expected_path in (
            (field(None), field(None), "/ingredients/0/quantity"),
            (field(None, "needs_review"), field(None), "/ingredients/0/quantity"),
            (field("100"), field(None, "needs_review"), "/ingredients/0/unit"),
        ):
            with self.subTest(path=expected_path, quantity_status=quantity["status"]):
                self.recipe["ingredients"][0].update(quantity=quantity, unit=unit)
                source = self.plan("vegan baking block")
                self.assertEqual(source["status"], "ready")
                self.gemini.respond_structured.return_value = clear_verdict("vegan baking block")

                verified = verify_vegan_plan(self.gemini, self.recipe, source)

                self.assertTrue(vegan_plan_usable(self.recipe, verified))
                sent_ingredient = self.gemini.respond_structured.call_args.args[2]["recipe"]["ingredients"][0]
                self.assertEqual(sent_ingredient["quantity"], quantity)
                self.assertEqual(sent_ingredient["unit"], unit)
                adapted = adapt_recipe(self.recipe, vegan=True, vegan_plan=verified)
                replacement = adapted["recipe"]["ingredients"][0]
                self.assertEqual(replacement["name"]["value"], "vegan baking block")
                self.assertEqual(replacement["quantity"], quantity)
                self.assertEqual(replacement["unit"], unit)
                self.assertEqual({item["path"] for item in adapted["review_items"]}, {expected_path})

    def test_blocks_incompatible_cooking_function_even_if_measure_is_missing(self):
        self.recipe["ingredients"][0].update(quantity=field(None), unit=field(None))
        source = self.plan("neutral oil")
        self.gemini.respond_structured.return_value = finding_verdict(self.issue())

        verified = verify_vegan_plan(self.gemini, self.recipe, source)

        self.assertEqual(verified["reason_code"], "verification_failed")
        self.assertEqual(verified["changes"], [])
        self.assertFalse(vegan_plan_usable(self.recipe, verified))

    def test_does_not_verify_when_planner_abstains(self):
        for status, reason_code in (("needs_review", "insufficient_information"), ("unavailable", "no_viable_substitute")):
            with self.subTest(status=status):
                plan = validate_vegan_plan(self.recipe, {
                    **proposal(), "status": status, "reason_code": reason_code, "reason": "No known suitable fat.",
                    "review_items": [{"path": "/ingredients/0/name", "reason": "No known suitable fat."}],
                })
                self.assertTrue(vegan_plan_matches(self.recipe, plan))
                self.assertIs(verify_vegan_plan(self.gemini, self.recipe, plan), plan)
                self.gemini.respond_structured.assert_not_called()
                self.assertFalse(vegan_plan_usable(self.recipe, plan))

    def test_unknown_ingredient_and_instruction_offer_exact_review_targets(self):
        source = deepcopy(self.recipe)
        source["ingredients"][0]["name"] = field(None, "needs_review")
        source["steps"][0]["instruction"] = field(None, "needs_review")
        blocked = vegan_source_review(source)
        self.assertEqual(blocked["status"], "needs_review")
        self.assertEqual(blocked["reason_code"], "insufficient_information")
        self.assertEqual({item["path"] for item in blocked["review_items"]}, {
            "/ingredients/0/name", "/steps/0/instruction",
        })
        self.assertEqual(blocked["changes"], [])

        plan = validate_vegan_plan(source, {
            **proposal(), "status": "needs_review", "reason_code": "insufficient_information", "reason": "Verify the source.",
            "review_items": [
                {"path": "/ingredients/0", "reason": "Verify the ingredient."},
                {"path": "/steps/0", "reason": "Verify the instruction."},
            ],
        })
        self.assertEqual({item["path"] for item in plan["review_items"]}, {
            "/ingredients/0/name", "/steps/0/instruction",
        })

    def test_verified_plan_survives_title_and_serving_edits_but_not_method_edits(self):
        self.recipe["title"] = field(None, "needs_review")
        self.recipe["servings"] = field(None, "needs_review")
        self.assertIsNone(vegan_source_review(self.recipe))
        self.gemini.respond_structured.return_value = clear_verdict("vegan baking block")
        verified = verify_vegan_plan(self.gemini, self.recipe, self.plan("vegan baking block"))
        self.assertTrue(vegan_plan_usable(self.recipe, verified))

        edited = deepcopy(self.recipe)
        edited["title"] = field("Cookies")
        edited["servings"] = field("6")
        rebound = rebind_vegan_plan(self.recipe, edited, verified)
        self.assertTrue(vegan_plan_usable(edited, rebound))
        self.assertTrue(vegan_plan_usable(self.recipe, verified))
        self.assertEqual(rebound["changes"], verified["changes"])

        edited["steps"][0]["instruction"] = field("Melt the butter.")
        self.assertIsNone(rebind_vegan_plan(self.recipe, edited, verified))

    def test_vegan_recipe_may_change_meat_only_marination_timing(self):
        original_instruction = "Den Rinderbraten nach dem 1. Tag oder 2. Tag aus der Marinade nehmen."
        source = recipe("4", [ingredient("meat", "Rinderbraten")], [
            step(original_instruction, "meat", condition="nach dem 1. Tag oder 2. Tag"),
        ])
        plan = validate_vegan_plan(source, proposal([
            {"path": "/ingredients/0/name", "before": "Rinderbraten", "after": "Seitanbraten", "reason": "Plant roast."},
            {"path": "/steps/0/instruction", "before": original_instruction,
             "after": "Den Seitanbraten mit der Marinade bestreichen.", "reason": "Flavor the plant roast."},
            {"path": "/steps/0/condition", "before": "nach dem 1. Tag oder 2. Tag", "after": None,
             "reason": "The meat-only marination period does not apply."},
        ]))
        self.assertEqual(plan["status"], "ready")
        plan = validate_vegan_plan(source, proposal([
            {"path": "/steps/0/instruction", "before": original_instruction,
             "after": original_instruction.replace("1. Tag", "1 Tag"), "reason": "Change the time."},
        ]))
        self.assertEqual(plan["status"], "needs_review")

    def test_rejects_an_abstention_containing_a_partial_recipe(self):
        plan = self.plan("vegan baking block")
        with self.assertRaisesRegex(ValueError, "without edits"):
            validate_vegan_plan(self.recipe, {
                **plan, "status": "unavailable", "reason_code": "no_viable_substitute", "reason": "No suitable fat.",
                "review_items": [{"path": "/ingredients/0/name", "reason": "No suitable fat."}],
            })

    def test_drops_unsupported_findings(self):
        source = self.plan("neutral oil")
        for finding in (
            {**self.issue(), "path": "/ingredients/1/name"},
            {**self.issue(), "kind": "preference"},
            {**self.issue(), "evidence": [{"path": "/steps/0/instruction", "quote": "Bake at 200 °C."}]},
            {**self.issue(), "evidence": [{"path": "/ingredients/0/name", "quote": "neutral oil"}]},
        ):
            with self.subTest(finding=finding):
                self.gemini.respond_structured.return_value = finding_verdict(finding)
                verified = verify_vegan_plan(self.gemini, self.recipe, source)
                self.assertEqual(verified["verification"]["status"], "unavailable")
                self.assertEqual(verified["status"], "unavailable")
                self.assertEqual(verified["changes"], [])

    def test_provider_failure_withholds_an_unverified_plan(self):
        source = self.plan("vegan baking block")
        self.gemini.respond_structured.side_effect = ProviderError("offline")
        verified = verify_vegan_plan(self.gemini, self.recipe, source)
        self.assertEqual(verified["verification"]["status"], "unavailable")
        self.assertEqual(verified["status"], "unavailable")
        self.assertEqual(verified["reason_code"], "verification_unavailable")
        self.assertEqual(verified["changes"], [])
        self.assertFalse(vegan_plan_usable(self.recipe, source))
        self.assertFalse(vegan_plan_usable(self.recipe, verified))

    def test_requires_verification_for_the_exact_replacement(self):
        source = self.plan("vegan baking block")
        for response in ({"findings": []}, clear_verdict(), clear_verdict("vegan spread"), clear_verdict("vegan baking block", "extra")):
            with self.subTest(response=response):
                self.gemini.respond_structured.return_value = response
                verified = verify_vegan_plan(self.gemini, self.recipe, source)
                self.assertEqual(verified["status"], "unavailable")
                self.assertEqual(verified["changes"], [])

    def test_explicit_verifier_uncertainty_withholds_an_unverified_ingredient(self):
        source = self.plan("Floravel crystal cream")
        self.gemini.respond_structured.return_value = {
            **clear_verdict(), "status": "unavailable", "reason_code": "insufficient_information",
            "reason": "The proposed crystal cream could not be identified.",
        }

        verified = verify_vegan_plan(self.gemini, self.recipe, source)

        self.assertEqual(verified["reason_code"], "insufficient_information")
        self.assertEqual(verified["changes"], [])
        self.assertFalse(vegan_plan_usable(self.recipe, verified))

    def test_unsupported_replacement_requires_the_final_ingredient_as_evidence(self):
        replacement = "Floravel crystal cream"
        source = self.plan(replacement)
        finding = {
            **self.issue(), "kind": "unsupported_substitute",
            "reason": "The proposed cream is not an established food or product class.",
        }
        for quote, verdict in (("neutral oil", "unavailable"), (replacement, "needs_review")):
            with self.subTest(quote=quote):
                finding["evidence"][-1]["quote"] = quote
                self.gemini.respond_structured.return_value = finding_verdict(finding)
                verified = verify_vegan_plan(self.gemini, self.recipe, source)
                self.assertEqual(verified["verification"]["status"], verdict)
                self.assertEqual(verified["changes"], [])

    def test_an_already_vegan_recipe_requires_an_explicit_clear_verdict(self):
        source = recipe("4", [ingredient("salt", "salt")], [step("Add salt.", "salt")])
        plan = validate_vegan_plan(source, proposal())
        self.assertFalse(vegan_plan_usable(source, plan))
        self.gemini.respond_structured.return_value = clear_verdict()
        self.assertTrue(vegan_plan_usable(source, verify_vegan_plan(self.gemini, source, plan)))

    def test_blocks_clear_verdict_when_final_ingredient_list_still_contains_butter(self):
        plan = validate_vegan_plan(self.recipe, proposal())
        self.gemini.respond_structured.return_value = clear_verdict()
        verified = verify_vegan_plan(self.gemini, self.recipe, plan)
        self.assertEqual(verified["status"], "unavailable")
        self.assertEqual(verified["reason_code"], "verification_failed")
        self.assertEqual([item["path"] for item in verified["review_items"]], ["/ingredients/0/name"])
        sent = self.gemini.respond_structured.call_args.args[2]
        self.assertEqual(sent["ingredients_to_check"], [{"path": "/ingredients/0/name", "ingredient": "butter"}])
        self.assertFalse(vegan_plan_usable(self.recipe, {**plan, "verification": {"provider": "gemini", **clear_verdict()}}))

    def test_omission_requires_an_ingredient_still_present_after_the_changes(self):
        finding = {
            "path": "/ingredients/0/name", "kind": "animal_ingredient",
            "reason": "The recipe still uses dairy butter.",
            "evidence": [{"path": "/ingredients/0/name", "quote": "butter"}],
        }
        self.gemini.respond_structured.return_value = finding_verdict(finding)
        unchanged = validate_vegan_plan(self.recipe, proposal())

        valid = verify_vegan_plan(self.gemini, self.recipe, unchanged)
        invalid = verify_vegan_plan(self.gemini, self.recipe, self.plan("vegan butter"))

        self.assertEqual(valid["verification"]["findings"], [finding])
        self.assertEqual(valid["status"], "unavailable")
        self.assertEqual(invalid["verification"]["findings"], [])
