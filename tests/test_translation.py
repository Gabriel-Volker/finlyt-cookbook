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

    def test_rejects_the_ten_minute_inversion_and_accepts_equivalent_maximums(self):
        path = "/steps/0/instruction"
        self.recipe["steps"][0]["instruction"] = field("Die Sauce nicht länger als 10 Minuten kochen.")
        self.response({**ENGLISH, path: "Boil the sauce for longer than [MEASURE_A]."})
        with self.assertRaisesRegex(TranslationError, "negation or a recipe limit"):
            translate_recipe(self.openai, self.recipe)

        self.response({**ENGLISH, path: "Do not stir and boil the sauce for longer than [MEASURE_A]."})
        with self.assertRaisesRegex(TranslationError, "negation or a recipe limit"):
            translate_recipe(self.openai, self.recipe)

        for reply in (
            "Boil the sauce for at most [MEASURE_A].",
            "Do not boil the sauce for longer than [MEASURE_A].",
            "Boil the sauce for [MEASURE_A] or less.",
        ):
            with self.subTest(reply=reply):
                self.response({**ENGLISH, path: reply})
                result = translate_recipe(self.openai, self.recipe)
                self.assertIn("10 min", result["steps"][0]["instruction"]["value"])

    def test_compares_each_minimum_and_maximum_without_loosening_a_strict_limit(self):
        path = "/steps/0/instruction"
        self.recipe["steps"][0]["instruction"] = field("Cozinhe por no mínimo 5 minutos e por menos de 10 minutos.")
        for reply in (
            "Cook for at most [MEASURE_A] and for less than [MEASURE_B].",
            "Cook for at least [MEASURE_A] and for at most [MEASURE_B].",
            "Cook for [MEASURE_A] and for [MEASURE_B].",
        ):
            with self.subTest(reply=reply), self.assertRaisesRegex(TranslationError, "negation or a recipe limit"):
                self.response({**ENGLISH, path: reply})
                translate_recipe(self.openai, self.recipe)

        self.response({**ENGLISH, path: "Cook for at least [MEASURE_A] and for less than [MEASURE_B]."})
        result = translate_recipe(self.openai, self.recipe)
        self.assertEqual(result["steps"][0]["instruction"]["value"], "Cook for at least 5 min and for less than 10 min.")

    def test_rejects_a_lost_or_moved_negation_without_any_numbers(self):
        path = "/steps/0/instruction"
        self.recipe["steps"][0]["instruction"] = field("Não mexa. Depois cubra.")
        for reply in ("Stir. Then cover.", "Stir. Then do not cover."):
            with self.subTest(reply=reply), self.assertRaisesRegex(TranslationError, "negation or a recipe limit"):
                self.response({**ENGLISH, path: reply})
                translate_recipe(self.openai, self.recipe)

        self.response({**ENGLISH, path: "Do not stir. Then cover."})
        result = translate_recipe(self.openai, self.recipe)
        self.assertEqual(result["steps"][0]["instruction"]["value"], "Do not stir. Then cover.")

    def test_accepts_a_negated_condition_moved_to_start_of_sentence(self):
        path = "/steps/0/instruction"
        self.recipe["steps"][0]["instruction"] = field("Den Braten, wenn er nicht geschnitten ist, in die Soße legen.")
        translated = "If the roast has not been sliced, place it in the sauce."
        self.response({**ENGLISH, path: translated})
        result = translate_recipe(self.openai, self.recipe)
        self.assertEqual(result["steps"][0]["instruction"]["value"], translated)

        self.response({**ENGLISH, path: "If the roast has been sliced, do not place it in the sauce."})
        with self.assertRaisesRegex(TranslationError, "negation or a recipe limit"):
            translate_recipe(self.openai, self.recipe)

    def test_accepts_not_until_after_as_an_equivalent_time_bound(self):
        path = "/steps/0/instruction"
        self.recipe["steps"][0]["instruction"] = field("Soll der Braten erst nach 8 Tagen zubereitet werden, gießt man es kalt darauf.")
        self.response({**ENGLISH, path: "If the roast is not to be prepared until after [MEASURE_A], pour it over cold."})
        result = translate_recipe(self.openai, self.recipe)
        self.assertIn("until after 8 days", result["steps"][0]["instruction"]["value"])

        self.recipe["steps"][0]["instruction"] = field("Não mexa e cubra.")
        self.response({**ENGLISH, path: "Stir and do not cover."})
        with self.assertRaisesRegex(TranslationError, "negation or a recipe limit"):
            translate_recipe(self.openai, self.recipe)

    def test_preserves_a_numeric_range_across_equivalent_wording(self):
        path = "/steps/0/instruction"
        self.recipe["steps"][0]["instruction"] = field("Cozinhe por entre 5 e 10 minutos.")
        self.response({**ENGLISH, path: "Cook for [MEASURE_A] and [MEASURE_B]."})
        with self.assertRaisesRegex(TranslationError, "negation or a recipe limit"):
            translate_recipe(self.openai, self.recipe)

        self.response({**ENGLISH, path: "Cook for between [MEASURE_A] and [MEASURE_B]."})
        result = translate_recipe(self.openai, self.recipe)
        self.assertEqual(result["steps"][0]["instruction"]["value"], "Cook for between 5 and 10 min.")

    def test_original_language_notes_preserve_inclusive_bounds_and_prohibitions(self):
        notes = {"time": "Do not boil for longer than 10 minutes. Do not stir."}
        self.response({"/display/0": "Länger als [MEASURE_A] Minuten kochen. Nicht umrühren."})
        with self.assertRaisesRegex(TranslationError, "negation or a recipe limit"):
            translate_output(self.openai, self.recipe, notes, language="original")

        self.response({"/display/0": "Höchstens [MEASURE_A] Minuten kochen. Nicht umrühren."})
        result = translate_output(self.openai, self.recipe, notes, language="original")
        self.assertEqual(result["texts"], {"time": "Höchstens 10 Minuten kochen. Nicht umrühren."})

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

    def test_cached_vegan_steps_use_the_current_fractions_and_units(self):
        path = "/steps/0/instruction"
        variant = deepcopy(self.recipe)
        variant["steps"][0]["instruction"]["value"] = "Koche ½ l\nWasser und 1 Tasse Äpfel."
        variant_notes = {"batches": "Batches required: 5."}
        templates = {}
        self.response({
            **ENGLISH, f"/variant{path}": "Boil [MEASURE_A] of water and [MEASURE_B] of apples.",
            "/prefetch/0": "Batches required: [MEASURE_A].",
        })
        translate_output(
            self.openai, self.recipe, {}, templates=templates, variant=variant,
            prefetch_texts=("Batches required: 2.",),
        )
        self.openai.reset_mock()
        for german, english in (
            ("250 ml\nWasser und 2 Tassen Äpfel", "250 mL of water and 2 cups of apples"),
            ("2½ l\nWasser und 5 Tassen Äpfel", "2½ L of water and 5 cups of apples"),
            ("3 l\nWasser und 6 Tassen Äpfel", "3 L of water and 6 cups of apples"),
        ):
            with self.subTest(amounts=german):
                variant["steps"][0]["instruction"]["value"] = f"Koche {german}."
                result = translate_output(self.openai, variant, variant_notes, templates=templates, cache_only=True)
                self.assertEqual(result["recipe"]["steps"][0]["instruction"]["value"], f"Boil {english}.")
                self.assertEqual(result["texts"], variant_notes)
        self.openai.respond.assert_not_called()

    def test_cached_render_never_reuses_a_translation_for_a_different_step(self):
        path = "/steps/0/instruction"
        self.response()
        templates = {}
        translate_output(self.openai, self.recipe, {}, templates=templates)
        self.openai.reset_mock()
        self.recipe["steps"][0]["instruction"]["value"] = "2 Tassen Äpfel ohne Deckel kochen."
        with self.assertRaisesRegex(TranslationError, "current recipe is not ready"):
            translate_output(self.openai, self.recipe, {}, templates=templates, cache_only=True)
        result = translate_output(
            self.openai, self.recipe, {"uncached": "Check the pot."},
            language="original", templates={}, cache_only=True,
        )
        self.assertEqual(result, {"recipe": self.recipe, "texts": {"uncached": "Check the pot."}})
        self.openai.respond.assert_not_called()
