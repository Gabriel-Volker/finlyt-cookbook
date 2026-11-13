import unittest
from copy import deepcopy

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

    def test_labels_implied_tools_and_keeps_the_linked_action_as_evidence(self):
        candidate = porridge()
        action = "Bake at 180 °C until golden."
        transcriptions = {source: text + "\n" + action for source, text in TRANSCRIPTIONS.items()}
        candidate["tools"].append({"id": "oven", "name": span("oven", action), "inferred": True})
        candidate["steps"].append({
            "instruction": span(action), "ingredient_ids": [], "tool_ids": ["oven"],
            "duration": None, "condition": None,
        })
        result = build_recipe(candidate, transcriptions, [])
        self.assertEqual(result["tools"][0]["name"]["status"], "stated")
        self.assertEqual(result["tools"][1]["name"], {
            "value": "oven", "status": "inferred",
            "evidence": {"source": "A", "page": "2", "quote": action}, "review_item_ids": [],
        })
        self.assertEqual(result["steps"][-1]["tool_ids"], ["oven"])

        candidate["steps"][-1]["tool_ids"] = []
        issues = []
        unresolved = build_recipe(candidate, transcriptions, [], extraction_issues=issues)
        self.assertIsNone(unresolved["tools"][1]["name"]["value"])
        self.assertEqual(unresolved["tools"][1]["name"]["status"], "needs_review")
        self.assertEqual(issues[0]["field"], "/tools/1/name")

    def test_withholds_an_implied_tool_with_disputed_action_evidence(self):
        candidate = porridge()
        cook = "Bake until golden."
        alternative = "Fry until golden."
        transcriptions = {
            "A": TRANSCRIPTIONS["A"] + "\n" + cook,
            "B": TRANSCRIPTIONS["B"] + "\n" + alternative,
        }
        candidate["tools"].append({"id": "oven", "name": span("oven", cook), "inferred": True})
        candidate["steps"].append({
            "instruction": span(cook), "ingredient_ids": [], "tool_ids": ["oven"],
            "duration": None, "condition": None,
        })
        review = [{"kind": "conflict", "page": "2", "a_excerpt": cook, "b_excerpt": alternative}]
        name = build_recipe(candidate, transcriptions, review)["tools"][1]["name"]
        self.assertEqual(name["status"], "needs_review")
        self.assertIsNone(name["value"])
        self.assertEqual(name["evidence"], {"source": "A", "page": "2", "quote": cook})
        self.assertEqual(name["review_item_ids"], [1])

    def test_leaves_missing_servings_and_ingredient_quantities_unknown(self):
        recipe = build_recipe(porridge(), TRANSCRIPTIONS, [])

        for field in (recipe["servings"], recipe["ingredients"][1]["quantity"]):
            self.assertIsNone(field["value"])
            self.assertEqual(field["status"], "missing")
            self.assertIsNone(field["evidence"])
            self.assertEqual(field["review_item_ids"], [])

    def test_withholds_an_ingredient_amount_with_an_unreadable_digit(self):
        illegible = "2[unreadable] cups water"
        transcriptions = {source: text.replace(f"{value} cups water", illegible)
                          for source, text, value in (("A", TRANSCRIPTIONS["A"], "2"),
                                                      ("B", TRANSCRIPTIONS["B"], "3"))}
        candidate = porridge()
        candidate["ingredients"][0]["quantity"] = span("2", illegible)
        candidate["ingredients"][0]["unit"] = span("cups", illegible)
        findings = [{
            "kind": "uncertain", "page": "1", "a_excerpt": "[unreadable]", "b_excerpt": None,
        }]

        result = build_recipe(candidate, transcriptions, findings, extraction_issues=[])

        quantity = result["ingredients"][0]["quantity"]
        self.assertIsNone(quantity["value"])
        self.assertEqual(quantity["status"], "needs_review")
        self.assertEqual(quantity["evidence"]["quote"], illegible)
        self.assertEqual(quantity["review_item_ids"], [1])

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

    def test_rejects_partial_mixed_fractions_even_with_a_short_quote(self):
        cases = (
            ("1 1/2", "1/2"), ("1 1/2", "1"), ("1½", "½"),
            ("1½", "1"), ("1 ½", "½"), ("1\n1⁄2", "1⁄2"),
        )
        for written, partial in cases:
            with self.subTest(written=written, partial=partial):
                candidate = porridge()
                candidate["ingredients"][0]["quantity"] = span(partial)
                text = TRANSCRIPTIONS["A"].replace("2 cups", f"{written} cups")
                readings = {**TRANSCRIPTIONS, "A": text}
                with self.assertRaisesRegex(ValueError, "partial measure"):
                    build_recipe(candidate, readings, [])

    def test_preserves_verbatim_complete_mixed_fractions(self):
        for written, submitted in (("1 1/2", "1 1/2"), ("1½", "1½"), ("1\n1/2", "1 1/2")):
            with self.subTest(written=written):
                candidate = porridge()
                candidate["ingredients"][0]["quantity"] = span(submitted, f"{submitted} cups water")
                candidate["ingredients"][0]["unit"] = span("cups", f"{submitted} cups water")
                text = TRANSCRIPTIONS["A"].replace("2 cups", f"{written} cups")
                readings = {**TRANSCRIPTIONS, "A": text}
                quantity = build_recipe(candidate, readings, [])["ingredients"][0]["quantity"]
                self.assertEqual(quantity["value"], written)
                self.assertEqual(quantity["evidence"]["quote"], f"{written} cups water")

    def test_requires_context_when_a_fraction_is_both_standalone_and_mixed(self):
        candidate = porridge()
        text = TRANSCRIPTIONS["A"].replace("2 cups water", "1 1/2 cups water\n1/2 cups milk")
        candidate["ingredients"][0]["quantity"] = span("1/2")
        readings = {**TRANSCRIPTIONS, "A": text}
        with self.assertRaisesRegex(ValueError, "ambiguous evidence"):
            build_recipe(candidate, readings, [])

    def test_does_not_join_fractions_across_paragraphs_or_ingredients(self):
        for prefix in ("188\n\n", "2 eggs\n", "2, "):
            with self.subTest(prefix=prefix):
                candidate = porridge()
                candidate["ingredients"][0]["quantity"] = span("1/2", "1/2 cups water")
                text = TRANSCRIPTIONS["A"].replace("2 cups", f"{prefix}1/2 cups")
                readings = {**TRANSCRIPTIONS, "A": text}
                quantity = build_recipe(candidate, readings, [])["ingredients"][0]["quantity"]
                self.assertEqual(quantity["value"], "1/2")

    def test_accepts_a_fraction_below_a_printed_page_label(self):
        candidate = porridge()
        candidate["ingredients"][0]["quantity"] = span("1/2", "1/2 cups water")
        candidate["ingredients"][0]["unit"] = span("cups")
        text = TRANSCRIPTIONS["A"].replace("Oat porridge\n2 cups", "Page 188\n1/2 cups")
        candidate["title"] = None
        quantity = build_recipe(candidate, {**TRANSCRIPTIONS, "A": text}, [])["ingredients"][0]["quantity"]
        self.assertEqual(quantity["value"], "1/2")

    def test_does_not_use_generated_page_markers_as_quantity_evidence(self):
        candidate = porridge()
        candidate["ingredients"][0]["quantity"] = span("1")
        with self.assertRaisesRegex(ValueError, "partial measure"):
            build_recipe(candidate, TRANSCRIPTIONS, [])

    def test_mixed_fraction_conflict_withholds_either_reading(self):
        readings = {
            "A": TRANSCRIPTIONS["A"].replace("2 cups", "1½ cups"),
            "B": TRANSCRIPTIONS["B"].replace("3 cups", "½ cups"),
        }
        reviews = [{"page": "1", "a_excerpt": "1½ cups", "b_excerpt": "½ cups"}]
        for source, written in (("A", "1½"), ("B", "½")):
            with self.subTest(source=source):
                candidate = porridge()
                candidate["ingredients"][0]["quantity"] = span(written, f"{written} cups", source)
                candidate["ingredients"][0]["unit"] = span("cups")
                quantity = build_recipe(candidate, readings, reviews)["ingredients"][0]["quantity"]
                self.assertIsNone(quantity["value"])
                self.assertEqual(quantity["status"], "needs_review")
                self.assertEqual(quantity["review_item_ids"], [1])

    def test_rejects_dangling_step_references(self):
        for reference in ("ingredient_ids", "tool_ids"):
            with self.subTest(reference=reference):
                candidate = porridge()
                candidate["steps"][0][reference].append("missing")
                with self.assertRaises(ValueError):
                    build_recipe(candidate, TRANSCRIPTIONS, [])

    def test_preserves_evidence_when_a_measure_is_taken_from_the_wrong_ingredient(self):
        candidate = porridge()
        candidate["ingredients"][0].update(quantity=span("1", "1 cup salt"), unit=span("cup", "1 cup salt"))
        for separator in ("; ", " "):
            with self.subTest(separator=separator):
                text = TRANSCRIPTIONS["A"].replace("2 cups water", f"2 cups water{separator}1 cup salt")
                readings = {"A": text, "B": text}
                with self.assertRaisesRegex(ValueError, "same ingredient"):
                    build_recipe(candidate, readings, [])
                issues = []
                water = build_recipe(candidate, readings, [], extraction_issues=issues)["ingredients"][0]
                for key in ("quantity", "unit"):
                    self.assertIsNone(water[key]["value"])
                    self.assertEqual(water[key]["status"], "needs_review")
                    self.assertEqual(water[key]["evidence"]["quote"], "1 cup salt")
                self.assertEqual([item["field"] for item in issues], ["/ingredients/0/quantity", "/ingredients/0/unit"])

    def test_rejects_cooking_duration_as_an_ingredient_amount(self):
        candidate = porridge()
        candidate["ingredients"][0].update(quantity=span("10", "Simmer water for 10 minutes."), unit=None)
        text = "[page: 1]\nOat porridge\nSimmer water for 10 minutes.\nrolled oats\nsaucepan"
        candidate["steps"] = []
        with self.assertRaisesRegex(ValueError, "same ingredient"):
            build_recipe(candidate, {"A": text, "B": text}, [])

    def test_quantity_and_unit_from_two_readings_must_belong_to_the_same_measure(self):
        candidate = porridge()
        candidate["ingredients"][0].update(
            quantity=span("1/2", "1/2 cup water"), unit=span("cup", "½ cup water", source="B"),
        )
        first = TRANSCRIPTIONS["A"].replace("2 cups water", "1/2 cup water")
        second = first.replace("1/2 cup water", "½ cup water")
        water = build_recipe(candidate, {"A": first, "B": second}, [])["ingredients"][0]
        self.assertEqual((water["quantity"]["value"], water["unit"]["value"]), ("1/2", "cup"))
        candidate["ingredients"][0]["unit"] = span("ml", "500 ml water", source="B")
        second = first.replace("1/2 cup water", "500 ml water")
        with self.assertRaisesRegex(ValueError, "same ingredient amount"):
            build_recipe(candidate, {"A": first, "B": second}, [])

    def test_deduplicates_one_source_occurrence_and_preserves_distinct_measures(self):
        candidate = porridge()
        water = candidate["ingredients"][0]
        water["name"] = span("water", "2 cups water")
        duplicate = {**deepcopy(water), "id": "water-repeat"}
        candidate["ingredients"].append(duplicate)
        candidate["steps"][0]["ingredient_ids"].append("water-repeat")
        readings = {"A": TRANSCRIPTIONS["A"], "B": TRANSCRIPTIONS["A"]}
        result = build_recipe(candidate, readings, [])
        self.assertEqual([item["id"] for item in result["ingredients"]], ["water", "oats"])
        self.assertEqual(result["steps"][0]["ingredient_ids"], ["water", "oats"])

        duplicate.update(name=span("water", "1 cup water"), quantity=span("1", "1 cup water"),
                         unit=span("cup", "1 cup water"))
        text = TRANSCRIPTIONS["A"].replace("2 cups water", "2 cups water\n1 cup water")
        result = build_recipe(candidate, {"A": text, "B": text}, [])
        self.assertEqual([item["id"] for item in result["ingredients"]], ["water", "oats", "water-repeat"])

    def test_flags_an_agreed_measured_line_omitted_from_an_ingredient_section(self):
        candidate = porridge()
        text = TRANSCRIPTIONS["A"].replace("2 cups water\nrolled oats", "Ingredients:\n2 cups water\n100 g sugar\nrolled oats")
        readings = {"A": text, "B": text}
        with self.assertRaisesRegex(ValueError, "missing from the extraction"):
            build_recipe(candidate, readings, [])
        issues = []
        result = build_recipe(candidate, readings, [], extraction_issues=issues)
        self.assertEqual([item["id"] for item in result["ingredients"]], ["water", "oats"])
        self.assertEqual(issues[0]["field"], "/ingredients")
        self.assertEqual(issues[0]["evidence"], {"source": "A", "page": "1", "quote": "100 g sugar"})
