import json
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from cookbook_ocr.pipeline import run_pipeline
from cookbook_ocr.providers import ProviderError
from test_extraction import TRANSCRIPTIONS, porridge, span


class ExtractionRepairTests(unittest.TestCase):
    def extract(self, source, first_candidate, second_response=None):
        openai = Mock()
        gemini = Mock()
        openai.transcribe.return_value = source
        gemini.transcribe.return_value = source
        openai.respond.side_effect = [
            candidate if isinstance(candidate, Exception) else json.dumps(candidate)
            for candidate in (first_candidate, second_response)
            if candidate is not None
        ]
        with patch("cookbook_ocr.pipeline.plan_vegan_recipe", return_value={"status": "unavailable"}), patch(
            "cookbook_ocr.pipeline.refresh_scaling_plans", return_value={},
        ):
            report = run_pipeline(b"image", "recipe.png", gemini, openai)
        gemini.transcribe.assert_called_once()
        openai.transcribe.assert_called_once()
        return report, [call.kwargs for call in openai.respond.call_args_list]

    def test_retry_replaces_only_the_invalid_field_and_revalidates_it(self):
        candidate = porridge()
        candidate["ingredients"][0]["quantity"] = span("9", "9 cups water")
        path = "/ingredients/0/quantity"
        repair = {"repairs": [
            {"path": path, "span": span("2", "2 cups water")},
            {"path": "/title", "span": span("Serve", "Serve when thick.")},
        ]}
        source = TRANSCRIPTIONS["A"]

        report, calls = self.extract(source, candidate, repair)

        self.assertEqual([call["text"]["format"]["name"] for call in calls],
                         ["recipe_extraction", "recipe_extraction_repair"])
        request = json.loads(calls[1]["input"][1]["content"])
        self.assertEqual(request["readings"], {"A": source, "B": source})
        self.assertEqual([field["path"] for field in request["fields"]], [path])
        schema = calls[1]["text"]["format"]["schema"]
        self.assertEqual(schema["properties"]["repairs"]["items"]["properties"]["path"]["enum"], [path])
        recipe = report["recipe"]
        self.assertEqual(recipe["title"]["value"], "Oat porridge")
        self.assertEqual(recipe["steps"][0]["ingredient_ids"], ["water", "oats"])
        self.assertEqual(recipe["ingredients"][0]["quantity"]["value"], "2")
        self.assertEqual(recipe["ingredients"][0]["quantity"]["evidence"],
                         {"source": "A", "page": "1", "quote": "2 cups water"})
        self.assertEqual(report["extraction_issues"], [])

    def test_compact_evidence_still_requires_context_for_repeated_fraction(self):
        source = TRANSCRIPTIONS["A"].replace("2 cups water", "½ cup water").replace(
            "rolled oats\n", "1½ cups rolled oats\n", 1,
        )
        candidate = porridge()
        candidate["ingredients"][0].update(
            name=span("water", "½ cup water"),
            quantity={"source": "A", "text": "½", "quote": None},
            unit=span("cup", "½ cup water"),
        )
        candidate["ingredients"][1]["quantity"] = span("1½", "1½ cups rolled oats")
        candidate["steps"][0]["instruction"]["quote"] = None
        path = "/ingredients/0/quantity"

        report, calls = self.extract(source, candidate, {
            "repairs": [{"path": path, "span": span("½", "½ cup water")}],
        })

        request = json.loads(calls[1]["input"][1]["content"])
        self.assertEqual([field["path"] for field in request["fields"]], [path])
        self.assertEqual(report["recipe"]["ingredients"][0]["quantity"]["value"], "½")
        self.assertEqual(report["recipe"]["ingredients"][1]["quantity"]["value"], "1½")
        step = report["recipe"]["steps"][0]["instruction"]
        self.assertEqual(step["evidence"]["quote"], step["value"])
        self.assertEqual(step["evidence"]["page"], "2")
        self.assertEqual(report["extraction_issues"], [])

    def test_repair_provider_failure_keeps_valid_extraction_and_review_flag(self):
        candidate = porridge()
        candidate["ingredients"][0]["quantity"] = span("9", "9 cups water")

        with self.assertLogs("cookbook_ocr.pipeline", level="WARNING"):
            report, calls = self.extract(TRANSCRIPTIONS["A"], candidate, ProviderError("Temporary failure"))

        self.assertEqual(len(calls), 2)
        self.assertEqual(report["recipe"]["title"]["value"], "Oat porridge")
        self.assertEqual(report["recipe"]["steps"][0]["duration"]["value"], "10 minutes")
        self.assertIsNone(report["recipe"]["ingredients"][0]["quantity"]["value"])
        self.assertEqual(report["recipe"]["ingredients"][0]["quantity"]["status"], "needs_review")
        self.assertEqual(report["extraction_issues"][0]["field"], "/ingredients/0/quantity")

    def test_accepts_joined_word_without_changing_the_source_quote(self):
        source = TRANSCRIPTIONS["A"].replace("rolled oats", "rolled oa-\n  ts")
        candidate = porridge()
        candidate["ingredients"][1]["name"] = span("rolled oats", "rolled oats")

        report, calls = self.extract(source, candidate)

        self.assertEqual(len(calls), 1)
        field = report["recipe"]["ingredients"][1]["name"]
        self.assertEqual(field["value"], "rolled oa-\n  ts")
        self.assertEqual(field["evidence"]["quote"], "rolled oa-\n  ts")
        self.assertEqual(report["extraction_issues"], [])

    def test_retry_cannot_move_a_quantity_between_ingredients_or_hide_the_issue(self):
        candidate = porridge()
        candidate["ingredients"][0]["quantity"] = span("9", "9 cups water")
        source = TRANSCRIPTIONS["A"].replace("rolled oats\n", "3 cups rolled oats\n", 1)
        path = "/ingredients/0/quantity"
        for corrected in (span("3", "3 cups rolled oats"), None):
            with self.subTest(corrected=corrected):
                report, calls = self.extract(source, candidate, {
                    "repairs": [{"path": path, "span": corrected}],
                })

                self.assertEqual(len(calls), 2)
                water = report["recipe"]["ingredients"][0]
                self.assertIsNone(water["quantity"]["value"])
                self.assertEqual(water["quantity"]["status"], "needs_review")
                self.assertEqual(report["extraction_issues"][0]["field"], path)
                self.assertEqual(report["recipe"]["title"]["value"], "Oat porridge")

    def test_repair_targets_raw_record_when_an_earlier_duplicate_is_collapsed(self):
        candidate = porridge()
        candidate["ingredients"][0]["name"] = span("water", "2 cups water")
        duplicate = deepcopy(candidate["ingredients"][0])
        duplicate["id"] = "water-duplicate"
        candidate["ingredients"].insert(1, duplicate)
        candidate["ingredients"][2]["quantity"] = span("9", "9 cups rolled oats")
        candidate["ingredients"][2]["name"] = span("rolled oats", "3 cups rolled oats")
        source = TRANSCRIPTIONS["A"].replace("rolled oats\n", "3 cups rolled oats\n", 1)
        path = "/ingredients/2/quantity"

        report, calls = self.extract(source, candidate, {
            "repairs": [{"path": path, "span": span("3", "3 cups rolled oats")}],
        })

        request = json.loads(calls[1]["input"][1]["content"])
        self.assertEqual([field["path"] for field in request["fields"]], [path])
        self.assertEqual([(item["id"], item["quantity"]["value"]) for item in report["recipe"]["ingredients"]],
                         [("water", "2"), ("oats", "3")])
        self.assertEqual(report["extraction_issues"], [])

    def test_full_retry_is_kept_when_a_measured_ingredient_is_omitted(self):
        complete = porridge()
        source = TRANSCRIPTIONS["A"].replace("Oat porridge\n", "Oat porridge\nIngredients\n", 1)
        partial = deepcopy(complete)
        partial["ingredients"].pop(0)
        partial["steps"][0]["ingredient_ids"] = ["oats"]

        report, calls = self.extract(source, partial, complete)

        self.assertEqual([call["text"]["format"]["name"] for call in calls],
                         ["recipe_extraction", "recipe_extraction"])
        self.assertEqual([item["id"] for item in report["recipe"]["ingredients"]], ["water", "oats"])
        self.assertEqual(report["extraction_issues"], [])

    def test_explicitly_unreadable_amount_does_not_trigger_a_repair_call(self):
        source = TRANSCRIPTIONS["A"].replace("2 cups water", "[unreadable] cups water", 1)
        candidate = porridge()
        candidate["ingredients"][0]["quantity"] = span("[unreadable]", "[unreadable] cups water")
        candidate["ingredients"][0]["unit"] = span("cups", "[unreadable] cups water")

        report, calls = self.extract(source, candidate)

        self.assertEqual(len(calls), 1)
        quantity = report["recipe"]["ingredients"][0]["quantity"]
        self.assertEqual(quantity["status"], "needs_review")
        self.assertIsNone(quantity["value"])
        self.assertTrue(quantity["review_item_ids"])
        self.assertEqual(report["extraction_issues"], [])
