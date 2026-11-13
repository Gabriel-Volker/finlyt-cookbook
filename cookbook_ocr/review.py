"""Apply human decisions without replacing the extracted recipe or its evidence."""

import re
from copy import deepcopy

from .extraction import INGREDIENT_FIELDS, UNREADABLE


REVIEW_FIELDS = {
    "ingredients": INGREDIENT_FIELDS,
    "tools": ("name",),
    "steps": ("instruction", "duration", "condition"),
}
OPTIONAL_FIELDS = {"title", "servings", "quantity", "unit", "preparation", "optional_when", "duration", "condition"}
REFERENCE_FIELDS = {"ingredient_ids": "ingredients", "tool_ids": "tools"}


def review_recipe(recipe: dict, decisions: list) -> dict:
    """Build a reviewed copy from the original fields and the current decisions."""
    if not isinstance(decisions, list):
        raise ValueError("Review decisions must be a list.")

    reviewed = deepcopy(recipe)
    try:
        fields = {f"/{name}": reviewed[name] for name in ("title", "servings")}
        for collection, names in REVIEW_FIELDS.items():
            fields.update({
                f"/{collection}/{index}/{name}": entry[name]
                for index, entry in enumerate(reviewed[collection])
                for name in names
            })
        pending = {path for path, field in fields.items() if field["status"] == "needs_review"}
        references = {}
        for key, collection in REFERENCE_FIELDS.items():
            allowed = {item["id"] for item in reviewed[collection]}
            references.update({f"/steps/{index}/{key}": (step, key, allowed)
                               for index, step in enumerate(reviewed["steps"])})
    except (KeyError, TypeError):
        raise ValueError("The extraction report contains an invalid recipe object.") from None

    recorded = []
    seen = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("Each review decision must be an object.")
        path, action = decision.get("path"), decision.get("action")
        if not isinstance(path, str) or (path not in fields and path not in references) or path in seen:
            raise ValueError("Each review decision must address a different existing recipe field.")
        if action not in ("confirm", "correct"):
            raise ValueError("Choose confirm or correct for the review decision.")

        if path in references:
            step, key, allowed = references[path]
            before = step[key]
            value = decision.get("value", before if action == "confirm" else None)
            if (not isinstance(before, list) or not isinstance(value, list)
                    or any(not isinstance(item, str) or item not in allowed for item in value)):
                raise ValueError("Choose existing recipe entries for step references.")
            value = list(dict.fromkeys(value))
            if action == "confirm" and set(value) == set(before):
                value = before
        else:
            field = fields[path]
            before = field["value"]
            value = decision.get("value", before if action == "confirm" else None)
            key = path.rsplit("/", 1)[-1]
            can_clear = action == "correct" and "value" in decision and key in OPTIONAL_FIELDS
            if value is not None or not can_clear:
                if not isinstance(value, str) or not value.strip() or UNREADABLE.search(value):
                    raise ValueError("Enter the value verified against the original recipe.")
                value = value.strip()
                if action == "confirm" and isinstance(before, str) and value == before.strip():
                    value = before
                if path == "/servings" and (action == "correct" or before is None) and not re.fullmatch(r"[1-9][0-9]*", value):
                    raise ValueError("Enter a positive whole number for original servings.")
        if action == "confirm" and before is not None and value != before:
            raise ValueError("Use correct to change an existing extracted value.")

        if path in references:
            step[key] = value
        else:
            field.update(value=value, status="reviewed")
        recorded.append({"path": path, "action": action, "value": value, "before": before, "after": value})
        pending.discard(path)
        seen.add(path)

    return {
        "recipe": reviewed,
        "decisions": recorded,
        "status": "needs_review" if pending else "reviewed",
        "unresolved_paths": sorted(pending),
    }
