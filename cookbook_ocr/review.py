"""Apply human decisions without replacing the extracted recipe or its evidence."""

import re
from copy import deepcopy

from .extraction import INGREDIENT_FIELDS, UNREADABLE


REVIEW_FIELDS = {
    "ingredients": INGREDIENT_FIELDS,
    "tools": ("name",),
    "steps": ("instruction", "duration", "condition"),
}


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
        original_values = {path: fields[path]["value"] for path in pending}
    except (KeyError, TypeError):
        raise ValueError("The extraction report contains an invalid recipe object.") from None

    recorded = []
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("Each review decision must be an object.")
        path, action = decision.get("path"), decision.get("action")
        if not isinstance(path, str) or path not in pending:
            raise ValueError("Each review decision must address a different field flagged for review.")
        if action not in ("confirm", "correct"):
            raise ValueError("Choose confirm or correct for the review decision.")

        field = fields[path]
        before = original_values[path]
        value = decision.get("value", before if action == "confirm" else None)
        if not isinstance(value, str) or not value.strip() or UNREADABLE.search(value):
            raise ValueError("Enter the value verified against the original recipe.")
        value = value.strip()
        if path == "/servings" and not re.fullmatch(r"[1-9][0-9]*", value):
            raise ValueError("Enter a positive whole number for original servings.")
        if action == "confirm" and before is not None and value != before:
            raise ValueError("Use correct to change an existing extracted value.")

        field.update(value=value, status="reviewed")
        recorded.append({"path": path, "action": action, "value": value, "before": before, "after": value})
        pending.remove(path)

    return {
        "recipe": reviewed,
        "decisions": recorded,
        "status": "needs_review" if pending else "reviewed",
        "unresolved_paths": sorted(pending),
    }
