"""Validate and apply a vegan plan tied to a particular recipe."""

import json
import re
from collections import Counter
from collections.abc import Callable
from hashlib import sha256

from .quantities import QUANTITY_PATTERN, known_units
from .review import REVIEW_FIELDS


EDITABLE_FIELDS = {
    "ingredients": {"name", "preparation"},
    "steps": {"instruction", "condition", "duration"},
}
VEGAN_PLAN_VERSION = 2
PLAN_STATUSES = ("ready", "needs_review", "unavailable")
REMOVABLE_FIELDS = {"preparation", "condition", "duration"}
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


def vegan_substitutions_approved(changes: list, approvals) -> bool:
    """Require one exact approval for every proposed ingredient replacement."""
    try:
        expected = proposed_vegan_substitutions(changes)
    except ValueError:
        return False
    if not isinstance(approvals, list) or len(approvals) != len(expected):
        return False
    approved = set()
    for entry in approvals:
        if not isinstance(entry, dict):
            return False
        path, ingredient = entry.get("path"), entry.get("ingredient")
        if not isinstance(path, str) or path in approved or path not in expected or ingredient != expected[path]:
            return False
        approved.add(path)
    return True


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
    )


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


def _phrase(text: str) -> str:
    return r"\s+".join(re.escape(word) for word in text.split())


def _measure_pattern(recipe: dict) -> re.Pattern:
    units = set(known_units()) | TIME_UNITS | OTHER_UNITS
    units.update(item["unit"]["value"] for item in recipe["ingredients"] if item["unit"]["value"])
    unit_pattern = "|".join(_phrase(unit) for unit in sorted(units, key=lambda unit: (-len(unit), unit)))
    return re.compile(
        rf"(?<![\d/⁄∕])(?P<number>{QUANTITY_PATTERN})(?![\d/⁄∕])"
        rf"(?:[ \t]*(?P<unit>{unit_pattern})(?!\w))?",
        re.IGNORECASE,
    )


def _measure(match: re.Match) -> tuple[str, str]:
    return match["number"], (match["unit"] or "").casefold().rstrip(".")


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


def _ingredient_measures(pattern: re.Pattern, text: str, name: str, prep: str | None) -> list:
    """Recognize explicit measures beside an ingredient's source or replacement name."""
    modifier = rf"(?:{_phrase(prep)}\s+)?" if prep else ""
    following = re.compile(rf"\s+(?:(?:of|de)\s+)?{modifier}{_phrase(name)}(?!\w)", re.I)
    preceding = re.compile(rf"(?<!\w){_phrase(name)}\s*\(\s*$", re.I)
    return [
        _measure(match) for match in pattern.finditer(text)
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
        new_name = edits.get(f"{base}/name", {}).get("after", name)
        new_prep = edits.get(f"{base}/preparation", {}).get("after", prep)
        amounts = _ingredient_measures(pattern, before, name, prep)
        if amounts and amounts != _ingredient_measures(pattern, after, new_name, new_prep):
            return False
    return True


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _review_linked_steps(recipe: dict, edits: dict, warn: Callable[[str, str], None]) -> None:
    """Flag literal references to a replaced ingredient left in linked steps."""
    substitutions = {}
    for index, ingredient in enumerate(recipe["ingredients"]):
        edit = edits.get(f"/ingredients/{index}/name")
        if edit:
            substitutions[ingredient["id"]] = tuple(
                re.compile(rf"(?<!\w){_phrase(edit[key])}(?!\w)", re.I) for key in ("before", "after")
            )
    for index, step in enumerate(recipe["steps"]):
        references = [substitutions[key] for key in step["ingredient_ids"] if key in substitutions]
        for name in ("instruction", "condition"):
            path = f"/steps/{index}/{name}"
            text = edits.get(path, {}).get("after", step[name]["value"]) or ""
            if any(before.search(after.sub("", text)) for before, after in references):
                warn(path, "Check this step. It still refers to an ingredient that the vegan plan replaces.")


def validate_vegan_plan(recipe: dict, proposal: dict) -> dict:
    """Normalize an anchored proposal and withhold edits that change recipe measures."""
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
    review_paths = {"/recipe"}
    for collection, names in REVIEW_FIELDS.items():
        for index, entry in enumerate(recipe[collection]):
            base = f"/{collection}/{index}"
            review_paths.add(base)
            for name in names:
                path = f"{base}/{name}"
                fields[path] = entry[name]
                if name in EDITABLE_FIELDS.get(collection, ()):
                    editable.add(path)
    review_paths.update(fields)

    warnings = []

    def warn(path, reason):
        item = {"path": path, "reason": reason}
        if item not in warnings:
            warnings.append(item)

    for item in proposal["review_items"]:
        if not isinstance(item, dict) or not _text(item.get("path")) or not _text(item.get("reason")):
            raise ValueError("The vegan plan returned an invalid review item.")
        if item["path"] not in review_paths:
            raise ValueError("The vegan plan review item refers to an unknown recipe field.")
        warn(item["path"], item["reason"])

    if status != "ready":
        return _vegan_plan(recipe, status, reason_code, reason.strip(), [], warnings)

    for path, field in fields.items():
        ingredient_measure = path.startswith("/ingredients/") and path.endswith(("/quantity", "/unit"))
        if (field["status"] == "needs_review" and not ingredient_measure
                and not any(item["path"] == path for item in warnings)):
            warn(path, "Resolve this source field to complete the vegan adaptation.")

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
            edits[path] = {"path": path, "before": before, "after": after, "reason": reason}

    pattern = _measure_pattern(recipe)
    removed_timings = {}
    for path, item in list(edits.items()):
        field_name = path.rsplit("/", 1)[-1]
        if field_name == "instruction":
            continue
        before, after = (_measures(pattern, item[key]) for key in ("before", "after"))
        allowed = Counter(measure for measure in before if measure[1] in TIME_UNITS) if (
            field_name in {"duration", "condition"}
        ) else Counter()
        if not _preserves_measures(before, after, allowed.copy()):
            del edits[path]
            warn(path, "Check the proposed vegan change. It would change a measure or add a number.")
        elif allowed:
            removed_timings[path] = (Counter(before) - Counter(after)) & allowed

    for path, item in list(edits.items()):
        if not path.startswith("/steps/") or not path.endswith(("/instruction", "/condition")):
            continue
        base = path.rsplit("/", 1)[0]
        before, after = (_measures(pattern, item[key]) for key in ("before", "after"))
        allowed = removed_timings.get(f"{base}/duration", Counter()) | removed_timings.get(
            f"{base}/condition", Counter()
        )
        if not _preserves_measures(before, after, allowed) or not _preserves_ingredient_measures(
            pattern, recipe, edits, item
        ):
            del edits[path]
            warn(path, "Check the proposed vegan change. It would change a measure or add a number.")

    _review_linked_steps(recipe, edits, warn)
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
                    warn(path.removesuffix("/name") + "/quantity", "Confirm the amount of the vegan replacement.")
                if unit["status"] == "needs_review":
                    warn(path.removesuffix("/name") + "/unit", "Confirm the unit for the vegan replacement.")
        change(path, field, item["after"], item["reason"])
