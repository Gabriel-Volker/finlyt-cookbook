"""Apply auditable recipe changes to a copy of the extracted fields."""

import re
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction

from .capacity import review_vessel_capacity
from .extraction import has_unresolved_fields
from .quantities import (
    NUMBER_PATTERN,
    QUANTITY_PATTERN,
    known_units,
    normalized_amount,
    parse_number,
    quantity_matches,
    scale_quantity,
    step_unit,
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
ALTERNATE_AMOUNT = re.compile(r"\d\s+(?:or|oder|ou|to|bis|a|até)\s+$", re.IGNORECASE)
REMAINING_AMOUNT = r"(?:(?:the\s+)?remaining\s+|(?:die\s+|den\s+|das\s+)?restlich\w*\s+)"
SALT_NAMES = re.compile(
    r"(?:(?:table|sea|kosher) salt|salt|sal(?: marinho| grosso| fino)?|"
    r"(?:speise|meer|koch|tafel)salz|salz)", re.IGNORECASE
)
PEPPER_NAMES = re.compile(
    r"(?:pepper|(?:(?:ground|cracked) )?(?:black|white) pepper|"
    r"pimenta(?:[- ]do[- ]reino| preta| branca)|pfeffer|"
    r"(?:(?:schwarz|weiß)er|gemahlener) pfeffer)", re.IGNORECASE
)
OIL_WORD = re.compile(r"\b(?:oil|[óo]leo|azeite|öl|[\w]+öl)\b", re.IGNORECASE)
OIL_PURPOSE = (
    r"(?:for (?:deep[- ]?)?frying|for greasing|to (?:grease|coat) the pan|"
    r"para fritar|para untar|zum (?:frittieren|braten|einfetten))"
)


def _portion_count(field: dict) -> int | None:
    if field["status"] not in {"stated", "reviewed"} or field["value"] is None:
        return None
    match = YIELD.fullmatch(field["value"].strip().rstrip(".:"))
    value = parse_number(match[1]) if match else None
    return int(value) if value and value.denominator == 1 else None


def _phrase_pattern(text: str) -> str:
    return r"\s+".join(re.escape(word) for word in text.split())


def _manual_scale_reason(ingredient: dict, steps: list[dict]) -> str | None:
    name = ingredient["name"]["value"] or ""
    normalized_name = " ".join(name.split())
    unit = ingredient["unit"]["value"]
    if SALT_NAMES.fullmatch(normalized_name) or (
        PEPPER_NAMES.fullmatch(normalized_name)
        and (unit or normalized_name.casefold() != "pepper")
    ):
        return "Check the seasoning by taste before changing its original amount."
    if not OIL_WORD.search(normalized_name):
        return None

    preparation = ingredient["preparation"]["value"] or ""
    if re.search(OIL_PURPOSE, f"{normalized_name} {preparation}", re.IGNORECASE):
        return "Check how much oil is needed for the pan or frying vessel."

    named_oil = _phrase_pattern(normalized_name)
    units = "|".join(_phrase_pattern(alias) for alias in unit_aliases(unit)) if unit else ""
    measure = rf"(?:{QUANTITY_PATTERN}\s*(?:{units})\s+(?:of\s+|de\s+)?)?" if units else ""
    oil_reference = rf"{measure}{named_oil}(?!\w)"
    linked_oil = re.compile(
        rf"(?<!\w){named_oil}(?!\w)\s*[,(-]?\s*{OIL_PURPOSE}\b|"
        rf"\b(?:fry[^.!?;,]{{0,80}}? in|fritar[^.!?;,]{{0,80}}? em|frite[^.!?;,]{{0,80}}? em)\s+{oil_reference}|"
        rf"\b(?:grease|coat) (?:the )?pan with\s+{oil_reference}|"
        rf"\bin\s+{oil_reference}\s+(?:braten|frittieren)\b",
        re.IGNORECASE,
    )
    # Quotes may contain instructions which were extracted into a separate step.
    quotes = [(field.get("evidence") or {}).get("quote") or "" for field in (
        ingredient["name"], ingredient["quantity"], ingredient["unit"], ingredient["preparation"]
    )]
    instructions = [
        step[key]["value"] or ""
        for step in steps
        if ingredient["id"] in step["ingredient_ids"]
        for key in ("instruction", "condition")
    ]
    if any(linked_oil.search(text) for text in (*quotes, *instructions)):
        return "Check how much oil is needed for the pan or frying vessel."
    return None


def _ingredient_measure_patterns(ingredient: dict) -> tuple[re.Pattern, ...]:
    name = ingredient["name"]["value"]
    if not name:
        return ()
    name_pattern = _phrase_pattern(name)
    unit = ingredient["unit"]["value"]
    measure = rf"(?P<amount>{QUANTITY_PATTERN})"
    if unit:
        units = sorted(set((*unit_aliases(unit), *known_units())), key=lambda alias: (-len(alias), alias))
        unit_pattern = "|".join(_phrase_pattern(alias) for alias in units)
        measure += rf"(?P<gap>\s*)(?P<unit>{unit_pattern})(?!\w)"
    prep = ingredient["preparation"]["value"]
    modifier = rf"(?:{_phrase_pattern(prep)}\s+)?" if prep else ""
    return (
        re.compile(
            rf"{QUANTITY_START}{measure}\s+(?:of\s+|de\s+)?(?:{REMAINING_AMOUNT})?"
            rf"{modifier}{name_pattern}(?!\w)",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?<!\w){name_pattern}\s*\(\s*{measure}\s*\)",
            re.IGNORECASE,
        ),
    )


@dataclass(frozen=True)
class _StepMeasure:
    step: int
    field: str
    start: int
    end: int
    quantity: str
    unit: str | None
    gap: str
    is_alternative: bool

    @property
    def path(self) -> str:
        return f"/steps/{self.step}/{self.field}"


def _step_measures(steps, ingredient_id, patterns):
    for index, step in enumerate(steps):
        if ingredient_id not in step["ingredient_ids"]:
            continue
        for key in ("instruction", "condition"):
            text = step[key]["value"] or ""
            for pattern in patterns:
                for match in pattern.finditer(text):
                    groups = match.groupdict()
                    unit = groups.get("unit")
                    start, end = match.start("amount"), match.end("unit" if unit else "amount")
                    yield _StepMeasure(
                        index, key, start, end, match["amount"], unit, groups.get("gap", ""),
                        bool(ALTERNATE_AMOUNT.search(text[:start])),
                    )


def _step_amounts_for_scale(mentions, quantity, unit, scaled, scaled_unit, factor, warn):
    instructions = [item for item in mentions if item.field == "instruction"]
    total = normalized_amount(quantity, unit)
    parts = [normalized_amount(item.quantity, item.unit) for item in instructions]
    reconciled = (
        len(parts) >= 2
        and total is not None
        and all(part is not None and part[0] == total[0] and part[1] > 0 for part in parts)
        and sum(part[1] for part in parts) == total[1]
    )
    if any(item.is_alternative for item in instructions) or (
        instructions and not reconciled and (
            len(instructions) > 1
            or not quantity_matches(quantity, unit, instructions[0].quantity, instructions[0].unit)
        )
    ):
        for path in dict.fromkeys(item.path for item in mentions):
            warn(path, "The linked step amounts do not reconcile with the ingredient total.")
        return

    for item in mentions:
        if item.is_alternative:
            warn(item.path, "Alternative measures in this step need manual scaling.")
            continue
        if quantity_matches(quantity, unit, item.quantity, item.unit):
            new_quantity, new_unit = scaled, scaled_unit
        elif reconciled and (
            item.field == "instruction"
            or any(
                match.step == item.step
                and quantity_matches(match.quantity, match.unit, item.quantity, item.unit)
                for match in instructions
            )
        ):
            new_quantity, new_unit = scale_quantity(item.quantity, item.unit, factor)
        else:
            warn(item.path, "A step measure differs from its linked ingredient quantity.")
            continue
        replacement = new_quantity
        if new_unit:
            replacement += item.gap + step_unit(new_quantity, new_unit)
        yield item, replacement


def _apply_step_edits(text, edits, warn):
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
    for start, end, replacement in reversed(accepted):
        text = text[:start] + replacement + text[end:]
    return text


def _scale_steps(recipe, measures, original_servings, servings, factor, change, warn):
    edits_by_path = {}
    owners = {}
    step_amounts = {
        ingredient_id: list(dict.fromkeys(_step_measures(recipe["steps"], ingredient_id, measure[-1])))
        for ingredient_id, measure in measures.items()
    }
    for ingredient_id, amounts in step_amounts.items():
        for item in amounts:
            owners.setdefault((item.path, item.start, item.end), set()).add(ingredient_id)

    for ingredient_id, amounts in step_amounts.items():
        if any(len(owners[item.path, item.start, item.end]) > 1 for item in amounts):
            for path in dict.fromkeys(item.path for item in amounts):
                warn(path, "A step measure could belong to more than one linked ingredient.")
            continue
        quantity, unit, scaled, scaled_unit, _ = measures[ingredient_id]
        for item, replacement in _step_amounts_for_scale(
            amounts, quantity, unit, scaled, scaled_unit, factor, warn
        ):
            edits_by_path.setdefault(item.path, {}).setdefault((item.start, item.end), set()).add(replacement)

    for index, step in enumerate(recipe["steps"]):
        for key in ("instruction", "condition"):
            field = step[key]
            text = field["value"]
            if text is None:
                continue
            path = f"/steps/{index}/{key}"
            edits = edits_by_path.get(path, {})
            for match in YIELD_INSTRUCTION.finditer(text):
                if parse_number(match[2]) == original_servings:
                    edits.setdefault(match.span(2), set()).add(str(servings))
                else:
                    warn(path, "Check the number of portions named in this step against the recipe yield.")
            updated = _apply_step_edits(text, edits, lambda reason: warn(path, reason))
            change(path, field, updated, "Apply the serving factor to measures identified in this step.")


def adapt_recipe(
    recipe: dict, *, servings: int | None = None, original_servings: int | None = None,
    vegan: bool = False, vegan_plan: dict | None = None,
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

    if vegan:
        veganize(adapted, vegan_plan, change, warn)

    scale = None
    if servings is not None:
        yield_field = recipe["servings"]
        recipe_count = _portion_count(yield_field)
        if recipe_count is not None and original_servings not in (None, recipe_count):
            raise ValueError("--original-servings conflicts with the serving count in the recipe.")
        source_count = recipe_count or original_servings
        if source_count is None:
            raise ValueError("The recipe has no confirmed serving count. Supply --original-servings.")
        source = "user" if recipe_count is None else "recipe"
        if recipe_count is not None and yield_field["status"] == "reviewed":
            source = "review"
        factor = Fraction(servings, source_count)
        scale = {
            "original_servings": source_count,
            "original_servings_source": source,
            "target_servings": servings,
            "factor": str(factor),
        }
        if factor != 1 or recipe_count is None:
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
                review_reason = _manual_scale_reason(ingredient, adapted["steps"])
                if review_reason:
                    warn(f"{path}/quantity", review_reason)
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
            _scale_steps(adapted, measures, source_count, servings, factor, change, warn)
            review_vessel_capacity(adapted, factor, warn)

    return {
        "recipe": adapted,
        "scale": scale,
        "vegan_requested": vegan,
        "status": "needs_review" if review_items or has_unresolved_fields(adapted) else "applied",
        "changes": changes,
        "review_items": review_items,
    }
