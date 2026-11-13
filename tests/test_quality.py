import json
import unittest
from io import BytesIO
from unittest.mock import Mock, patch

import pymupdf
import test_web
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from cookbook_ocr.pipeline import UnreadableRecipeError, run_pipeline
from cookbook_ocr.quality import assess_blur


RECIPE_LINES = (
    "Pear compote",
    "Ingredients",
    "4 pears, peeled and diced",
    "150 ml water",
    "2 tbsp sugar",
    "Method",
    "Combine the pears, water, and sugar.",
    "Simmer until the pears are tender.",
    "Cool before serving.",
)
UNREADABLE_PAGE = "[page: 1]\n[unleserlich]"
READABLE_PAGE = "[page: 1]\n" + "\n".join(RECIPE_LINES)
EMPTY_EXTRACTION = {"title": None, "servings": None, "ingredients": [], "tools": [], "steps": []}


def png(image):
    with BytesIO() as buffer:
        image.save(buffer, format="PNG")
        return buffer.getvalue()


def recipe_image(*, size=(1100, 1400), font_size=42, ink=30, blur=0, lines=RECIPE_LINES):
    with Image.new("L", size, 250) as image:
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=font_size)
        for index, line in enumerate(lines):
            draw.text((size[0] // 10, size[1] // 10 + index * font_size * 2), line, font=font, fill=ink)
        if blur:
            with image.filter(ImageFilter.GaussianBlur(blur)) as blurred:
                return png(blurred)
        return png(image)


def recipe_pdf(*pages):
    with pymupdf.open() as pdf:
        for image in pages:
            page = pdf.new_page(width=550, height=700)
            if image is None:
                page.insert_text((55, 90), "\n".join(RECIPE_LINES), fontsize=16)
            else:
                page.insert_image(page.rect, stream=image)
        return pdf.tobytes()


def partially_blurred_recipe_image():
    with Image.open(BytesIO(recipe_image())) as clear, Image.open(BytesIO(recipe_image(blur=18))) as blurred:
        # Preserve the original ingredient text in one region of an otherwise blurred page.
        with clear.crop((100, 290, 750, 550)) as readable:
            blurred.paste(readable, (100, 290))
        return png(blurred)


class BlurAssessmentTests(unittest.TestCase):
    def test_only_the_heavily_blurred_page_is_suspected(self):
        assessments = assess_blur([
            recipe_image(),
            recipe_image(blur=18),
            recipe_image(size=(420, 620), font_size=16, ink=155),
            recipe_image(font_size=20, ink=183),
            recipe_image(blur=3),
        ])
        self.assertEqual([item.page for item in assessments], [1, 2, 3, 4, 5])
        self.assertEqual([item.suspected for item in assessments], [False, True, False, False, False])

    def test_blank_page_is_not_classified_as_blur(self):
        blank = recipe_image(lines=())
        self.assertFalse(assess_blur([blank])[0].suspected)

    def test_keeps_a_partially_legible_page(self):
        self.assertFalse(assess_blur([partially_blurred_recipe_image()])[0].suspected)


class UnreadableImportTests(unittest.TestCase):
    def setUp(self):
        self.gemini = Mock()
        self.luna = Mock()
        self.gemini.transcribe.return_value = UNREADABLE_PAGE
        self.luna.transcribe.return_value = UNREADABLE_PAGE
        self.luna.respond.side_effect = [
            json.dumps({"review_items": [], "equivalent_variations": []}),
            json.dumps(EMPTY_EXTRACTION),
        ]

    def run_import(self, document, filename="recipe.png"):
        return run_pipeline(document, filename, self.gemini, self.luna)

    def test_rejects_heavy_blur_when_both_readers_explicitly_cannot_read_the_page(self):
        with self.assertRaises(UnreadableRecipeError):
            self.run_import(recipe_image(blur=18))

        self.gemini.transcribe.assert_called_once()
        self.luna.transcribe.assert_called_once()
        self.luna.respond.assert_not_called()

    def test_upload_returns_actionable_error_for_unreadable_blur(self):
        with patch("cookbook_ocr.web.load_api_keys", return_value=("gemini-test", "openai-test")), patch(
            "cookbook_ocr.web.GeminiClient", return_value=self.gemini,
        ), patch("cookbook_ocr.web.OpenAIClient", return_value=self.luna):
            status, _, payload = test_web.WebTests().request(
                "POST", "/api/extract?filename=recipe.png", recipe_image(blur=18),
            )

        self.assertEqual(status, 422)
        body = json.loads(payload)
        self.assertEqual(body["code"], "unreadable_blur")
        self.assertIn("sharper", body["error"])
        self.luna.respond.assert_not_called()

    def test_keeps_legible_low_contrast_text_even_if_both_readers_fail(self):
        report = self.run_import(recipe_image(font_size=20, ink=183))

        self.assertEqual(report["transcriptions"], {"A": UNREADABLE_PAGE, "B": UNREADABLE_PAGE})
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["extraction_issues"][0]["field"], "/recipe")
        self.assertEqual(self.luna.respond.call_count, 2)

    def test_keeps_a_blurred_page_when_either_reader_recovers_text(self):
        self.luna.transcribe.return_value = READABLE_PAGE
        report = self.run_import(recipe_image(blur=18))

        self.assertEqual(report["transcriptions"]["B"], READABLE_PAGE)

    def test_keeps_a_blurred_page_when_a_reader_recovers_only_a_number(self):
        self.gemini.transcribe.return_value = "[page: 1]\n4\n[unleserlich]"
        report = self.run_import(recipe_image(blur=18))

        self.assertIn("4", report["transcriptions"]["A"])

    def test_keeps_a_blurred_page_if_a_reader_returns_no_explicit_unreadable_marker(self):
        self.gemini.transcribe.return_value = "[page: 1]\n"
        report = self.run_import(recipe_image(blur=18))

        self.assertEqual(report["transcriptions"]["A"].strip(), "[page: 1]")

    def test_keeps_a_page_if_pixel_assessment_is_unavailable(self):
        with patch("cookbook_ocr.pipeline.assess_blur", return_value=[]):
            report = self.run_import(recipe_image(blur=18))

        self.assertEqual(report["transcriptions"]["A"], UNREADABLE_PAGE)

    def test_readable_non_recipe_text_does_not_count_as_unreadable(self):
        notice = "[page: 1]\nThe library closes early today."
        self.gemini.transcribe.return_value = notice
        self.luna.transcribe.return_value = notice
        report = self.run_import(recipe_image(blur=18, lines=("The library closes early today.",) * 8))

        self.assertEqual(report["transcriptions"], {"A": notice, "B": notice})

    def test_rejects_a_blurred_scan_without_a_native_text_layer(self):
        with self.assertRaises(UnreadableRecipeError):
            self.run_import(recipe_pdf(recipe_image(blur=18)), "scan.pdf")

    def test_keeps_scan_if_a_page_remains_sharp(self):
        self.gemini.transcribe.return_value = f"{UNREADABLE_PAGE}\n[page: 2]\n[unleserlich]"
        self.luna.transcribe.return_value = self.gemini.transcribe.return_value

        report = self.run_import(recipe_pdf(recipe_image(blur=18), recipe_image()), "scan.pdf")

        self.assertIn("[page: 2]", report["transcriptions"]["A"])

    def test_keeps_scan_if_either_reader_omits_a_page(self):
        self.gemini.transcribe.return_value = f"{UNREADABLE_PAGE}\n[page: 2]\n[unleserlich]"
        blurred = recipe_image(blur=18)

        report = self.run_import(recipe_pdf(blurred, blurred), "scan.pdf")

        self.assertEqual(report["transcriptions"]["B"], UNREADABLE_PAGE)

    def test_keeps_a_pdf_with_a_native_text_page_despite_suspected_blur(self):
        self.gemini.transcribe.return_value = f"{UNREADABLE_PAGE}\n[page: 2]\n[unleserlich]"
        self.luna.transcribe.return_value = self.gemini.transcribe.return_value
        false_positive = [Mock(page=1, suspected=True), Mock(page=2, suspected=True)]

        with patch("cookbook_ocr.pipeline.assess_blur", return_value=false_positive):
            report = self.run_import(recipe_pdf(recipe_image(blur=18), None), "mixed.pdf")

        self.assertEqual(self.luna.respond.call_count, 2)
        self.assertIn("[page: 2]", report["transcriptions"]["B"])
