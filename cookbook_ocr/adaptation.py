"""Apply auditable recipe changes to a copy of the extracted fields."""

import re
from copy import deepcopy
from fractions import Fraction

from .extraction import has_unresolved_fields
from .quantities import (
    NUMBER_PATTERN,
    QUANTITY_PATTERN,
    parse_number,
    quantity_matches,
    scale_quantity,
    unit_aliases,
)
from .vegan import veganize


YIELD_UNIT = r"(?:servings?|people|persons?|portions?|porç(?:ão|ões)|pessoas?|portionen?|personen?)"
YIELD = re.compile(
    rf"(?:(?:serves|makes|yield|für|rende|para)\s+)?({NUMBER_PATTERN})(?:\s+{YIELD_UNIT})?",
    re.IGNORECASE,
)
YIELD_INSTRUCTION = re.compile(
    rf"\b((?:divide into|split into|divida em|teile in|in)\s+)({NUMBER_PATTERN})"
    r"(?=\s+(?:servings?|portions?|porç(?:ão|ões)|portionen?)\b)",
    re.IGNORECASE,
)
QUANTITY_START = r"(?<![\w.,/⁄∕–—−-])(?<![0-9]\s)"


def _portion_count(field: dict) -> int | None:
    if field["status"] != "stated" or field["value"] is None:
        return None
    match = YIELD.fullmatch(field["value"].strip().rstrip(".:"))
    value = parse_number(match[1]) if match else None
    return int(value) if value and value.denominator == 1 else None


def _phrase_pattern(text: str) -> str:
    return r"\s+".join(re.escape(word) for word in text.split())


def _ingredient_measure_patterns(ingredient: dict) -> tuple[re.Pattern, ...]:
    name = ingredient["name"]["value"]
    if not name:
        return ()
    name_pattern = _phrase_pattern(name)
    unit = ingredient["unit"]["value"]
    measure = rf"(?P<amount>{QUANTITY_PATTERN})"
    if unit:
        units = sorted(unit_aliases(unit), key=lambda alias: (-len(alias), alias))
        unit_pattern = "|".join(_phrase_pattern(alias) for alias in units)
        measure += rf"(?P<gap>\s*)(?P<unit>{unit_pattern})(?!\w)"
    prep = ingredient["preparation"]["value"]
    modifier = rf"(?:{_phrase_pattern(prep)}\s+)?" if prep else ""
    return (
        re.compile(
            rf"{QUANTITY_START}{measure}\s+(?:of\s+|de\s+)?{modifier}{name_pattern}(?!\w)",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?<!\w){name_pattern}\s*\(\s*{measure}\s*\)",
            re.IGNORECASE,
        ),
    )


def _scale_step_text(text, measures, original_servings, servings, warn):
    edits = {}
    unmatched = set()
    for quantity, unit, scaled, scaled_unit, patterns in measures:
        for pattern in patterns:
            for match in pattern.finditer(text):
                in_step_unit = match.groupdict().get("unit")
                start, end = match.span("amount")
                if in_step_unit:
                    end = match.end("unit")
                span = (start, end)
                if re.search(r"\d\s+(?:or|oder|ou|to|bis|a|até)\s+$", text[:start], re.I):
                    unmatched.add(span)
                    continue
                if not quantity_matches(quantity, unit, match["amount"], in_step_unit):
                    unmatched.add(span)
                    continue
                replacement = scaled
                if scaled_unit:
                    replacement += match["gap"] + scaled_unit
                edits.setdefault(span, set()).add(replacement)

    for match in YIELD_INSTRUCTION.finditer(text):
        if parse_number(match[2]) == original_servings:
            edits.setdefault(match.span(2), set()).add(str(servings))
        else:
            warn("Check the number of portions named in this step against the recipe yield.")

    accepted = []
    last_end = 0
    for (start, end), options in sorted(edits.items()):
        if len(options) != 1 or start < last_end:
            warn("Overlapping measures in this step need manual scaling.")
            if accepted and start < accepted[-1][1]:
                accepted.pop()
            last_end = max(end, last_end)
            continue
        accepted.append((start, end, next(iter(options))))
        last_end = end
    if unmatched - edits.keys():
        warn("A step measure differs from its linked ingredient quantity.")
    for start, end, replacement in reversed(accepted):
        text = text[:start] + replacement + text[end:]
    return text


def _scale_steps(recipe, measures, original_servings, servings, change, warn):
    for index, step in enumerate(recipe["steps"]):
        linked = [measures[key] for key in step["ingredient_ids"] if key in measures]
        for key in ("instruction", "condition"):
            field = step[key]
            if field["value"] is None:
                continue
            path = f"/steps/{index}/{key}"
            updated = _scale_step_text(
                field["value"], linked, original_servings, servings, lambda reason: warn(path, reason)
            )
            change(path, field, updated, "Apply the serving factor to measures identified in this step.")


def adapt_recipe(
    recipe: dict, *, servings: int | None = None, original_servings: int | None = None,
    vegan: bool = False,
) -> dict:
    """Return the adapted recipe and explain the changes without mutating the extraction."""
    for count in (servings, original_servings):
        if count is not None and (type(count) is not int or count <= 0):
            raise ValueError("Serving counts must be positive integers.")
    if original_servings is not None and servings is None:
        raise ValueError("--original-servings requires --servings.")

    adapted = deepcopy(recipe)
    changes = []
    review_items = []

    def change(path, field, value, reason):
        if value != field["value"]:
            changes.append({"path": path, "before": field["value"], "after": value, "reason": reason})
            field.update(value=value, status="derived", evidence=None)

    def warn(path, reason):
        item = {"path": path, "reason": reason}
        if item not in review_items:
            review_items.append(item)

    scale = None
    if servings is not None:
        stated_count = _portion_count(recipe["servings"])
        if stated_count is not None and original_servings not in (None, stated_count):
            raise ValueError("--original-servings conflicts with the serving count stated in the recipe.")
        source_count = stated_count or original_servings
        if source_count is None:
            raise ValueError("The recipe has no confirmed serving count. Supply --original-servings.")
        factor = Fraction(servings, source_count)
        scale = {
            "original_servings": source_count,
            "original_servings_source": "recipe" if stated_count is not None else "user",
            "target_servings": servings,
            "factor": str(factor),
        }
        if factor != 1 or stated_count is None:
            change("/servings", adapted["servings"], str(servings), "Use the requested serving count.")
        measures = {}
        if factor != 1:
            for index, ingredient in enumerate(adapted["ingredients"]):
                path = f"/ingredients/{index}"
                quantity_field, unit_field = ingredient["quantity"], ingredient["unit"]
                quantity, unit = quantity_field["value"], unit_field["value"]
                if any(field["status"] == "needs_review" for field in (
                    ingredient["name"], quantity_field, unit_field
                )):
                    warn(path, "Resolve the source measure before scaling this ingredient.")
                    continue
                if quantity is None:
                    continue
                adjusted = scale_quantity(quantity, unit, factor)
                if adjusted is None:
                    warn(f"{path}/quantity", "The source measure cannot be scaled numerically.")
                    continue
                scaled, scaled_unit = adjusted
                measures[ingredient["id"]] = (
                    quantity, unit, scaled, scaled_unit, _ingredient_measure_patterns(ingredient)
                )
                change(f"{path}/quantity", quantity_field, scaled, "Multiply by the serving factor.")
                change(f"{path}/unit", unit_field, scaled_unit, "Convert within the same metric dimension.")
                count = parse_number(scaled) if unit is None else None
                if count is not None and count.denominator != 1:
                    warn(f"{path}/quantity", "The scaled count is fractional. Check how to divide this ingredient.")
            _scale_steps(adapted, measures, source_count, servings, change, warn)

    if vegan:
        veganize(adapted, change, warn)
    return {
        "recipe": adapted,
        "scale": scale,
        "vegan_requested": vegan,
        "status": "needs_review" if review_items or has_unresolved_fields(adapted) else "applied",
        "changes": changes,
        "review_items": review_items,
    }
