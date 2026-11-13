import json
import unittest
from pathlib import Path
from unittest.mock import Mock

import pymupdf

from cookbook_ocr.pipeline import (
    compare_transcriptions,
    plan_vegan_recipe,
    run_pipeline,
    validate_comparison,
)


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
TRANSCRIPTION_A = "Page 1\nPear compote by Mara\nUse ¾ cup of water."
TRANSCRIPTION_B = "Page 1\nPear compote by Ma[unreadable]\nUse 3/4 cup of water."
with pymupdf.open() as pdf:
    pdf.new_page()
    DOCUMENT = pdf.tobytes()
EMPTY_RECIPE = {"title": None, "servings": None, "ingredients": [], "tools": [], "steps": []}


def comparison_with_uncertainty():
    return {
        "review_items": [
            {
                "page": "1",
                "section": "Attribution",
                "kind": "uncertain",
                "a_excerpt": "Pear compote by Mara",
                "b_excerpt": "Pear compote by Ma[unreadable]",
                "reason": "One reading marks part of the name as unreadable.",
            }
        ],
        "equivalent_variations": [
            {
                "page": "1",
                "kind": "quantity_notation",
                "a_excerpt": "¾ cup of water",
                "b_excerpt": "3/4 cup of water",
                "reason": "The fractions are equivalent.",
            }
        ],
    }


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.gemini = Mock()
        self.gemini.transcribe.return_value = TRANSCRIPTION_A
        self.openai = Mock()
        self.openai.transcribe.return_value = TRANSCRIPTION_B

    def test_compare_sends_only_anonymous_transcriptions(self):
        prompt = (PROMPTS_DIR / "compare_transcriptions.system.txt").read_text(
            encoding="utf-8"
        )
        schema = json.loads(
            (PROMPTS_DIR / "compare_transcriptions.schema.json").read_text(
                encoding="utf-8"
            )
        )
        comparison = comparison_with_uncertainty()
        self.openai.respond.return_value = json.dumps(comparison)

        result = compare_transcriptions(
            self.openai, prompt, schema, TRANSCRIPTION_A, TRANSCRIPTION_B
        )

        self.openai.respond.assert_called_once()
        payload = self.openai.respond.call_args.kwargs
        system_message, user_message = payload["input"]
        self.assertEqual(system_message, {"role": "system", "content": prompt})
        self.assertEqual(user_message["role"], "user")
        self.assertEqual(
            json.loads(user_message["content"]),
            {"A": TRANSCRIPTION_A, "B": TRANSCRIPTION_B},
        )
        self.assertIs(payload["text"]["format"]["strict"], True)
        self.assertEqual(payload["text"]["format"]["schema"], schema)
        self.assertEqual(result, comparison)

    def test_review_items_set_pipeline_status(self):
        comparison = comparison_with_uncertainty()
        self.openai.respond.side_effect = [json.dumps(comparison), json.dumps(EMPTY_RECIPE)]

        report = run_pipeline(DOCUMENT, "recipe.pdf", self.gemini, self.openai)

        self.gemini.transcribe.assert_called_once()
        self.openai.transcribe.assert_called_once()
        gemini_args = self.gemini.transcribe.call_args.args
        luna_args = self.openai.transcribe.call_args.args
        self.assertEqual(gemini_args, luna_args)
        self.assertEqual(gemini_args[:2], (DOCUMENT, "recipe.pdf"))
        self.assertEqual(len(self.openai.transcribe.call_args.kwargs["prepared_pdf"].pages), 1)
        self.assertEqual(
            report["transcriptions"], {"A": TRANSCRIPTION_A, "B": TRANSCRIPTION_B}
        )
        self.assertEqual(report["comparison"], comparison)
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["recipe"]["servings"]["status"], "missing")
        extraction_call = self.openai.respond.call_args.kwargs
        self.assertEqual(extraction_call["text"]["format"]["name"], "recipe_extraction")
        self.assertEqual(
            json.loads(extraction_call["input"][1]["content"]),
            {"A": TRANSCRIPTION_A, "B": TRANSCRIPTION_B},
        )

    def test_retries_comparison_with_invalid_evidence(self):
        invalid = comparison_with_uncertainty()
        invalid["review_items"][0]["a_excerpt"] = "Missing from source"
        valid = comparison_with_uncertainty()
        self.openai.respond.side_effect = [
            json.dumps(invalid), json.dumps(valid), json.dumps(EMPTY_RECIPE)
        ]

        report = run_pipeline(DOCUMENT, "recipe.pdf", self.gemini, self.openai)
        self.assertEqual(report["comparison"], valid)
        self.gemini.transcribe.assert_called_once()
        self.openai.transcribe.assert_called_once()
        self.assertEqual(self.openai.respond.call_count, 3)

    def test_equivalent_fraction_does_not_require_review(self):
        self.gemini.transcribe.return_value = "Use ¾ cup of water."
        self.openai.transcribe.return_value = "Use 3/4 cup of water."
        comparison = comparison_with_uncertainty()
        comparison["review_items"] = []
        comparison["equivalent_variations"][0]["page"] = None
        self.openai.respond.side_effect = [json.dumps(comparison), json.dumps(EMPTY_RECIPE)]

        report = run_pipeline(DOCUMENT, "recipe.pdf", self.gemini, self.openai)

        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["extraction_issues"][0]["field"], "/recipe")
        self.assertEqual(report["comparison"], comparison)

    def test_checks_numeric_equivalences_and_complete_quotes(self):
        cases = [
            ("Add ½ cup flour.", "Add 1½ cup flour.", "½ cup flour", "1½ cup flour", "quantity_notation", False),
            ("Add ½ cup flour.", "Add 1½ cup flour.", "½ cup flour", "½ cup flour", "quantity_notation", False),
            ("Add ½ cup flour.", "Add 1 1/2 cup flour.", "½ cup flour", "1 1/2 cup flour", "formatting", False),
            ("Add 1½ cup flour.", "Add 1 1/2 cup flour.", "1½ cup flour", "1 1/2 cup flour", "quantity_notation", True),
            ("Add ½ l water.", "Add 500 ml water.", "½ l water", "500 ml water", "unit_conversion", True),
        ]
        for a, b, excerpt_a, excerpt_b, kind, equivalent in cases:
            with self.subTest(a=a, b=b, kind=kind):
                self.openai.respond.return_value = json.dumps({
                    "review_items": [], "equivalent_variations": [{
                        "page": "1", "kind": kind, "a_excerpt": excerpt_a,
                        "b_excerpt": excerpt_b, "reason": "Same amount.",
                    }],
                })
                result = compare_transcriptions(self.openai, "", {}, f"[page: 1]\n{a}", f"[page: 1]\n{b}")
                self.assertEqual(bool(result["equivalent_variations"]), equivalent)
                self.assertEqual(bool(result["review_items"]), not equivalent)

    def test_detects_a_numeric_difference_the_model_omits(self):
        self.openai.respond.return_value = json.dumps({"review_items": [], "equivalent_variations": []})
        a = "[page: 1]\n½ cup flour\n½ l water"
        b = "[page: 1]\n1½ cup flour\n500 ml water"
        report = compare_transcriptions(self.openai, "", {}, a, b)
        self.assertEqual([(item["a_excerpt"], item["b_excerpt"]) for item in report["review_items"]],
                         [("½ cup flour", "1½ cup flour")])

    def test_keeps_unreadability_when_the_comparator_drops_or_misclassifies_it(self):
        a = "[page: 1]\n1[unreadable] cup flour"
        b = "[page: 1]\n1½ cup flour"
        item = {
            "page": "1", "kind": "formatting", "a_excerpt": "1[unreadable] cup flour",
            "b_excerpt": "1½ cup flour", "reason": "Same text.",
        }
        for equivalents in ([], [item]):
            with self.subTest(equivalents=equivalents):
                self.openai.respond.return_value = json.dumps({
                    "review_items": [], "equivalent_variations": equivalents,
                })
                result = compare_transcriptions(self.openai, "", {}, a, b)
                self.assertEqual(result["equivalent_variations"], [])
                self.assertEqual(len(result["review_items"]), 1)
                finding = result["review_items"][0]
                self.assertEqual(finding["kind"], "uncertain")
                self.assertEqual((finding["a_excerpt"], finding["b_excerpt"]), (item["a_excerpt"], item["b_excerpt"]))

    def test_retries_extraction_with_invalid_evidence_without_repeating_ocr(self):
        invalid_recipe = {
            **EMPTY_RECIPE,
            "title": {"source": "A", "quote": "Fabricated", "text": "Fabricated"},
        }
        self.openai.respond.side_effect = [
            json.dumps(comparison_with_uncertainty()),
            json.dumps(invalid_recipe),
            json.dumps(EMPTY_RECIPE),
        ]

        report = run_pipeline(DOCUMENT, "recipe.pdf", self.gemini, self.openai)

        self.gemini.transcribe.assert_called_once()
        self.openai.transcribe.assert_called_once()
        self.assertEqual(self.openai.respond.call_count, 3)
        self.assertEqual(report["recipe"]["title"]["status"], "missing")

    def test_withholds_field_if_evidence_is_still_invalid_after_retry(self):
        invalid_recipe = {
            **EMPTY_RECIPE,
            "title": {"source": "A", "quote": "Fabricated", "text": "Fabricated"},
        }
        self.openai.respond.side_effect = [
            json.dumps({"review_items": [], "equivalent_variations": []}),
            json.dumps(invalid_recipe),
            json.dumps(invalid_recipe),
        ]

        report = run_pipeline(DOCUMENT, "recipe.pdf", self.gemini, self.openai)

        self.assertEqual(report["status"], "needs_review")
        self.assertIsNone(report["recipe"]["title"]["value"])
        self.assertEqual(report["recipe"]["title"]["status"], "needs_review")
        self.assertEqual(report["extraction_issues"][0]["field"], "/title")

    def test_rejects_invalid_comparison_evidence(self):
        cases = [
            ("review_items", "b_excerpt", "Pear compote by Mara"),
            ("review_items", "page", "1 (188)"),
            ("equivalent_variations", "a_excerpt", "3/4 cup of water"),
        ]
        for category, field, value in cases:
            with self.subTest(category=category, field=field):
                comparison = comparison_with_uncertainty()
                comparison[category][0][field] = value
                with self.assertRaises(ValueError):
                    validate_comparison(comparison, TRANSCRIPTION_A, TRANSCRIPTION_B)

    def test_pipeline_asks_luna_for_vegan_plan_after_extraction(self):
        text = "[page: 1]\nFry the bacon."
        self.gemini.transcribe.return_value = text
        self.openai.transcribe.return_value = text
        span = {"source": "A", "quote": "Fry the bacon.", "text": "bacon"}
        candidate = {
            **EMPTY_RECIPE,
            "ingredients": [{"id": "bacon", "name": span, "quantity": None, "unit": None,
                             "preparation": None, "optional_when": None}],
            "steps": [{"instruction": {**span, "text": "Fry the bacon."}, "condition": None,
                       "duration": None, "ingredient_ids": ["bacon"], "tool_ids": []}],
        }
        proposal = {
            "status": "ready", "reason_code": None, "reason": None,
            "changes": [{"path": "/ingredients/0/name", "before": "bacon", "after": "smoked tempeh",
                         "reason": "Provide smoky flavour with a firm texture."},
                        {"path": "/steps/0/instruction", "before": "Fry the bacon.",
                         "after": "Fry the smoked tempeh.", "reason": "Use the replacement in the frying step."}],
            "review_items": [],
        }
        def respond(**call):
            name = call["text"]["format"]["name"]
            if name == "transcription_comparison":
                return json.dumps({"review_items": [], "equivalent_variations": []})
            if name == "recipe_extraction":
                return json.dumps(candidate)
            if name == "vegan_plan":
                return json.dumps(proposal)
            supplied = json.loads(call["input"][1]["content"])["recipe"]
            return json.dumps({"rules": [{
                "ingredient_id": "bacon", "mode": "review", "batch_servings": None,
                "reason": supplied["ingredients"][0]["name"]["value"] + " has no numeric amount.", "evidence": [],
            }]})

        self.openai.respond.side_effect = respond
        def gemini_check(prompt, schema, data):
            if "findings" in schema["properties"]:
                self.assertEqual(data["changes"], proposal["changes"])
                return {"status": "clear", "reason_code": None, "reason": None, "findings": [],
                        "approved_substitutions": [{"path": "/ingredients/0/name", "ingredient": "smoked tempeh"}]}
            self.assertEqual(set(data), {"recipe"})
            return {"rules": [{
                "ingredient_id": "bacon", "mode": "review", "batch_servings": None,
                "reason": "No numeric amount was stated.", "evidence": [],
            }]}

        self.gemini.respond_structured.side_effect = gemini_check
        report = run_pipeline(DOCUMENT, "recipe.pdf", self.gemini, self.openai)
        call = self.openai.respond.call_args_list[2].kwargs
        self.assertEqual(call["text"]["format"]["name"], "vegan_plan")
        supplied = json.loads(call["input"][1]["content"])["recipe"]
        self.assertEqual(supplied["ingredients"][0]["name"], {"value": "bacon", "status": "stated"})
        self.assertEqual(report["recipe"]["ingredients"][0]["name"]["value"], "bacon")
        self.assertEqual(report["vegan_plan"]["changes"][0]["after"], "smoked tempeh")
        self.assertEqual(report["vegan_plan"]["verification"]["status"], "clear")
        self.assertEqual(report["scaling_plan"]["original"]["rules"][0]["reason"], "bacon has no numeric amount.")
        self.assertEqual(report["scaling_plan"]["vegan"]["rules"][0]["reason"], "smoked tempeh has no numeric amount.")
        for variant in ("original", "vegan"):
            verification = report["scaling_plan"][variant]["verification"]
            self.assertEqual(verification["status"], "completed")
            self.assertEqual(verification["rules"][0]["reason"], "No numeric amount was stated.")
        self.assertEqual(self.gemini.respond_structured.call_count, 3)

        def unavailable_verification(prompt, schema, data):
            if "findings" in schema["properties"]:
                return {"status": "unavailable", "reason_code": "insufficient_information",
                        "reason": "Could not verify the replacement.", "findings": [], "approved_substitutions": []}
            return gemini_check(prompt, schema, data)

        self.gemini.respond_structured.reset_mock()
        self.gemini.respond_structured.side_effect = unavailable_verification
        blocked = run_pipeline(DOCUMENT, "recipe.pdf", self.gemini, self.openai)
        self.assertEqual(blocked["vegan_plan"]["changes"], [])
        self.assertEqual(set(blocked["scaling_plan"]), {"original"})
        self.assertEqual(self.gemini.respond_structured.call_count, 2)

        self.openai.respond.side_effect = None
        invalid = json.dumps({**proposal, "changes": [{**proposal["changes"][0], "path": "/bad"}]})
        self.openai.respond.reset_mock()
        self.openai.respond.side_effect = [invalid, json.dumps(proposal)]
        with self.assertLogs("cookbook_ocr.pipeline", level="WARNING") as logged:
            retried = plan_vegan_recipe(self.openai, report["recipe"])
        self.assertEqual(retried["status"], "ready")
        self.assertEqual(self.openai.respond.call_count, 2)
        self.assertIn("supported recipe field", logged.output[0])
        self.assertIn("previous response did not validate", self.openai.respond.call_args.kwargs["input"][0]["content"])

        self.openai.respond.side_effect = [invalid, invalid]
        with self.assertLogs("cookbook_ocr.pipeline", level="WARNING") as logged:
            rejected = plan_vegan_recipe(self.openai, report["recipe"])
        self.assertEqual((rejected["status"], rejected["reason_code"], rejected["changes"]),
                         ("unavailable", "invalid_output", []))
        self.assertEqual(len(logged.output), 2)
