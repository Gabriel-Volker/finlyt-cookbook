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
from .scaling_plan import (
    compare_scaling_outcomes,
    ingredient_scale_factor,
    proportional_source_conflict,
    scaling_plan_matches,
    validate_scaling_plan,
    verified_scaling_rules,
)
from .vegan import veganize


SCALE_STEP_REASON = "Apply each ingredient's scaling rule to matched measures in this step."
SCALE_SERVINGS_REASON = "Use the requested serving count."
SCALE_UNIT_REASON = "Convert within the same metric dimension."
SCALE_BATCH_WARNING = "Batches required: {count}. Keep the written amount in the method per batch."
SCALE_CONFLICT_WARNING = "Luna and Gemini calculate different amounts or batches for these servings. Check the source amount and method."
SCALE_UNRESOLVED_WARNING = "Resolve the source measure before scaling this ingredient."
SCALE_BASELINE_WARNING = "The original serving count differs from the documented single-batch yield. Check the batch amount."
SCALE_UNSUPPORTED_WARNING = "The source measure cannot be scaled numerically."
SCALE_FRACTIONAL_WARNING = "The scaled count is fractional. Check how to divide this ingredient."
STEP_UNRECONCILED_WARNING = "The linked step amounts do not reconcile with the ingredient total."
STEP_ALTERNATIVE_WARNING = "Alternative measures in this step need manual scaling."
STEP_MISMATCH_WARNING = "A step measure differs from its linked ingredient quantity."
STEP_OVERLAP_WARNING = "Overlapping measures in this step need manual scaling."
STEP_UNSAFE_WARNING = "Check the linked ingredient amounts. At least one could not be scaled safely."
STEP_AMBIGUOUS_WARNING = "A step measure could belong to more than one linked ingredient."
STEP_BATCH_WARNING = "Check this step's amounts for each batch. The ingredient list shows totals."
STEP_YIELD_WARNING = "Check the number of portions named in this step against the recipe yield."
SCALING_DISPLAY_TEXTS = (
    SCALE_STEP_REASON, SCALE_SERVINGS_REASON, SCALE_UNIT_REASON, SCALE_BATCH_WARNING.format(count=2),
    SCALE_CONFLICT_WARNING, SCALE_UNRESOLVED_WARNING, SCALE_BASELINE_WARNING, SCALE_UNSUPPORTED_WARNING,
    SCALE_FRACTIONAL_WARNING, STEP_UNRECONCILED_WARNING, STEP_ALTERNATIVE_WARNING, STEP_MISMATCH_WARNING,
    STEP_OVERLAP_WARNING, STEP_UNSAFE_WARNING, STEP_AMBIGUOUS_WARNING, STEP_BATCH_WARNING, STEP_YIELD_WARNING,
)


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
WRAPPED_WORD = re.compile(r"(?<=[^\W\d_])[-\u00ad][ \t]*\r?\n[ \t]*(?=[^\W\d_])")
ALTERNATE_AMOUNT = re.compile(r"\d\s+(?:or|oder|ou|to|bis|a|até)\s+$", re.IGNORECASE)
REMAINING_AMOUNT = r"(?:(?:the\s+)?remaining\s+|(?:die\s+|den\s+|das\s+)?restlich\w*\s+)"
MEASURE_CONNECTOR = r"(?:(?:of(?:\s+the)?|de|da|das|do|dos)\s+)?"
WRITTEN_AMOUNT = (
    r"(?:half(?:\s+(?:of\s+)?an?)?|an|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"um|uma|dois|duas|três|quatro|cinco|meio|meia|ein|eine|einen|zwei|drei|halb|halbe)"
)
UNRECOGNIZED_MODIFIER = (
    r"(?:(?!(?:and|or|then|with|e|ou|com|und|oder|mit)\b)[^\W\d_]+\s+){0,2}"
)


def _portion_count(field: dict) -> int | None:
    if field["status"] not in {"stated", "reviewed"} or field["value"] is None:
        return None
    match = YIELD.fullmatch(field["value"].strip().rstrip(".:"))
    value = parse_number(match[1]) if match else None
    return int(value) if value and value.denominator == 1 else None


def _phrase_pattern(text: str) -> str:
    text = WRAPPED_WORD.sub("", text)
    return r"\s+".join(re.escape(word) for word in text.split())


def _ingredient_measure_patterns(ingredient: dict, *, review_only: bool = False) -> tuple[re.Pattern, ...]:
    name = ingredient["name"]["value"]
    if not name:
        return ()
    name_pattern = _phrase_pattern(name)
    unit = ingredient["unit"]["value"]
    amount = QUANTITY_PATTERN
    if review_only:
        # Bare “a” is also a Portuguese article, as in “a canela”.
        amount = rf"(?:{amount}|a|{WRITTEN_AMOUNT})" if unit else WRITTEN_AMOUNT
    elif unit:
        amount = rf"(?:{amount}|an?)"
    measure = rf"(?P<amount>{amount})"
    if unit:
        units = sorted(set((*unit_aliases(unit), *known_units())), key=lambda alias: (-len(alias), alias))
        unit_pattern = "|".join(_phrase_pattern(alias) for alias in units)
        measure += rf"(?P<gap>\s*)(?P<unit>{unit_pattern})(?!\w)"
    prep = ingredient["preparation"]["value"]
    modifier = rf"(?:{_phrase_pattern(prep)}\s+)?" if prep else ""
    if review_only and unit:
        modifier += UNRECOGNIZED_MODIFIER
    return (
        re.compile(
            rf"{QUANTITY_START}{measure}\s+{MEASURE_CONNECTOR}(?:{REMAINING_AMOUNT})?"
            rf"{modifier}{name_pattern}(?!\w)",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?<!\w){name_pattern}\s*\(\s*{measure}\s*\)",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?<!\w){name_pattern}\s*:\s*{measure}(?=\s*(?:[.;!?)]|$))",
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


@dataclass(frozen=True)
class _ScaledIngredient:
    quantity: str
    unit: str | None
    scaled_quantity: str
    scaled_unit: str | None
    factor: Fraction
    patterns: tuple[re.Pattern, ...]
    review_patterns: tuple[re.Pattern, ...]


def _step_text_fields(steps):
    for index, step in enumerate(steps):
        for key in ("instruction", "condition"):
            original = step[key]["value"] or ""
            offset = []
            end = 0
            for gap in WRAPPED_WORD.finditer(original):
                offset.extend(range(end, gap.start()))
                end = gap.end()
            if end:
                offset.extend(range(end, len(original)))
                text = WRAPPED_WORD.sub("", original)
            else:
                text = original
            yield index, key, text, offset, step["ingredient_ids"]


def _step_measures(fields, ingredient_id, patterns, *, include_unlinked=False):
    for index, key, text, offset, linked_ids in fields:
        if not include_unlinked and ingredient_id not in linked_ids:
            continue
        for pattern in patterns:
            for match in pattern.finditer(text):
                groups = match.groupdict()
                unit = groups.get("unit")
                start, end = match.start("amount"), match.end("unit" if unit else "amount")
                alternative = bool(ALTERNATE_AMOUNT.search(text[:start]))
                if offset:
                    start, end = offset[start], offset[end - 1] + 1
                yield _StepMeasure(
                    index, key, start, end,
                    "1" if match["amount"].casefold() in {"a", "an"} and unit else match["amount"],
                    unit, groups.get("gap", ""),
                    alternative,
                )


def _step_amounts_for_scale(mentions, ingredient: _ScaledIngredient, warn):
    instructions = [item for item in mentions if item.field == "instruction"]
    quantity, unit = ingredient.quantity, ingredient.unit
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
            warn(path, STEP_UNRECONCILED_WARNING)
        return

    for item in mentions:
        if item.is_alternative:
            warn(item.path, STEP_ALTERNATIVE_WARNING)
            continue
        if quantity_matches(quantity, unit, item.quantity, item.unit):
            new_quantity, new_unit = ingredient.scaled_quantity, ingredient.scaled_unit
            if item.unit and quantity_matches("1", item.unit, "1", new_unit):
                new_unit = item.unit
        elif reconciled and (
            item.field == "instruction"
            or any(
                match.step == item.step
                and quantity_matches(match.quantity, match.unit, item.quantity, item.unit)
                for match in instructions
            )
        ):
            new_quantity, new_unit = scale_quantity(item.quantity, item.unit, ingredient.factor)
        else:
            warn(item.path, STEP_MISMATCH_WARNING)
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
            warn(STEP_OVERLAP_WARNING)
            if accepted and start < accepted[-1][1]:
                accepted.pop()
            last_end = max(end, last_end)
            continue
        accepted.append((start, end, next(iter(options))))
        last_end = end
    for start, end, replacement in reversed(accepted):
        text = text[:start] + replacement + text[end:]
    return text


def _reviewed_measure_conflicts(steps, ingredient, measure: _ScaledIngredient) -> list[str]:
    if not any(ingredient[key]["status"] == "reviewed" for key in ("quantity", "unit")):
        return []
    fields = tuple(_step_text_fields(steps))
    matches = list(dict.fromkeys(_step_measures(fields, ingredient["id"], measure.patterns)))
    unresolved = set(_step_measures(fields, ingredient["id"], measure.review_patterns)) - set(matches)
    conflicts = {item.path for item in unresolved}
    list(_step_amounts_for_scale(matches, measure, lambda path, _: conflicts.add(path)))
    return sorted(conflicts)


def _scale_steps(recipe, measures, batch_ingredients, held_ingredients, original_servings, servings,
                 change, warn, reviewed_step_links):
    edits_by_path = {}
    owners = {}
    steps = recipe["steps"]
    fields = tuple(_step_text_fields(steps))
    step_amounts = {
        ingredient_id: list(dict.fromkeys(
            item for item in _step_measures(fields, ingredient_id, measure.patterns, include_unlinked=True)
            if ingredient_id in steps[item.step]["ingredient_ids"]
            or (item.step not in reviewed_step_links
                and quantity_matches(measure.quantity, measure.unit, item.quantity, item.unit))
        ))
        for ingredient_id, measure in measures.items()
    }
    review_amounts = {
        ingredient_id: list(_step_measures(
            fields, ingredient_id, measure.review_patterns,
        ))
        for ingredient_id, measure in measures.items()
    }
    for ingredient_id, amounts in step_amounts.items():
        for item in amounts:
            owners.setdefault((item.path, item.start, item.end), set()).add(ingredient_id)
    held_spans = {}
    for ingredient in held_ingredients:
        for item in _step_measures(
            fields, ingredient["id"], _ingredient_measure_patterns(ingredient), include_unlinked=True,
        ):
            held_spans.setdefault(item.path, []).append((item.start, item.end))

    for ingredient_id, amounts in step_amounts.items():
        unmatched = [
            item for item in review_amounts[ingredient_id]
            if (item.path, item.start, item.end) not in owners
            and (item.start, item.end) not in held_spans.get(item.path, ())
        ]
        if unmatched:
            for path in dict.fromkeys(item.path for item in (*unmatched, *amounts)):
                warn(path, STEP_UNSAFE_WARNING)
            continue
        if any(
            len(owners[item.path, item.start, item.end]) > 1
            or any(item.start < end and start < item.end for start, end in held_spans.get(item.path, ()))
            for item in amounts
        ):
            for path in dict.fromkeys(item.path for item in amounts):
                warn(path, STEP_AMBIGUOUS_WARNING)
            continue
        for item, replacement in _step_amounts_for_scale(amounts, measures[ingredient_id], warn):
            edits_by_path.setdefault(item.path, {}).setdefault((item.start, item.end), set()).add(replacement)

    for index, step in enumerate(recipe["steps"]):
        if batch_ingredients.intersection(step["ingredient_ids"]):
            if any(f"/steps/{index}/{key}" in edits_by_path for key in ("instruction", "condition")):
                warn(f"/steps/{index}/instruction", STEP_BATCH_WARNING)
            continue
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
                    warn(path, STEP_YIELD_WARNING)
            updated = _apply_step_edits(text, edits, lambda reason: warn(path, reason))
            change(path, field, updated, SCALE_STEP_REASON)


def adapt_recipe(
    recipe: dict, *, servings: int | None = None, original_servings: int | None = None,
    vegan: bool = False, vegan_plan: dict | None = None, scaling_plan: dict | None = None,
    reviewed_step_links: set[int] | None = None,
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
        variant = "vegan" if vegan else "original"
        plan = scaling_plan.get(variant) if isinstance(scaling_plan, dict) else None
        rules = []
        if factor != 1 and adapted["ingredients"]:
            if not scaling_plan_matches(adapted, plan):
                raise ValueError("A current scaling plan for this recipe is required. Generate or refresh the plan.")
            rules = validate_scaling_plan(adapted, plan)["rules"]
        scale = {
            "original_servings": source_count,
            "original_servings_source": source,
            "target_servings": servings,
            "factor": str(factor),
            "ingredients": [],
        }
        gemini_rules = {}
        if rules or scaling_plan_matches(adapted, plan):
            scale["verification"], gemini_rules = verified_scaling_rules(adapted, plan)
        if factor != 1 or recipe_count is None:
            change("/servings", adapted["servings"], str(servings), SCALE_SERVINGS_REASON)
        measures = {}
        batch_ingredients = set()
        held_ingredients = []
        if factor != 1:
            for index, (ingredient, rule) in enumerate(zip(adapted["ingredients"], rules)):
                path = f"/ingredients/{index}"
                quantity_field, unit_field = ingredient["quantity"], ingredient["unit"]
                quantity, unit = quantity_field["value"], unit_field["value"]
                multiplier = ingredient_scale_factor(rule, source_count, servings)
                applied = {**rule, "multiplier": str(multiplier) if multiplier is not None else None}
                scale["ingredients"].append(applied)
                gemini_rule = gemini_rules.get(ingredient["id"])
                if gemini_rule is not None:
                    comparison = compare_scaling_outcomes(ingredient, rule, gemini_rule, source_count, servings)
                    applied["verification"] = comparison
                    if comparison["materially_different"]:
                        scale["verification"]["material_conflicts"].append(ingredient["id"])
                        reason = SCALE_CONFLICT_WARNING
                        if rule["mode"] == "review":
                            reason = f'{reason} Luna requested review: {rule["reason"]}'
                        applied.update(
                            mode="review", multiplier=None, batch_servings=None, reason=reason,
                        )
                        held_ingredients.append(ingredient)
                        if any(outcome["batches"] and outcome["batches"] > 1 for outcome in (
                            comparison["luna_outcome"], comparison["gemini_outcome"],
                        )):
                            batch_ingredients.add(ingredient["id"])
                        warn(f"{path}/quantity", reason)
                        continue
                if any(ingredient[key]["status"] == "needs_review" for key in ("name", "quantity", "unit")):
                    held_ingredients.append(ingredient)
                    warn(path, SCALE_UNRESOLVED_WARNING)
                    continue
                if multiplier is None:
                    reason = rule["reason"] if rule["mode"] == "review" else SCALE_BASELINE_WARNING
                    applied["reason"] = reason
                    held_ingredients.append(ingredient)
                    warn(f"{path}/quantity", reason)
                    continue
                reason = proportional_source_conflict(adapted, index, ingredient) if rule["mode"] == "proportional" else None
                if reason:
                    applied.update(mode="review", multiplier=None, reason=reason)
                    held_ingredients.append(ingredient)
                    warn(f"{path}/quantity", reason)
                    continue
                if rule["mode"] == "per_batch":
                    # The ingredient list needs a total. The method still uses the original amount per batch.
                    applied.update(batches=int(multiplier), per_batch_quantity=quantity, per_batch_unit=unit)
                    batch_ingredients.add(ingredient["id"])
                    warn(path, SCALE_BATCH_WARNING.format(count=int(multiplier)))
                if multiplier == 1:
                    held_ingredients.append(ingredient)
                    continue
                adjusted = scale_quantity(quantity, unit, multiplier)
                if adjusted is None:
                    warn(f"{path}/quantity", SCALE_UNSUPPORTED_WARNING)
                    continue
                scaled, scaled_unit = adjusted
                if rule["mode"] != "per_batch":
                    measure = _ScaledIngredient(
                        quantity, unit, scaled, scaled_unit, multiplier,
                        _ingredient_measure_patterns(ingredient),
                        _ingredient_measure_patterns(ingredient, review_only=True),
                    )
                    conflicts = _reviewed_measure_conflicts(adapted["steps"], ingredient, measure)
                    if conflicts:
                        reason = "The reviewed amount does not match the linked preparation. Check the step before scaling."
                        applied.update(mode="review", multiplier=None, reason=reason)
                        held_ingredients.append(ingredient)
                        for step_path in conflicts:
                            warn(step_path, reason)
                        continue
                    measures[ingredient["id"]] = measure
                change(f"{path}/quantity", quantity_field, scaled, rule["reason"])
                change(f"{path}/unit", unit_field, scaled_unit, SCALE_UNIT_REASON)
                count = parse_number(scaled) if unit is None else None
                if count is not None and count.denominator != 1:
                    warn(f"{path}/quantity", SCALE_FRACTIONAL_WARNING)
            _scale_steps(adapted, measures, batch_ingredients, held_ingredients,
                         source_count, servings, change, warn, reviewed_step_links or set())
            review_vessel_capacity(adapted, factor, warn)

    return {
        "recipe": adapted,
        "scale": scale,
        "vegan_requested": vegan,
        "status": "needs_review" if review_items or has_unresolved_fields(adapted) else "applied",
        "changes": changes,
        "review_items": review_items,
    }
