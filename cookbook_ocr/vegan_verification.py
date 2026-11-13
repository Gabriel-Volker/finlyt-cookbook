"""Check a vegan plan for concrete issues using the recipe and proposed edits."""

import json
import logging
from pathlib import Path

from .extraction import UNREADABLE
from .providers import GeminiClient, ProviderError
from .review import REVIEW_FIELDS
from .vegan import (
    proposed_vegan_substitutions,
    unavailable_vegan_plan,
    validate_vegan_plan,
    vegan_animal_review,
    vegan_ingredient_names,
    vegan_plan_matches,
    vegan_plan_usable,
    vegan_substitutions_approved,
)


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
LOGGER = logging.getLogger(__name__)
FINDING_KINDS = {"animal_ingredient", "unsupported_substitute", "cooking_function", "quantity_or_unit", "preparation"}


def _provider_failure_reason(error: ProviderError) -> str:
    status = error.http_status
    if status == 429:
        return "Gemini rate or quota limit reached (HTTP 429). Retry after the quota allows it."
    if status in (401, 403):
        return f"Gemini denied access (HTTP {status}). Check the API key and model access."
    if status == 400:
        return "Gemini rejected the vegan verification request (HTTP 400). Check the API configuration."
    if status is not None:
        return f"Gemini returned HTTP {status} while verifying the vegan plan."
    if error.kind == "timeout":
        return "Gemini timed out while verifying the vegan plan. Retry adaptation plans."
    if error.kind == "connection":
        return "Could not reach Gemini to verify the vegan plan. Check the connection and retry adaptation plans."
    return "Gemini did not return a usable vegan verification response. Retry adaptation plans."


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
    # A retained or newly added animal ingredient is a property of the final
    # recipe. A quote from that field is enough even if the source never used it.
    if kind == "animal_ingredient":
        grounded = has_result_quote
    else:
        grounded = has_source_quote and (kind != "unsupported_substitute" or has_result_quote)
    if not grounded:
        return None
    return {"path": path, "kind": kind, "reason": reason.strip(), "evidence": evidence}


def verify_vegan_plan(gemini: GeminiClient | None, recipe: dict, plan: dict) -> dict:
    """Require independent approval before any proposed substitution can be used."""
    def unavailable(reason: str, reason_code: str = "verification_unavailable") -> dict:
        return {
            **unavailable_vegan_plan(recipe, reason_code, reason, [{"path": "/recipe", "reason": reason}]),
            "verification": {
                "provider": "gemini", "status": "unavailable", "reason_code": reason_code,
                "reason": reason, "findings": [], "approved_substitutions": [],
            },
        }

    if not vegan_plan_matches(recipe, plan):
        return unavailable("Gemini needs a current vegan plan to verify.")
    if plan["status"] != "ready" or vegan_plan_usable(recipe, plan):
        return plan
    try:
        plan = validate_vegan_plan(recipe, plan)
    except ValueError:
        return unavailable("Gemini needs a valid vegan plan to verify.")
    if plan["status"] != "ready":
        return plan
    replacements = proposed_vegan_substitutions(plan["changes"])
    ingredient_names = vegan_ingredient_names(recipe, plan["changes"])
    if review_items := vegan_animal_review(ingredient_names):
        return unavailable_vegan_plan(
            recipe, "verification_failed", "An animal ingredient remains in the proposed recipe.", review_items,
        )
    if gemini is None:
        return unavailable("Set GEMINI_API_KEY to verify the vegan plan.")
    changes = {change["path"]: change["after"] for change in plan["changes"]}
    steps_to_check = {}
    for index, step in enumerate(recipe["steps"]):
        for name in REVIEW_FIELDS["steps"]:
            path = f"/steps/{index}/{name}"
            steps_to_check[path] = changes.get(path, step[name]["value"])
    prompt = (PROMPTS_DIR / "verify_vegan.system.txt").read_text(encoding="utf-8")
    schema = json.loads((PROMPTS_DIR / "verify_vegan.schema.json").read_text(encoding="utf-8"))
    approvals_schema = schema["properties"]["approved_substitutions"]
    approvals_schema["maxItems"] = len(replacements)
    if replacements:
        approvals_schema["items"]["properties"]["path"]["enum"] = list(replacements)
    try:
        result = gemini.respond_structured(prompt, schema, {
            "recipe": recipe, "changes": plan["changes"], "review_items": plan["review_items"],
            "substitutions_to_approve": [
                {"path": path, "ingredient": name} for path, name in replacements.items()
            ],
            "ingredients_to_check": [
                {"path": path, "ingredient": name} for path, name in ingredient_names.items()
            ],
            "steps_to_check": steps_to_check,
        })
    except ProviderError as error:
        reason = _provider_failure_reason(error)
        LOGGER.warning("%s", reason)
        return unavailable(reason)
    except ValueError:
        reason = "Gemini returned invalid verification results. Retry adaptation plans."
        LOGGER.warning("%s", reason)
        return unavailable(reason)
    if not isinstance(result, dict) or not all(key in result for key in (
        "status", "reason_code", "reason", "findings", "approved_substitutions",
    )) or not isinstance(result["findings"], list):
        return unavailable("Gemini returned invalid verification results.")

    status, reason_code, reason = (result[key] for key in ("status", "reason_code", "reason"))
    if status == "clear":
        if (
            reason_code is not None or reason is not None or result["findings"]
            or not vegan_substitutions_approved(plan["changes"], result["approved_substitutions"])
        ):
            return unavailable("Gemini did not explicitly approve every vegan substitution.")
        return {**plan, "verification": {
            "provider": "gemini", "status": status, "reason_code": None, "reason": None,
            "findings": [], "approved_substitutions": result["approved_substitutions"],
        }}
    if result["approved_substitutions"] != [] or not isinstance(reason, str) or not reason.strip():
        return unavailable("Gemini returned invalid verification results.")
    if status == "unavailable" and reason_code == "insufficient_information" and result["findings"] == []:
        return unavailable(reason.strip(), reason_code)
    if status != "needs_review" or reason_code != "verification_failed":
        return unavailable("Gemini returned invalid verification results.")

    fields = {
        f"/{collection}/{index}/{name}": record[name]
        for collection, names in REVIEW_FIELDS.items()
        for index, record in enumerate(recipe[collection])
        for name in names
    }
    findings = []
    for item in result["findings"]:
        finding = _validated_finding(item, recipe, fields, changes)
        if finding is not None and finding not in findings:
            findings.append(finding)
    if not findings:
        return unavailable("Gemini's findings could not be grounded in the recipe.")

    review_items = [{key: finding[key] for key in ("path", "reason")} for finding in findings]
    reason = findings[0]["reason"]
    return {
        **unavailable_vegan_plan(recipe, reason_code, reason, review_items),
        "verification": {
            "provider": "gemini", "status": "needs_review", "reason_code": reason_code,
            "reason": reason, "findings": findings, "approved_substitutions": [],
        },
    }
