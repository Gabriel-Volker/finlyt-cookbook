"""Render an English recipe without changing the fields used for review or scaling."""

import json
import re
from copy import deepcopy
from pathlib import Path

from .pipeline import _structured_response
from .providers import OpenAIClient
from .quantities import NUMBER_PATTERN, known_units, normalized_amount, step_unit
from .review import REVIEW_FIELDS
from .translation_constraints import preserves_constraints


class TranslationError(ValueError):
    """The English display recipe could not be produced or verified."""


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
_UNIT_LABELS = {
    "kg": ("quilo", "quilos"),
    "tbsp": ("tbsp", "tablespoon", "tablespoons", "esslöffel", "el", "colher de sopa", "colheres de sopa",
             "colher (sopa)", "colheres (sopa)"),
    "tsp": ("tsp", "teaspoon", "teaspoons", "teelöffel", "tl", "colher de chá", "colheres de chá",
            "colher (chá)", "colheres (chá)"),
    "cup": ("cup", "tasse", "xícara", "xícara (chá)", "xícara de chá"),
    "cups": ("cups", "tassen", "xícaras", "xícaras (chá)", "xícaras de chá"),
    "coffee spoon": ("coffee spoon", "colher de café", "colher (café)"),
    "coffee spoons": ("coffee spoons", "colheres de café", "colheres (café)"),
    "can": ("can", "lata", "dose"),
    "cans": ("cans", "latas", "dosen"),
    "oz": ("oz", "ounce", "ounces"),
    "lb": ("lb", "lbs", "pound", "pounds"),
    "s": ("s", "sec", "secs", "second", "seconds", "sek", "sekunde", "sekunden", "segundo", "segundos"),
    "min": ("min", "mins", "minute", "minutes", "minuto", "minutos", "minuten"),
    "h": ("h", "hr", "hrs", "hour", "hours", "stunde", "stunden", "hora", "horas"),
    "day": ("day", "tag", "dia"),
    "days": ("days", "tage", "tagen", "dias"),
    "cm": ("cm", "centimeter", "centimeters", "centimetre", "centimetres", "zentimeter", "centímetro", "centímetros"),
    "mm": ("mm", "millimeter", "millimeters", "millimetre", "millimetres", "millímetro", "milímetro", "milímetros"),
    "°C": ("°c", "° c"),
    "°F": ("°f", "° f"),
    "%": ("%",),
}
_METRIC_LABELS = {("g", 1): "g", ("g", 1000): "kg", ("ml", 1): "mL", ("ml", 1000): "L"}
_ENGLISH_UNITS = {alias: label for label, aliases in _UNIT_LABELS.items() for alias in aliases}
_ENGLISH_UNITS.update({
    unit: _METRIC_LABELS[base] for unit in known_units()
    if (base := normalized_amount("1", unit)) in _METRIC_LABELS
})
_UNIT_PATTERN = "|".join(re.escape(unit) for unit in sorted(_ENGLISH_UNITS, key=lambda unit: (-len(unit), unit)))
_SCALABLE_UNIT_PATTERN = "|".join(re.escape(unit) for unit in sorted(
    set(known_units()) & _ENGLISH_UNITS.keys(), key=lambda unit: (-len(unit), unit),
))
_NUMERIC_TEXT = re.compile(
    rf"(?:(?P<ordinal>[0-9]+)\.(?=\s+(?:tags?|tage|tagen)\b)|"
    rf"(?P<number>(?:(?<!\w)[-−])?(?:{NUMBER_PATTERN}|\d+))|"
    rf"(?P<article>\ban?)(?=\s+(?:{_SCALABLE_UNIT_PATTERN})(?!\w)))"
    rf"(?:\s*(?P<unit>{_UNIT_PATTERN})(?!\w))?",
    re.IGNORECASE,
)
_NUMBERS_ONLY = re.compile(rf"(?P<number>(?:(?<!\w)[-−])?(?:{NUMBER_PATTERN}|\d+))")
_PLACEHOLDER = re.compile(r"\[MEASURE_[A-Z]+\]")


def _english_unit(unit: str) -> str | None:
    return _ENGLISH_UNITS.get(unit.strip().casefold().rstrip("."))


def _english_ordinal(number: str) -> str:
    value = int(number)
    suffix = "th" if 11 <= value % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return number + suffix


def _measure_kind(unit: str | None, label: str | None) -> str:
    if label is None:
        return "number"
    metric = normalized_amount("1", unit)
    if metric in _METRIC_LABELS:
        return metric[0]
    return step_unit("1", label)


def _recipe_fields(recipe: dict) -> dict:
    fields = {f"/{name}": recipe[name] for name in ("title", "servings")}
    for collection, names in REVIEW_FIELDS.items():
        fields.update({
            f"/{collection}/{index}/{name}": item[name]
            for index, item in enumerate(recipe[collection])
            for name in names
        })
    return fields


def _protected_measures(text: str, language: str) -> tuple[str, dict[str, str], tuple[str, ...]]:
    measures = {}
    measure_kinds = []
    reserved = set(_PLACEHOLDER.findall(text))
    pattern = _NUMERIC_TEXT if language == "english" else _NUMBERS_ONLY

    def mask(match: re.Match) -> str:
        index = len(measures)
        while True:
            letter = chr(ord("A") + index % 26)
            token = f"[MEASURE_{letter * (1 + index // 26)}]"
            if token not in reserved and token not in measures:
                break
            index += 1
        measures[token] = match[0]
        if language == "english":
            unit = match["unit"]
            label = _english_unit(unit) if unit else None
            if match["ordinal"]:
                number = _english_ordinal(match["ordinal"])
            else:
                number = match["number"] or "1"
            separator = " " if label and label not in {"°C", "°F", "%"} else ""
            measures[token] = number + separator + (step_unit(number, label) if label else "")
            kind = _measure_kind(unit, label)
            measure_kinds.append("ordinal " + kind if match["ordinal"] else kind)
        return token

    masked = pattern.sub(mask, text)
    return masked, measures, tuple(measure_kinds)


def _validate_translation_semantics(source: str, target: str) -> None:
    if not preserves_constraints(source, target):
        raise TranslationError("The translation changed a negation or a recipe limit.")


def translate_output(
    openai: OpenAIClient | None, recipe: dict, texts: dict[str, str], *,
    language: str = "english", source_recipe: dict | None = None,
    templates: dict[tuple[str, str, tuple[str, ...]], str] | None = None,
    variant: dict | None = None, prefetch_texts: tuple[str, ...] = (), cache_only: bool = False,
) -> dict:
    """Translate display values, or render them with previously validated templates."""
    if language not in ("english", "original"):
        raise TranslationError("Choose English or the original recipe language.")
    try:
        translated = deepcopy(recipe)
        fields = _recipe_fields(translated) if language == "english" else {}
        note_paths = {f"/display/{index}": key for index, key in enumerate(texts)}
        notes = {path: {"value": texts[key], "status": "derived"} for path, key in note_paths.items()}
        additional_recipes = []
        if language == "english" and not cache_only:
            if source_recipe is not None and source_recipe != recipe:
                additional_recipes.append(("/source", _recipe_fields(deepcopy(source_recipe))))
            if variant is not None:
                additional_recipes.append(("/variant", _recipe_fields(deepcopy(variant))))
        extra_notes = {
            f"/prefetch/{index}": {"value": text, "status": "derived"}
            for index, text in enumerate(prefetch_texts)
        }
        requested_fields = {**fields, **notes}
        entries = []
        pending = {}
        cached = templates if templates is not None else {}
        translations = {}
        work = [
            (path, path, field) for path, field in fields.items()
        ] + [
            (f"{prefix}{path}", path, field)
            for prefix, entries_by_path in additional_recipes for path, field in entries_by_path.items()
        ] + [
            (path, "/display", field) for path, field in {**notes, **extra_notes}.items()
        ]
        for path, scope, field in work:
            value = field["value"]
            if value is None or field["status"] == "needs_review":
                continue
            unit = _english_unit(value) if scope.endswith("/unit") else None
            if unit is not None:
                field["value"] = unit
                continue
            if not any(char.isalpha() for char in value):
                continue
            text, measures, measure_kinds = _protected_measures(value, language)
            key = (scope, text, measure_kinds)
            if path in requested_fields:
                translations[path] = (key, measures)
            if not cache_only and key not in cached and key not in pending:
                pending[key] = path
                entries.append({"path": path, "text": text})
    except (KeyError, TypeError, AttributeError):
        raise TranslationError("The recipe cannot be prepared for translation.") from None

    required = {key for key, _ in translations.values() if key in pending}
    fresh = _translate_missing(openai, entries, pending, required, language, source_recipe or recipe) if entries else {}
    for path, (key, measures) in translations.items():
        text = fresh.get(key) or cached.get(key)
        if text is None:
            if path in notes:
                continue
            raise TranslationError("The English translation for the current recipe is not ready.")
        requested_fields[path]["value"] = _PLACEHOLDER.sub(lambda match: measures.get(match.group(), match.group()), text).strip()
    if fresh:
        cached.update(fresh)
    return {"recipe": translated, "texts": {key: notes[path]["value"] for path, key in note_paths.items()}}


def _translate_missing(openai, entries, pending, required, language, source_recipe):
    paths = {path: key for key, path in pending.items()}
    prompt = (PROMPTS_DIR / "translate_recipe.system.txt").read_text(encoding="utf-8")
    schema = json.loads((PROMPTS_DIR / "translate_recipe.schema.json").read_text(encoding="utf-8"))
    schema["properties"]["translations"]["items"]["properties"]["path"]["enum"] = list(paths)
    data = {"fields": entries, "language": language}
    if language == "original":
        try:
            source_fields = _recipe_fields(source_recipe)
            source_text = {path: field["value"][:350] for path, field in source_fields.items()
                           if isinstance(field["value"], str)}
            data["source_examples"] = (
                ([source_text["/title"]] if "/title" in source_text else [])
                + [text for path, text in source_text.items() if path.endswith("/name")][:5]
                + [text for path, text in source_text.items() if path.endswith("/instruction")][:6]
            )
        except (KeyError, TypeError, AttributeError):
            raise TranslationError("The original language could not be determined.") from None
        if not data["source_examples"]:
            raise TranslationError("The original language could not be determined.")
    try:
        response = _structured_response(
            openai, prompt, schema, "recipe_translation", data, max_output_tokens=8192,
        )
    except json.JSONDecodeError:
        raise TranslationError("The translation response was invalid.") from None

    translations = response.get("translations") if isinstance(response, dict) else None
    if not isinstance(translations, list):
        raise TranslationError("The translation response was invalid.")
    translated = {}
    seen = set()
    for entry in translations:
        path = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(path, str) or path not in paths or path in seen:
            raise TranslationError("The translation did not match the recipe fields.")
        seen.add(path)
        text = entry.get("text")
        key = paths[path]
        source = key[1]
        try:
            if not isinstance(text, str) or not text.strip():
                raise TranslationError("The translation could not preserve every recipe field.")
            if (_PLACEHOLDER.findall(text) != _PLACEHOLDER.findall(source)
                    or any(char.isnumeric() for char in _PLACEHOLDER.sub("", text))):
                raise TranslationError("The translation changed a protected recipe measure.")
            _validate_translation_semantics(source, text)
        except TranslationError:
            if key in required:
                raise
            continue
        translated[key] = text
    if not required <= translated.keys():
        raise TranslationError("The translation omitted one or more recipe fields.")
    return translated


def translate_recipe(openai: OpenAIClient, recipe: dict) -> dict:
    """Translate only the final recipe display values."""
    return translate_output(openai, recipe, {})["recipe"]
