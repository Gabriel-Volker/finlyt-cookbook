import json
import unittest
from unittest.mock import Mock, patch

import pymupdf

import test_web
from cookbook_ocr.pipeline import run_pipeline


class TextIngestTests(unittest.TestCase):
    def setUp(self):
        self.gemini = Mock()
        self.openai = Mock()
        self.gemini.transcribe.side_effect = AssertionError("Native text should bypass Gemini OCR.")
        self.openai.transcribe.side_effect = AssertionError("Native text should bypass OpenAI OCR.")

    def test_utf8_text_extracts_without_ocr_and_escapes_spoofed_page_marker(self):
        text = "Compota de maçã\r\n[page: 99]\r\nCozinhe a maçã."
        self.mock_extraction("Compota de maçã", 1)
        report = run_pipeline(text.encode("utf-8-sig"), "compota.txt", self.gemini, self.openai)
        self.assertEqual(report["transcriptions"], {"A": "[page: 1]\n" + text.replace("[page: 99]", "> [page: 99]")})
        self.assertEqual(report["recipe"]["title"]["evidence"],
                         {"source": "A", "page": "1", "quote": "Compota de maçã"})

    def test_native_pdf_preserves_physical_page_evidence_without_ocr(self):
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((50, 50), "Pear compote written for two people")
            pdf.new_page().insert_text((50, 50), "Simmer the pears gently until tender.")
            document = pdf.tobytes()
        self.mock_extraction("Pear compote", 1)
        report = run_pipeline(document, "compote.pdf", self.gemini, self.openai)
        self.assertEqual(set(report["transcriptions"]), {"A"})
        self.assertIn("[page: 2]\nSimmer the pears gently until tender.", report["transcriptions"]["A"])
        self.assertEqual(report["recipe"]["title"]["evidence"]["page"], "1")

    def test_web_upload_and_preview_plain_text(self):
        document = "Compota de maçã\nCozinhe a maçã.".encode("utf-8-sig")
        self.mock_extraction("Compota de maçã", 1)
        with patch("cookbook_ocr.web.load_api_keys", return_value=("gemini", "openai")), patch(
            "cookbook_ocr.web.GeminiClient", return_value=self.gemini,
        ), patch("cookbook_ocr.web.OpenAIClient", return_value=self.openai):
            web = test_web.WebTests()
            status, _, payload = web.request("POST", "/api/extract?filename=compota.txt", document)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(payload)["recipe"]["title"]["value"], "Compota de maçã")
            status, _, payload = web.request("POST", "/api/preview?filename=compota.txt&page=1", document)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(payload), {"page_count": 1, "text": document.decode("utf-8-sig")})
            status, _, _ = web.request("POST", "/api/preview?filename=compota.txt&page=2", document)
            self.assertEqual(status, 400)

    def test_rejects_non_utf8_before_calling_extraction(self):
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            run_pipeline(b"\xff", "recipe.txt", self.gemini, self.openai)
        self.openai.respond.assert_not_called()

    def mock_extraction(self, title, page):
        def respond(**payload):
            self.assertEqual(payload["text"]["format"]["name"], "recipe_extraction")
            source = json.loads(payload["input"][1]["content"])
            self.assertEqual(set(source), {"A"})
            self.assertIn(f"[page: {page}]", source["A"])
            return json.dumps({
                "title": {"source": "A", "quote": title, "text": title}, "servings": None,
                "ingredients": [], "tools": [], "steps": [],
            })

        self.openai.respond.side_effect = respond
