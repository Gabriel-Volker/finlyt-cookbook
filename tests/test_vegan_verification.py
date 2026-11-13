import unittest
from copy import deepcopy
from unittest.mock import Mock

from cookbook_ocr.adaptation import adapt_recipe
from cookbook_ocr.providers import ProviderError
from cookbook_ocr.vegan import validate_vegan_plan
from cookbook_ocr.vegan_verification import verify_vegan_plan
from test_adaptation import ingredient, recipe, step


class VeganVerificationTests(unittest.TestCase):
    def setUp(self):
        self.recipe = recipe("4", [ingredient("butter", "butter", "100", "g")], [
            step("Cream the solid butter with sugar until fluffy.", "butter"),
        ])
        self.gemini = Mock()

    def plan(self, replacement, warnings=()):
        return validate_vegan_plan(self.recipe, {"changes": [
            {"path": "/ingredients/0/name", "before": "butter", "after": replacement,
             "reason": "Use a plant-based fat."},
            {"path": "/steps/0/instruction", "before": "Cream the solid butter with sugar until fluffy.",
             "after": f"Cream the {replacement} with sugar until fluffy.", "reason": "Use the replacement fat."},
        ], "review_items": list(warnings)})

    def issue(self):
        return {
            "path": "/ingredients/0/name", "kind": "cooking_function",
            "reason": "Neutral oil cannot be creamed with sugar as a solid fat to aerate the batter.",
            "evidence": [
                {"path": "/steps/0/instruction", "quote": "Cream the solid butter with sugar until fluffy."},
                {"path": "/ingredients/0/name", "quote": "neutral oil"},
            ],
        }

    def test_preserves_a_workable_substitute_without_review(self):
        source = self.plan("vegan baking block")
        self.gemini.respond_structured.return_value = {"findings": []}

        verified = verify_vegan_plan(self.gemini, self.recipe, source)

        self.assertEqual(verified["verification"], {"provider": "gemini", "status": "clear", "findings": []})
        self.assertEqual(verified["changes"], source["changes"])
        self.assertEqual(verified["review_items"], [])
        prompt, _, sent = self.gemini.respond_structured.call_args.args
        self.assertIn("Accept different workable ingredients and techniques", prompt)
        self.assertEqual(sent["recipe"], self.recipe)
        self.assertEqual(sent["changes"], source["changes"])

    def test_forwards_a_concrete_issue_to_adaptation_without_mutating_the_plan(self):
        source = self.plan("neutral oil")
        original = deepcopy(source)
        finding = self.issue()
        self.gemini.respond_structured.return_value = {"findings": [finding, finding]}

        verified = verify_vegan_plan(self.gemini, self.recipe, source)

        self.assertEqual(source, original)
        self.assertEqual(verified["verification"]["findings"], [finding])
        self.assertEqual(verified["verification"]["status"], "needs_review")
        self.assertEqual(verified["status"], "needs_review")
        adapted = adapt_recipe(self.recipe, vegan=True, vegan_plan=verified)
        self.assertEqual(adapted["recipe"]["ingredients"][0]["name"]["value"], "neutral oil")
        self.assertEqual(adapted["status"], "needs_review")
        self.assertEqual(adapted["review_items"], [{"path": finding["path"], "reason": finding["reason"]}])

    def test_does_not_repeat_a_warning_already_in_the_plan(self):
        finding = self.issue()
        warning = {key: finding[key] for key in ("path", "reason")}
        source = self.plan("neutral oil", [warning])
        self.gemini.respond_structured.return_value = {"findings": [finding]}
        verified = verify_vegan_plan(self.gemini, self.recipe, source)
        self.assertEqual(verified["review_items"], [warning])

    def test_drops_unsupported_findings(self):
        source = self.plan("neutral oil")
        for finding in (
            {**self.issue(), "path": "/ingredients/1/name"},
            {**self.issue(), "kind": "preference"},
            {**self.issue(), "evidence": [{"path": "/steps/0/instruction", "quote": "Bake at 200 °C."}]},
            {**self.issue(), "evidence": [{"path": "/ingredients/0/name", "quote": "neutral oil"}]},
        ):
            with self.subTest(finding=finding):
                self.gemini.respond_structured.return_value = {"findings": [finding]}
                verified = verify_vegan_plan(self.gemini, self.recipe, source)
                self.assertEqual(verified["verification"]["status"], "unavailable")
                self.assertEqual(verified["status"], "ready")
                self.assertEqual(verified["review_items"], [])

    def test_provider_failure_leaves_the_valid_plan_available(self):
        source = self.plan("vegan baking block")
        self.gemini.respond_structured.side_effect = ProviderError("offline")
        verified = verify_vegan_plan(self.gemini, self.recipe, source)
        self.assertEqual(verified["verification"]["status"], "unavailable")
        self.assertEqual(verified["status"], "ready")
        self.assertEqual(verified["changes"], source["changes"])

    def test_omission_requires_an_ingredient_still_present_after_the_changes(self):
        finding = {
            "path": "/ingredients/0/name", "kind": "animal_ingredient",
            "reason": "The recipe still uses dairy butter.",
            "evidence": [{"path": "/ingredients/0/name", "quote": "butter"}],
        }
        self.gemini.respond_structured.return_value = {"findings": [finding]}
        unchanged = validate_vegan_plan(self.recipe, {"changes": [], "review_items": []})

        valid = verify_vegan_plan(self.gemini, self.recipe, unchanged)
        invalid = verify_vegan_plan(self.gemini, self.recipe, self.plan("vegan butter"))

        self.assertEqual(valid["verification"]["findings"], [finding])
        self.assertEqual(invalid["review_items"], [])
