"""Validate and apply a vegan plan tied to a particular recipe."""

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from hashlib import sha256

from .extraction import UNREADABLE
from .quantities import QUANTITY_PATTERN, known_units, normalized_amount, parse_number
from .review import REVIEW_FIELDS, same_recipe_inputs


EDITABLE_FIELDS = {
    "ingredients": {"name", "preparation"},
    "steps": {"instruction", "condition", "duration"},
}
VEGAN_PLAN_VERSION = 2
VEGAN_AMOUNT_WARNING = "Confirm the amount of the vegan replacement."
VEGAN_UNIT_WARNING = "Confirm the unit for the vegan replacement."
PLAN_STATUSES = ("ready", "needs_review", "unavailable")
REMOVABLE_FIELDS = {"preparation", "condition", "duration"}
# Match unambiguous standalone names. Qualifiers such as "vegan butter" or
# "coconut milk" must be evaluated in context by the verifier.
_ANIMAL_NAMES = frozenset({
    "bacon", "beef", "beef drippings", "beef roast", "beef stock", "butter", "chicken",
    "chicken stock", "cow's milk", "cream", "dairy cream", "dairy butter", "egg", "egg white",
    "egg whites", "egg yolk", "egg yolks", "eggs", "fish sauce", "gelatin", "ghee", "ham",
    "honey", "lard", "milk", "pork", "sour cream", "whey",
    "bratenfond", "ei", "eier", "eigelb", "gelatine", "huhnerbruhe", "honig", "kuhmilch",
    "milch", "molke", "rinderbraten", "rinderbruhe", "rindfleisch", "sahne", "schinken", "schmalz",
    "schweinefleisch", "speck",
    "banha", "caldo de carne", "caldo de frango", "carne bovina", "carne de porco", "creme de leite",
    "frango", "gelatina", "leite", "leite condensado", "leite de vaca", "manteiga", "mel", "ovo", "ovos",
    "presunto", "soro de leite", "toucinho",
})
TIME_UNITS = frozenset(
    "s sec secs second seconds sek sekunde sekunden segundo segundos "
    "min mins minute minutes minuten minuto minutos "
    "h hr hrs hour hours stunde stunden hora horas "
    "day days tag tage tagen dia dias week weeks woche wochen semana semanas".split()
)
OTHER_UNITS = frozenset(
    "°c °f ° celsius fahrenheit c f piece pieces stück stücke pedaço pedaços "
    "slice slices scheibe scheiben fatia fatias package packages packet packets "
    "packung packungen pacote pacotes can cans dose dosen lata latas "
    "bunch bunches bund maço maços pinches pinch prise prisen pitada pitadas".split()
)
REPLACEMENT_UNITS = frozenset(unit.rstrip(".") for unit in (*known_units(), *OTHER_UNITS)) - {
    "°c", "°f", "°", "celsius", "fahrenheit", "c", "f",
}


def _fingerprint(recipe: dict) -> str:
    data = json.dumps(recipe, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(data.encode("utf-8")).hexdigest()


def vegan_plan_matches(recipe: dict, plan: dict | None) -> bool:
    """Check whether a stored plan was generated for these source values."""
    return (
        isinstance(plan, dict) and plan.get("protocol_version") == VEGAN_PLAN_VERSION
        and plan.get("status") in PLAN_STATUSES
        and plan.get("recipe_fingerprint") == _fingerprint(recipe)
    )


def proposed_vegan_substitutions(changes: list) -> dict[str, str]:
    """Collect the exact ingredient name changes that need independent approval."""
    replacements = {}
    for edit in changes:
        if not isinstance(edit, dict) or not isinstance(edit.get("path"), str) or "after" not in edit:
            raise ValueError("The vegan plan returned an invalid edit.")
        path = edit["path"]
        if path.startswith("/ingredients/") and path.endswith("/name"):
            if path in replacements or not _text(edit["after"]):
                raise ValueError("The vegan plan returned an invalid ingredient replacement.")
            replacements[path] = edit["after"]
    return replacements


def proposed_vegan_measures(changes: list) -> dict[str, dict]:
    """Collect the exact replacement amounts that need independent approval."""
    measures = {}
    for edit in changes:
        measure = edit.get("replacement_measure")
        if measure is None:
            continue
        path = edit["path"]
        if not re.fullmatch(r"/ingredients/[0-9]+/name", path) or not _valid_replacement_measure(measure):
            raise ValueError("A replacement measure must specify a positive amount, culinary unit and equivalence basis.")
        measures[path] = measure
    return measures


def vegan_approval_entries(changes: list) -> list[dict]:
    replacements = proposed_vegan_substitutions(changes)
    measures = proposed_vegan_measures(changes)
    return [
        {"path": path, "ingredient": name, **({"replacement_measure": measures[path]} if path in measures else {})}
        for path, name in replacements.items()
    ]


def vegan_substitutions_approved(changes: list, approvals) -> bool:
    """Require one exact approval for every proposed ingredient replacement."""
    try:
        expected = {entry["path"]: entry for entry in vegan_approval_entries(changes)}
    except ValueError:
        return False
    if not isinstance(approvals, list) or len(approvals) != len(expected):
        return False
    approved = set()
    for entry in approvals:
        if not isinstance(entry, dict):
            return False
        path = entry.get("path")
        if not isinstance(path, str) or path in approved or path not in expected:
            return False
        if (entry.get("ingredient") != expected[path]["ingredient"]
                or entry.get("replacement_measure") != expected[path].get("replacement_measure")):
            return False
        approved.add(path)
    return True


def vegan_ingredient_names(recipe: dict, changes: list) -> dict[str, str | None]:
    replacements = proposed_vegan_substitutions(changes)
    names = {}
    for index, ingredient in enumerate(recipe["ingredients"]):
        path = f"/ingredients/{index}/name"
        names[path] = replacements.get(path, ingredient["name"]["value"])
    return names


def vegan_animal_review(ingredient_names: dict[str, str | None]) -> list[dict]:
    """Catch clear animal ingredients even if the verifier overlooks them."""
    findings = []
    for path, name in ingredient_names.items():
        if not name:
            continue
        normalized = unicodedata.normalize("NFKD", name.casefold().replace("’", "'"))
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        normalized = re.sub(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)", "", normalized)
        if " ".join(normalized.split()).strip(".,") in _ANIMAL_NAMES:
            findings.append({"path": path, "reason": "The proposed recipe still contains an animal ingredient."})
    return findings


def vegan_plan_usable(recipe: dict, plan: dict | None) -> bool:
    if not vegan_plan_matches(recipe, plan) or plan["status"] != "ready" or plan.get("review_items") != []:
        return False
    verification = plan.get("verification")
    return (
        isinstance(verification, dict) and verification.get("provider") == "gemini"
        and verification.get("status") == "clear" and verification.get("findings") == []
        and verification.get("reason_code") is None and verification.get("reason") is None
        and plan.get("reason_code") is None and plan.get("reason") is None
        and isinstance(plan.get("changes"), list)
        and vegan_substitutions_approved(plan["changes"], verification.get("approved_substitutions"))
        and not vegan_source_review(recipe)
        and not vegan_animal_review(vegan_ingredient_names(recipe, plan["changes"]))
    )


def rebind_vegan_plan(previous_recipe: dict, recipe: dict, plan: dict | None) -> dict | None:
    """Retain verified ingredient and method decisions when their inputs are unchanged."""
    if not vegan_plan_usable(previous_recipe, plan):
        return None
    relevant = set(previous_recipe) | set(recipe)
    relevant.discard("servings")
    if all(change["path"] != "/title" for change in plan["changes"]):
        relevant.discard("title")
    if not all(same_recipe_inputs(previous_recipe.get(key), recipe.get(key)) for key in relevant):
        return None
    try:
        rebound = validate_vegan_plan(recipe, plan)
    except ValueError:
        return None
    if rebound["status"] != "ready":
        return None
    return {**plan, **rebound}


def _vegan_plan(recipe: dict, status: str, reason_code: str | None, reason: str | None,
                changes: list, review_items: list) -> dict:
    return {
        "protocol_version": VEGAN_PLAN_VERSION,
        "status": status,
        "reason_code": reason_code,
        "reason": reason,
        "recipe_fingerprint": _fingerprint(recipe),
        "changes": changes,
        "review_items": review_items,
    }


def unavailable_vegan_plan(recipe: dict, reason_code: str, reason: str, review_items=()) -> dict:
    return _vegan_plan(recipe, "unavailable", reason_code, reason, [], list(review_items))


def vegan_source_review(recipe: dict) -> dict | None:
    """Identify the source values a reviewer must supply before planning."""
    review = []
    for collection, key, reason in (
        ("ingredients", "name", "Verify the ingredient name so its role and a vegan substitute can be assessed."),
        ("steps", "instruction", "Verify the preparation step so the vegan method can be assessed."),
    ):
        for index, record in enumerate(recipe[collection]):
            field = record[key]
            if field["status"] == "needs_review" or not _text(field["value"]) or UNREADABLE.search(field["value"]):
                review.append({"path": f"/{collection}/{index}/{key}", "reason": reason})
    if not review:
        return None
    return _vegan_plan(
        recipe, "needs_review", "insufficient_information",
        "Review the flagged ingredient names or preparation steps before making the recipe vegan.", [], review,
    )


def _phrase(text: str) -> str:
    return r"\s+".join(re.escape(word) for word in text.split())


def _measure_pattern(recipe: dict, replacement_measures=()) -> re.Pattern:
    units = set(known_units()) | TIME_UNITS | OTHER_UNITS
    units.update(item["unit"]["value"] for item in recipe["ingredients"] if item["unit"]["value"])
    units.update(measure["unit"] for measure in replacement_measures if measure["unit"])
    unit_pattern = "|".join(_phrase(unit) for unit in sorted(units, key=lambda unit: (-len(unit), unit)))
    time_pattern = "|".join(sorted(TIME_UNITS, key=lambda unit: (-len(unit), unit)))
    return re.compile(
        rf"(?<![\d/⁄∕])(?P<number>{QUANTITY_PATTERN})(?![\d/⁄∕])"
        rf"(?:(?P<ordinal>\.(?=\s*(?:{time_pattern})(?!\w)))?\s*(?P<unit>{unit_pattern})(?!\w))?",
        re.IGNORECASE,
    )


def _measure(match: re.Match) -> tuple[str, str]:
    return match["number"] + (match["ordinal"] or ""), (match["unit"] or "").casefold().rstrip(".")


def _measures(pattern: re.Pattern, value: str | None) -> list[tuple[str, str]]:
    return [_measure(match) for match in pattern.finditer(value or "")]


def _preserves_measures(before: list, after: list, removals: Counter) -> bool:
    remaining = iter(after)
    expected = next(remaining, None)
    for measure in before:
        if measure == expected:
            expected = next(remaining, None)
        elif removals[measure]:
            removals[measure] -= 1
        else:
            return False
    return expected is None


def _ingredient_measure_matches(pattern: re.Pattern, text: str, name: str, prep: str | None) -> list[re.Match]:
    """Recognize explicit measures beside an ingredient's source or replacement name."""
    modifier = rf"(?:{_phrase(prep)}\s+)?" if prep else ""
    following = re.compile(rf"\s+(?:(?:of|de)\s+)?{modifier}{_phrase(name)}(?!\w)", re.I)
    preceding = re.compile(rf"(?<!\w){_phrase(name)}\s*\(\s*$", re.I)
    return [
        match for match in pattern.finditer(text)
        if following.match(text, match.end()) or preceding.search(text, 0, match.start())
    ]


def _preserves_ingredient_measures(pattern: re.Pattern, recipe: dict, edits: dict, change: dict) -> bool:
    before, after = change["before"], change["after"] or ""
    if not _measures(pattern, before):
        return True
    for index, ingredient in enumerate(recipe["ingredients"]):
        name = ingredient["name"]["value"]
        if not name:
            continue
        base = f"/ingredients/{index}"
        prep = ingredient["preparation"]["value"]
        replacement = edits.get(f"{base}/name", {})
        if replacement.get("replacement_measure"):
            continue
        new_name = replacement.get("after", name)
        new_prep = edits.get(f"{base}/preparation", {}).get("after", prep)
        amounts = [_measure(match) for match in _ingredient_measure_matches(pattern, before, name, prep)]
        matched = [_measure(match) for match in _ingredient_measure_matches(pattern, after, new_name, new_prep)]
        # A literal match can prove a conflict. Contextual verification handles
        # names with different inflections, shorthand, or word order.
        if amounts and Counter(matched) - Counter(amounts):
            return False
    return True


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_replacement_measure(measure) -> bool:
    if not isinstance(measure, dict) or set(measure) != {"quantity", "unit", "basis"}:
        return False
    quantity, unit, basis = (measure[key] for key in ("quantity", "unit", "basis"))
    if not _text(quantity) or (parse_number(quantity) or 0) <= 0 or not _text(basis):
        return False
    return unit is None or (_text(unit) and unit.strip().casefold().rstrip(".") in REPLACEMENT_UNITS)


def _converted_step_measures(pattern: re.Pattern, recipe: dict, edits: dict, change: dict) -> list | None:
    """Replace validated new measures with their old equivalents for comparison."""
    before, after = change["before"], change["after"] or ""
    index = int(change["path"].split("/")[2])
    linked_ids = set(recipe["steps"][index]["ingredient_ids"])
    matched = {}
    for ingredient_index, ingredient in enumerate(recipe["ingredients"]):
        base = f"/ingredients/{ingredient_index}"
        replacement = edits.get(f"{base}/name", {})
        measure = replacement.get("replacement_measure")
        if measure is None:
            continue
        name = ingredient["name"]["value"]
        prep = ingredient["preparation"]["value"]
        new_name = replacement["after"]
        new_prep = edits.get(f"{base}/preparation", {}).get("after", prep)
        original_matches = _ingredient_measure_matches(pattern, before, name, prep)
        converted_matches = _ingredient_measure_matches(pattern, after, new_name, new_prep)
        if not original_matches and not converted_matches:
            continue
        if ingredient["id"] not in linked_ids or len(original_matches) != len(converted_matches):
            return None
        source = normalized_amount(ingredient["quantity"]["value"], ingredient["unit"]["value"])
        target = normalized_amount(measure["quantity"], measure["unit"])
        for old, new in zip(original_matches, converted_matches):
            old_quantity, old_unit = _measure(old)
            new_quantity, new_unit = _measure(new)
            old_value = normalized_amount(old_quantity, old_unit or None)
            new_value = normalized_amount(new_quantity, new_unit or None)
            if (new.start() in matched or old_value is None or new_value is None
                    or old_value[0] != source[0] or new_value[0] != target[0]
                    or old_value[1] > source[1] or old_value[1] / source[1] * target[1] != new_value[1]):
                return None
            matched[new.start()] = _measure(old)
    return [matched.get(match.start(), _measure(match)) for match in pattern.finditer(after)]


def validate_vegan_plan(recipe: dict, proposal: dict) -> dict:
    """Normalize anchored edits and validate replacement measures against the source."""
    if not isinstance(proposal, dict) or not all(
        isinstance(proposal.get(key), list) for key in ("changes", "review_items")
    ):
        raise ValueError("The vegan plan must contain changes and review items.")
    status, reason_code, reason = (proposal.get(key) for key in ("status", "reason_code", "reason"))
    if status not in PLAN_STATUSES:
        raise ValueError("The vegan plan must state whether a viable adaptation can be made.")
    if status == "ready":
        if reason_code is not None or reason is not None:
            raise ValueError("A ready vegan plan must leave the uncertainty reason empty.")
    elif (
        not _text(reason) or proposal["changes"] or not proposal["review_items"]
        or reason_code not in (
            ("insufficient_information",) if status == "needs_review"
            else ("insufficient_information", "no_viable_substitute")
        )
    ):
        raise ValueError("An unresolved vegan plan must give its reason and affected fields without edits.")

    fields = {"/title": recipe["title"], "/servings": recipe["servings"]}
    editable = {"/title"}
    record_paths = {}
    for collection, names in REVIEW_FIELDS.items():
        for index, entry in enumerate(recipe[collection]):
            base = f"/{collection}/{index}"
            record_paths[base] = f"{base}/{'instruction' if collection == 'steps' else 'name'}"
            for name in names:
                path = f"{base}/{name}"
                fields[path] = entry[name]
                if name in EDITABLE_FIELDS.get(collection, ()):
                    editable.add(path)
    warnings = []

    def warn(path, reason):
        item = {"path": path, "reason": reason}
        if item not in warnings:
            warnings.append(item)

    for item in proposal["review_items"]:
        if not isinstance(item, dict) or not _text(item.get("path")) or not _text(item.get("reason")):
            raise ValueError("The vegan plan returned an invalid review item.")
        path = record_paths.get(item["path"], item["path"])
        if path not in fields and path != "/recipe":
            raise ValueError("The vegan plan review item refers to an unknown recipe field.")
        warn(path, item["reason"])

    if status != "ready":
        return _vegan_plan(recipe, status, reason_code, reason.strip(), [], warnings)

    if source_review := vegan_source_review(recipe):
        for item in source_review["review_items"]:
            if not any(warning["path"] == item["path"] for warning in warnings):
                warn(item["path"], item["reason"])

    edits = {}
    seen = set()
    for item in proposal["changes"]:
        if not isinstance(item, dict) or not all(_text(item.get(key)) for key in ("path", "before", "reason")):
            raise ValueError("The vegan plan returned an invalid change.")
        path, before, after, reason = (item.get(key) for key in ("path", "before", "after", "reason"))
        field_name = path.rsplit("/", 1)[-1]
        if path not in editable or path in seen:
            raise ValueError("The vegan plan must edit each supported recipe field at most once.")
        if "after" not in item or not (_text(after) or (after is None and field_name in REMOVABLE_FIELDS)):
            raise ValueError("The vegan plan returned an invalid replacement.")
        seen.add(path)
        field = fields[path]
        if field["status"] == "needs_review" or field["value"] is None:
            warn(path, "The vegan proposal tried to edit a field without a verified value.")
        elif before != field["value"]:
            raise ValueError("The vegan plan change does not match the original recipe field.")
        elif before != after:
            edit = {"path": path, "before": before, "after": after, "reason": reason}
            replacement_measure = item.get("replacement_measure")
            if replacement_measure is not None:
                if not re.fullmatch(r"/ingredients/[0-9]+/name", path) or not _valid_replacement_measure(replacement_measure):
                    raise ValueError("A replacement measure must specify a positive amount, culinary unit and equivalence basis.")
                base = path.removesuffix("/name")
                quantity, unit = (fields[f"{base}/{part}"] for part in ("quantity", "unit"))
                value = quantity["value"]
                if (quantity["status"] == "needs_review" or unit["status"] == "needs_review"
                        or not _text(value) or (parse_number(value) or 0) <= 0):
                    warn(path, "A replacement measure needs a confirmed numeric quantity and its unit or count.")
                else:
                    edit["replacement_measure"] = dict(replacement_measure)
            edits[path] = edit
        elif item.get("replacement_measure") is not None:
            raise ValueError("A new measure requires an ingredient replacement.")

    measures = proposed_vegan_measures(list(edits.values()))
    pattern = _measure_pattern(recipe, measures.values())
    removed_timings = {}
    for path, item in list(edits.items()):
        field_name = path.rsplit("/", 1)[-1]
        if field_name == "instruction":
            continue
        before = _measures(pattern, item["before"])
        after = (
            _converted_step_measures(pattern, recipe, edits, item)
            if measures and field_name == "condition"
            else _measures(pattern, item["after"])
        )
        allowed = Counter(measure for measure in before if measure[1] in TIME_UNITS) if (
            field_name in {"duration", "condition"}
        ) else Counter()
        if after is None or not _preserves_measures(before, after, allowed.copy()):
            del edits[path]
            warn(path, "Check the proposed vegan change. It would change a measure or add a number.")
        elif allowed:
            removed_timings[path] = (Counter(before) - Counter(after)) & allowed

    for path, item in list(edits.items()):
        if not path.startswith("/steps/") or not path.endswith(("/instruction", "/condition")):
            continue
        base = path.rsplit("/", 1)[0]
        before = _measures(pattern, item["before"])
        after = (
            _converted_step_measures(pattern, recipe, edits, item)
            if measures
            else _measures(pattern, item["after"])
        )
        allowed = removed_timings.get(f"{base}/duration", Counter()) | removed_timings.get(
            f"{base}/condition", Counter()
        )
        if after is None or not _preserves_measures(before, after, allowed) or not _preserves_ingredient_measures(
            pattern, recipe, edits, item
        ):
            del edits[path]
            warn(path, "Check the proposed vegan change. It would change a measure or add a number.")

    if measures:
        for index, step in enumerate(recipe["steps"]):
            for field in ("instruction", "condition"):
                path = f"/steps/{index}/{field}"
                value = step[field]["value"]
                if path in edits or not value or any(item["path"] == path for item in warnings):
                    continue
                if _converted_step_measures(
                    pattern, recipe, edits, {"path": path, "before": value, "after": value},
                ) is None:
                    warn(path, "Update the ingredient measure in its linked preparation step.")

    if warnings:
        return _vegan_plan(recipe, "needs_review", "insufficient_information", warnings[0]["reason"], [], warnings)
    return _vegan_plan(recipe, "ready", None, None, list(edits.values()), [])


def veganize(
    recipe: dict,
    plan: dict | None,
    change: Callable[[str, dict, str | None, str], None],
    warn: Callable[[str, str], None],
) -> None:
    """Apply a valid plan without changing the source fields or calling a model."""
    if not vegan_plan_matches(recipe, plan):
        raise ValueError("The vegan plan is missing or does not match this recipe. Generate a new plan.")
    if not vegan_plan_usable(recipe, plan):
        reason = plan.get("reason")
        raise ValueError(reason if _text(reason) else "A verified vegan plan is required to adapt this recipe.")
    validated = validate_vegan_plan(recipe, plan)
    if validated["status"] != "ready":
        raise ValueError(validated["reason"])
    for item in validated["changes"]:
        path = item["path"]
        keys = path[1:].split("/")
        if len(keys) == 1:
            field = recipe[keys[0]]
        else:
            record = recipe[keys[0]][int(keys[1])]
            field = record[keys[2]]
            if keys[0] == "ingredients" and keys[2] == "name":
                quantity, unit = record["quantity"], record["unit"]
                if quantity["value"] is None or quantity["status"] == "needs_review":
                    warn(path.removesuffix("/name") + "/quantity", VEGAN_AMOUNT_WARNING)
                if unit["status"] == "needs_review":
                    warn(path.removesuffix("/name") + "/unit", VEGAN_UNIT_WARNING)
        change(path, field, item["after"], item["reason"])
        if measure := item.get("replacement_measure"):
            base = path.removesuffix("/name")
            for key in ("quantity", "unit"):
                change(f"{base}/{key}", record[key], measure[key], measure["basis"])
