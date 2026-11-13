import json
import unittest
from copy import deepcopy
from unittest.mock import Mock

from cookbook_ocr.translation import TranslationError, translate_output, translate_recipe


def field(value, status="stated"):
    return {
        "value": value, "status": status,
        "evidence": {"source": "A", "page": "1", "quote": value} if value is not None else None,
        "review_item_ids": [],
    }


def german_recipe():
    return {
        "title": field("Birnenkompott"), "servings": field("4 Portionen"),
        "ingredients": [{
            "id": "birnen", "name": field("Birnen"), "quantity": field("1½"),
            "unit": field("Tassen"), "preparation": field("geschält", "reviewed"),
            "optional_when": field(None, "needs_review"),
        }, {
            "id": "zucker", "name": field("Zucker"), "quantity": field("einige"),
            "unit": field(None, "missing"), "preparation": field(None, "missing"),
            "optional_when": field(None, "missing"),
        }],
        "tools": [{"id": "topf", "name": field("Topf")}],
        "steps": [{
            "instruction": field("1½ Tassen Birnen bei 125°C für 15 Minuten kochen."),
            "duration": field("15 Minuten"), "condition": field(None, "needs_review"),
            "ingredient_ids": ["birnen", "zucker"], "tool_ids": ["topf"],
        }],
    }


ENGLISH = {
    "/title": "Pear compote", "/servings": "[MEASURE_A] servings",
    "/ingredients/0/name": "Pears", "/ingredients/0/preparation": "peeled",
    "/ingredients/1/name": "Sugar", "/ingredients/1/quantity": "some",
    "/tools/0/name": "Saucepan",
    "/steps/0/instruction": "Cook [MEASURE_A] of pears at [MEASURE_B] for [MEASURE_C].",
    "/steps/0/duration": "[MEASURE_A]",
}


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.openai = Mock()
        self.recipe = german_recipe()

    def response(self, values=None):
        translations = ENGLISH if values is None else values
        self.openai.respond.return_value = json.dumps({"translations": [
            {"path": path, "text": text} for path, text in translations.items()
        ]})

    def test_preserves_recipe_structure_values_and_evidence(self):
        self.response()
        before = deepcopy(self.recipe)
        result = translate_recipe(self.openai, self.recipe)
        self.assertEqual(self.recipe, before)
        self.assertEqual(result["title"], {**before["title"], "value": "Pear compote"})
        self.assertEqual(result["servings"]["value"], "4 servings")
        first, second = result["ingredients"]
        self.assertEqual(first["quantity"], before["ingredients"][0]["quantity"])
        self.assertEqual(first["unit"]["value"], "cups")
        self.assertEqual(first["preparation"]["status"], "reviewed")
        self.assertEqual(first["optional_when"], before["ingredients"][0]["optional_when"])
        self.assertEqual(second["quantity"]["value"], "some")
        self.assertEqual(second["unit"], before["ingredients"][1]["unit"])
        step = result["steps"][0]
        self.assertEqual(step["ingredient_ids"], ["birnen", "zucker"])
        self.assertEqual(step["tool_ids"], ["topf"])
        self.assertEqual(step["instruction"]["value"], "Cook 1½ cups of pears at 125°C for 15 min.")
        self.assertEqual(step["duration"]["value"], "15 min")
        self.assertEqual(step["instruction"]["evidence"], before["steps"][0]["instruction"]["evidence"])

        call = self.openai.respond.call_args.kwargs
        user_fields = json.loads(call["input"][1]["content"])["fields"]
        self.assertEqual({item["path"] for item in user_fields}, set(ENGLISH))
        self.assertTrue(all(set(item) == {"path", "text"} for item in user_fields))
        self.assertEqual(call["input"][0]["role"], "system")
        self.assertIn("untrusted", call["input"][0]["content"])
        self.assertEqual(call["input"][1]["role"], "user")

    def test_rejects_any_altered_or_extra_numeric_text(self):
        path = "/steps/0/instruction"
        for text in (
            "Cook 1 cup at [MEASURE_B] for [MEASURE_C].",
            "Cook [MEASURE_A] at [MEASURE_B] for ⅖ [MEASURE_C].",
            "Cook [MEASURE_B] at [MEASURE_A] for [MEASURE_C].",
            "Cook [MEASURE_A] at [MEASURE_B] for [MEASURE_C] or [MEASURE_D].",
            "Cook [MEASURE_A] at [MEASURE_B] for [MEASURE_C] [MEASURE_C].",
        ):
            with self.subTest(text=text), self.assertRaisesRegex(TranslationError, "protected recipe measure"):
                self.response({**ENGLISH, path: text})
                translate_recipe(self.openai, self.recipe)

    def test_rejects_translation_that_is_missing_uncertain_or_edits_unknown_fields(self):
        without_title = {path: text for path, text in ENGLISH.items() if path != "/title"}
        for fields in (without_title, {**ENGLISH, "/title": None}, {**ENGLISH, "/ingredients/0/id": "pear"}):
            with self.subTest(fields=fields), self.assertRaises(TranslationError):
                self.response(fields)
                translate_recipe(self.openai, self.recipe)

    def test_missing_recipe_fields_fail_before_calling_provider(self):
        del self.recipe["steps"][0]["condition"]
        with self.assertRaisesRegex(TranslationError, "cannot be prepared"):
            translate_recipe(self.openai, self.recipe)
        self.openai.respond.assert_not_called()

    def test_no_provider_call_when_only_units_and_numeric_values_are_present(self):
        recipe = {
            "title": field(None, "missing"), "servings": field("4"),
            "ingredients": [], "tools": [], "steps": [],
        }
        self.assertEqual(translate_recipe(self.openai, recipe), recipe)
        self.openai.respond.assert_not_called()

    def test_german_ordinal_days_keep_ordinal_meaning(self):
        self.recipe["steps"][0]["condition"] = field("nach dem 1. Tag oder 2. Tag")
        self.response({**ENGLISH, "/steps/0/condition": "after the [MEASURE_A] or [MEASURE_B]"})
        translated = translate_recipe(self.openai, self.recipe)
        self.assertEqual(translated["steps"][0]["condition"]["value"], "after the 1st day or 2nd day")

    def test_translates_notes_using_separate_paths_and_preserves_numbers(self):
        self.response({**ENGLISH, "/display/0": "Confirm the [MEASURE_A] quantity."})
        result = translate_output(self.openai, self.recipe, {"/ingredients/0/name": "Die Menge von 1½ Tassen bestätigen."})
        self.assertEqual(result["recipe"]["ingredients"][0]["name"]["value"], "Pears")
        self.assertEqual(result["texts"], {"/ingredients/0/name": "Confirm the 1½ cups quantity."})

    def test_original_language_translates_notes_only(self):
        self.response({"/display/0": "[MEASURE_A] Minuten prüfen."})
        original = german_recipe()
        result = translate_output(self.openai, original, {"time": "Check [the] 15 minutes."}, language="original")
        self.assertEqual(result, {"recipe": original, "texts": {"time": "15 Minuten prüfen."}})
        data = json.loads(self.openai.respond.call_args.kwargs["input"][1]["content"])
        self.assertEqual(data["language"], "original")
        self.assertIn("Birnenkompott", data["source_examples"])
        self.assertEqual(data["fields"], [{"path": "/display/0", "text": "Check [the] [MEASURE_A] minutes."}])

    def test_original_language_without_notes_needs_no_provider(self):
        self.assertEqual(translate_output(None, self.recipe, {}, language="original"),
                         {"recipe": self.recipe, "texts": {}})
