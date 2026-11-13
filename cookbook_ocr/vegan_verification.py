"""Check a vegan plan for concrete issues using the recipe and proposed edits."""

import json
from pathlib import Path

from .extraction import UNREADABLE
from .providers import GeminiClient, ProviderError
from .review import REVIEW_FIELDS
from .vegan import vegan_plan_matches


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
FINDING_KINDS = {"animal_ingredient", "cooking_function", "quantity_or_unit", "preparation"}


def _related_records(recipe: dict, collection: str, index: int) -> set[str]:
    record = recipe[collection][index]
    related = {f"/{collection}/{index}"}
    if collection == "ingredients":
        steps = [(i, step) for i, step in enumerate(recipe["steps"]) if record["id"] in step["ingredient_ids"]]
        related.update(f"/steps/{i}" for i, _ in steps)
    else:
        steps = [(index, record)]
        related.update(f"/ingredients/{i}" for i, item in enumerate(recipe["ingredients"])
                       if item["id"] in record["ingredient_ids"])
    tool_ids = {tool_id for _, step in steps for tool_id in step["tool_ids"]}
    related.update(f"/tools/{i}" for i, tool in enumerate(recipe["tools"]) if tool["id"] in tool_ids)
    return related


def _source_contains(field: dict, quote: str) -> bool:
    if field["status"] not in {"stated", "reviewed", "derived"}:
        return False
    return quote in (field["value"] or "") or (
        field["status"] == "stated" and quote in ((field.get("evidence") or {}).get("quote") or "")
    )


def _validated_finding(finding, recipe: dict, fields: dict, changes: dict) -> dict | None:
    if not isinstance(finding, dict):
        return None
    path, kind, reason = (finding.get(key) for key in ("path", "kind", "reason"))
    if (not isinstance(path, str) or path not in fields or not path.startswith(("/ingredients/", "/steps/"))
            or not isinstance(kind, str) or kind not in FINDING_KINDS
            or not isinstance(reason, str) or not reason.strip()
            or not isinstance(finding.get("evidence"), list)):
        return None

    _, collection, index, field_name = path.split("/")
    related = _related_records(recipe, collection, int(index))
    evidence = []
    has_source_quote = False
    has_result_quote = False
    for item in finding["evidence"]:
        if not isinstance(item, dict):
            return None
        source_path, quote = item.get("path"), item.get("quote")
        if (not isinstance(source_path, str) or source_path not in fields
                or source_path.rsplit("/", 1)[0] not in related
                or not isinstance(quote, str) or not quote.strip() or UNREADABLE.search(quote)):
            return None
        in_source = _source_contains(fields[source_path], quote)
        if not in_source and quote not in (changes.get(source_path) or ""):
            return None
        has_source_quote |= in_source
        if source_path == path:
            result_text = changes.get(source_path, fields[source_path]["value"]) or ""
            has_result_quote |= quote == result_text if field_name == "name" else quote in result_text
        reference = {"path": source_path, "quote": quote}
        if reference not in evidence:
            evidence.append(reference)
    if not has_source_quote or (kind == "animal_ingredient" and not has_result_quote):
        return None
    return {"path": path, "kind": kind, "reason": reason.strip(), "evidence": evidence}


def verify_vegan_plan(gemini: GeminiClient | None, recipe: dict, plan: dict) -> dict:
    """Append grounded findings while leaving the planner's changes untouched."""
    def unavailable(reason: str) -> dict:
        return {**plan, "verification": {
            "provider": "gemini", "status": "unavailable", "findings": [], "reason": reason,
        }}

    if not vegan_plan_matches(recipe, plan):
        return unavailable("Gemini needs a current vegan plan to verify.")
    if gemini is None:
        return unavailable("Gemini verification is unavailable.")

    prompt = (PROMPTS_DIR / "verify_vegan.system.txt").read_text(encoding="utf-8")
    schema = json.loads((PROMPTS_DIR / "verify_vegan.schema.json").read_text(encoding="utf-8"))
    try:
        result = gemini.respond_structured(prompt, schema, {
            "recipe": recipe, "changes": plan["changes"], "review_items": plan["review_items"],
        })
    except (ProviderError, ValueError):
        return unavailable("Gemini could not verify the vegan plan.")
    if not isinstance(result, dict) or not isinstance(result.get("findings"), list):
        return unavailable("Gemini returned invalid verification results.")

    fields = {
        f"/{collection}/{index}/{name}": record[name]
        for collection, names in REVIEW_FIELDS.items()
        for index, record in enumerate(recipe[collection])
        for name in names
    }
    changes = {change["path"]: change["after"] for change in plan["changes"]}
    findings = []
    discarded = 0
    for item in result["findings"]:
        finding = _validated_finding(item, recipe, fields, changes)
        if finding is None:
            discarded += 1
        elif finding not in findings:
            findings.append(finding)
    if discarded and not findings:
        return unavailable("Gemini's findings could not be grounded in the recipe.")

    review_items = list(plan["review_items"])
    for finding in findings:
        item = {key: finding[key] for key in ("path", "reason")}
        if item not in review_items:
            review_items.append(item)
    verification = {"provider": "gemini", "status": "needs_review" if findings else "clear", "findings": findings}
    if discarded:
        verification["discarded_findings"] = discarded
    return {
        **plan, "status": "needs_review" if review_items else plan["status"],
        "verification": verification, "review_items": review_items,
    }
