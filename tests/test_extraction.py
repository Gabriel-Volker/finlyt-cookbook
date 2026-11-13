import unittest

from cookbook_ocr.extraction import build_recipe
from cookbook_ocr.pipeline import validate_comparison


TRANSCRIPTIONS = {
    "A": "[page: 1]\nOat porridge\n2 cups water\nrolled oats\n"
    "[page: 2]\nIn a saucepan, simmer water and rolled oats for 10 minutes.\n"
    "Serve when thick.",
    "B": "[page: 1]\nOat porridge\n3 cups water\nrolled oats\n"
    "[page: 2]\nIn a saucepan, simmer water and rolled oats for 10 minutes.\n"
    "Serve when thick.",
}


def span(text, quote=None, source="A"):
    return {"source": source, "text": text, "quote": quote or text}


def porridge():
    return {
        "title": span("Oat porridge"),
        "servings": None,
        "ingredients": [
            {
                "id": "water",
                "name": span("water"),
                "quantity": span("2", "2 cups water"),
                "unit": span("cups", "2 cups water"),
                "preparation": None,
                "optional_when": None,
            },
            {
                "id": "oats",
                "name": span("rolled oats"),
                "quantity": None,
                "unit": None,
                "preparation": None,
                "optional_when": None,
            },
        ],
        "tools": [{"id": "pan", "name": span("saucepan", source="B")}],
        "steps": [
            {
                "instruction": span(
                    "In a saucepan, simmer water and rolled oats for 10 minutes."
                ),
                "ingredient_ids": ["water", "oats"],
                "tool_ids": ["pan"],
                "duration": span("10 minutes"),
                "condition": None,
            },
            {
                "instruction": span("Serve when thick."),
                "ingredient_ids": [],
                "tool_ids": [],
                "duration": None,
                "condition": span("when thick"),
            },
        ],
    }


class ExtractionTests(unittest.TestCase):
    def test_rejects_conflict_evidence_attributed_to_the_wrong_page(self):
        review = {
            "kind": "conflict", "page": "2", "a_excerpt": "2 cups water",
            "b_excerpt": "3 cups water",
        }
        comparison = {"review_items": [review], "equivalent_variations": []}
        with self.assertRaisesRegex(ValueError, "wrong page"):
            validate_comparison(comparison, TRANSCRIPTIONS["A"], TRANSCRIPTIONS["B"])
        review["page"] = "1"
        validate_comparison(comparison, TRANSCRIPTIONS["A"], TRANSCRIPTIONS["B"])
        recipe = build_recipe(porridge(), TRANSCRIPTIONS, comparison["review_items"])
        self.assertEqual(recipe["ingredients"][0]["quantity"]["status"], "needs_review")

    def test_preserves_explicit_quantities_and_links_steps_to_entities(self):
        consistent_readings = {"A": TRANSCRIPTIONS["A"], "B": TRANSCRIPTIONS["A"]}
        recipe = build_recipe(porridge(), consistent_readings, [])

        water = recipe["ingredients"][0]
        self.assertEqual(water["quantity"]["value"], "2")
        self.assertEqual(water["quantity"]["status"], "stated")
        self.assertEqual(water["unit"]["value"], "cups")
        self.assertEqual(water["quantity"]["evidence"]["quote"], "2 cups water")
        self.assertEqual(recipe["steps"][0]["ingredient_ids"], ["water", "oats"])
        self.assertEqual(recipe["steps"][0]["tool_ids"], ["pan"])
        self.assertEqual(recipe["steps"][0]["duration"]["value"], "10 minutes")
        self.assertEqual(recipe["steps"][1]["condition"]["value"], "when thick")

    def test_derives_provenance_from_the_evidence_and_page_markers(self):
        recipe = build_recipe(porridge(), TRANSCRIPTIONS, [])

        self.assertEqual(recipe["title"]["evidence"]["page"], "1")
        self.assertEqual(
            recipe["tools"][0]["name"]["evidence"],
            {"source": "B", "page": "2", "quote": "saucepan"},
        )
        self.assertEqual(recipe["steps"][0]["duration"]["evidence"]["page"], "2")

    def test_leaves_missing_servings_and_ingredient_quantities_unknown(self):
        recipe = build_recipe(porridge(), TRANSCRIPTIONS, [])

        for field in (recipe["servings"], recipe["ingredients"][1]["quantity"]):
            self.assertIsNone(field["value"])
            self.assertEqual(field["status"], "missing")
            self.assertIsNone(field["evidence"])
            self.assertEqual(field["review_item_ids"], [])

    def test_conflicting_quantity_is_withheld_and_linked_to_review(self):
        review = [
            {
                "page": "1",
                "section": "Ingredients",
                "kind": "conflict",
                "a_excerpt": "2 cups water",
                "b_excerpt": "3 cups water",
                "reason": "The readings disagree on the amount of water.",
            }
        ]

        recipe = build_recipe(porridge(), TRANSCRIPTIONS, review)

        quantity = recipe["ingredients"][0]["quantity"]
        self.assertIsNone(quantity["value"])
        self.assertEqual(quantity["status"], "needs_review")
        self.assertEqual(quantity["review_item_ids"], [1])
        self.assertEqual(
            quantity["evidence"],
            {"source": "A", "page": "1", "quote": "2 cups water"},
        )
        self.assertEqual(recipe["ingredients"][1]["name"]["value"], "rolled oats")

    def test_rejects_unsubstantiated_excerpts_and_values(self):
        for evidence in (
            span("5", "5 cups water"),
            span("5", "2 cups water"),
            span("3", "3 cups water", source="A"),
        ):
            with self.subTest(evidence=evidence):
                candidate = porridge()
                candidate["ingredients"][0]["quantity"] = evidence
                with self.assertRaises(ValueError):
                    build_recipe(candidate, TRANSCRIPTIONS, [])

    def test_recovers_literal_whitespace_from_wrapped_source(self):
        candidate = porridge()
        candidate["ingredients"][1]["name"] = span("rolled oats", "Use rolled oats")
        readings = {**TRANSCRIPTIONS, "A": "[page: 1]\nUse rolled  \noats."}
        candidate["title"] = None
        candidate["ingredients"] = candidate["ingredients"][1:]
        candidate["steps"] = []
        recipe = build_recipe(candidate, readings, [])
        name = recipe["ingredients"][0]["name"]
        self.assertEqual(name["value"], "rolled  \noats")
        self.assertEqual(name["evidence"]["quote"], "Use rolled  \noats")

    def test_rejects_a_quantity_cut_from_a_larger_number(self):
        candidate = porridge()
        readings = {**TRANSCRIPTIONS, "A": TRANSCRIPTIONS["A"].replace("2 cups", "20 cups")}
        for quote in ("2", "20 cups water"):
            with self.subTest(quote=quote):
                candidate["ingredients"][0]["quantity"] = span("2", quote)
                with self.assertRaises(ValueError):
                    build_recipe(candidate, readings, [])

    def test_rejects_dangling_step_references(self):
        for reference in ("ingredient_ids", "tool_ids"):
            with self.subTest(reference=reference):
                candidate = porridge()
                candidate["steps"][0][reference].append("missing")
                with self.assertRaises(ValueError):
                    build_recipe(candidate, TRANSCRIPTIONS, [])
