import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from cookbook_ocr.pipeline import (
    compare_transcriptions,
    run_pipeline,
    validate_comparison,
)


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
TRANSCRIPTION_A = "Page 1\nPear compote by Mara\nUse ¾ cup of water."
TRANSCRIPTION_B = "Page 1\nPear compote by Ma[unreadable]\nUse 3/4 cup of water."
DOCUMENT = b"%PDF-1.7\n"


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
        self.openai.respond.return_value = json.dumps(comparison)

        report = run_pipeline(DOCUMENT, "recipe.pdf", self.gemini, self.openai)

        self.gemini.transcribe.assert_called_once()
        self.openai.transcribe.assert_called_once()
        gemini_args = self.gemini.transcribe.call_args.args
        luna_args = self.openai.transcribe.call_args.args
        self.assertEqual(gemini_args, luna_args)
        self.assertEqual(gemini_args[:2], (DOCUMENT, "recipe.pdf"))
        self.assertEqual(
            report["transcriptions"], {"A": TRANSCRIPTION_A, "B": TRANSCRIPTION_B}
        )
        self.assertEqual(report["comparison"], comparison)
        self.assertEqual(report["status"], "needs_review")

    def test_equivalent_fraction_does_not_require_review(self):
        self.gemini.transcribe.return_value = "Use ¾ cup of water."
        self.openai.transcribe.return_value = "Use 3/4 cup of water."
        comparison = comparison_with_uncertainty()
        comparison["review_items"] = []
        comparison["equivalent_variations"][0]["page"] = None
        self.openai.respond.return_value = json.dumps(comparison)

        report = run_pipeline(DOCUMENT, "recipe.pdf", self.gemini, self.openai)

        self.assertEqual(report["status"], "no_discrepancies")
        self.assertEqual(report["comparison"], comparison)

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
