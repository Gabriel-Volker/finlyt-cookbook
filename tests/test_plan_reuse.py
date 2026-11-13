import unittest
from unittest.mock import Mock, patch

from cookbook_ocr.pipeline import refresh_scaling_plans
from cookbook_ocr.review import review_recipe
from cookbook_ocr.scaling_plan import scaling_plan_matches, validate_scaling_plan
from cookbook_ocr.vegan import unavailable_vegan_plan, vegan_plan_usable
from cookbook_ocr.web import CookbookHandler

from test_adaptation import field, ingredient, recipe, scaling_rule, step, vegan_plan


class PlanReuseTests(unittest.TestCase):
    def setUp(self):
        self.recipe = recipe(None, [ingredient("oil", "olive oil", "10", "ml")],
                             [step("Add 10 ml olive oil.", "oil")])
        self.plan = validate_scaling_plan(self.recipe, {"rules": [scaling_rule("oil")]})
        self.verification = {"provider": "gemini", "status": "completed", "rules": self.plan["rules"]}
        self.luna = Mock()
        self.gemini = Mock()

    def test_updating_metadata_and_confirming_text_reuse_verified_plans(self):
        vegan = vegan_plan(self.recipe, [])
        report = {"recipe": self.recipe, "vegan_plan": vegan,
                  "scaling_plan": {"original": {**self.plan, "verification": self.verification}}}
        review = review_recipe(self.recipe, [
            {"path": "/title", "action": "correct", "value": "Renamed recipe"},
            {"path": "/servings", "action": "correct", "value": "4"},
            {"path": "/steps/0/instruction", "action": "confirm"},
        ])["recipe"]
        handler = object.__new__(CookbookHandler)
        with patch("cookbook_ocr.web.load_openai_key", return_value="test"), patch(
            "cookbook_ocr.web.OpenAIClient", return_value=self.luna,
        ), patch("cookbook_ocr.web.optional_gemini_client", return_value=self.gemini):
            refreshed = handler._plans(report, review)
        self.assertTrue(vegan_plan_usable(review, refreshed["vegan_plan"]))
        self.assertEqual(refreshed["vegan_plan"]["verification"], vegan["verification"])
        self.assertEqual(refreshed["scaling_plan"]["original"]["verification"], self.verification)
        self.assertTrue(scaling_plan_matches(review, refreshed["scaling_plan"]["original"]))
        self.luna.respond.assert_not_called()
        self.gemini.respond_structured.assert_not_called()

    def test_refresh_does_not_repeat_unavailable_verification_until_retry(self):
        unavailable = {"provider": "gemini", "status": "unavailable", "reason_code": "provider_unavailable"}
        stored = {"original": {**self.plan, "verification": unavailable}}
        self.gemini.respond_structured.return_value = {"rules": self.plan["rules"]}
        result = refresh_scaling_plans(self.luna, self.recipe, None, stored, gemini=self.gemini,
                                       retry_verification=False)
        self.assertEqual(result, stored)
        self.gemini.respond_structured.assert_not_called()
        recovered = refresh_scaling_plans(self.luna, self.recipe, None, stored, gemini=self.gemini,
                                          retry_verification=True, retry_recovered=True)
        self.assertEqual(recovered["original"]["verification"], self.verification)
        self.gemini.respond_structured.assert_called_once()
        self.luna.respond.assert_not_called()

    def test_retry_recovered_luna_rules_reuses_verified_gemini_rules(self):
        stored = {"original": {**self.plan, "status": "needs_review", "recovered_ingredients": ["oil"],
                               "verification": self.verification}}
        with patch("cookbook_ocr.pipeline.plan_recipe_scaling", return_value=self.plan) as generate:
            result = refresh_scaling_plans(self.luna, self.recipe, None, stored, gemini=self.gemini,
                                           retry_verification=True, retry_recovered=True)
        generate.assert_called_once_with(self.luna, self.recipe)
        self.assertEqual(result["original"], {**self.plan, "verification": self.verification})
        self.gemini.respond_structured.assert_not_called()

    def test_title_only_vegan_change_shares_scaling_classifications(self):
        self.recipe["title"] = field("Recipe")
        plan = vegan_plan(self.recipe, [("/title", "Recipe", "Vegan recipe")])
        self.gemini.respond_structured.return_value = {"rules": self.plan["rules"]}
        with patch("cookbook_ocr.pipeline.plan_recipe_scaling", return_value=self.plan) as generate:
            result = refresh_scaling_plans(self.luna, self.recipe, plan, gemini=self.gemini)
        generate.assert_called_once_with(self.luna, self.recipe)
        self.gemini.respond_structured.assert_called_once()
        self.assertEqual(result["original"], result["vegan"])

    def test_retry_keeps_vegan_review_blocked_until_source_changes(self):
        blocked = unavailable_vegan_plan(self.recipe, "insufficient_information", "Verify the source.")
        handler = object.__new__(CookbookHandler)
        with patch("cookbook_ocr.web.plan_vegan_recipe") as generate, patch("cookbook_ocr.web.verify_vegan_plan") as verify:
            result = handler._refresh_plan({"vegan_plan": blocked}, self.recipe, previous=self.recipe, retry=True)
        self.assertEqual(result, blocked)
        generate.assert_not_called()
        verify.assert_not_called()
