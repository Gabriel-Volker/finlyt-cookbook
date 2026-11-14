"""Validate ingredient scaling rules and calculate their exact factors."""

import json
import re
from fractions import Fraction
from hashlib import sha256

from .extraction import UNREADABLE, _source_quotes
from .quantities import QUANTITY_PATTERN, quantity_matches, scale_quantity, unit_aliases
from .review import REVIEW_FIELDS, same_recipe_inputs


MODES = {"proportional", "fixed", "per_batch", "review"}
PLAN_STATUSES = ("ready", "needs_review")
_SERVINGS = r"(?:servings?|portions?|people|persons?|porç(?:ão|ões)|pessoas?|portionen?|personen?)"
_BATCH = r"(?:batch(?:es)?|lotes?|fornadas?|chargen?)"
_PER_BATCH = rf"(?:per|por|je|pro|for each|para cada|each|cada)\s+{_BATCH}"
_FIXED_AMOUNT = re.compile(
    r"\b(?:fixed amount|same amount|once for the (?:entire|whole) recipe|"
    r"regardless of (?:the (?:number of )?)?(?:servings|portions|batch size|yield)|"
    r"quantidade fixa|mesma quantidade|uma vez para (?:a )?receita (?:toda|inteira)|"
    r"independentemente (?:do número de|das) (?:porções|pessoas)|"
    r"feste menge|gleiche menge|einmal für das ganze rezept|unabhängig von der portionszahl)\b",
    re.IGNORECASE,
)
_PER_BATCH_AMOUNT = re.compile(rf"\b{_PER_BATCH}\b", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÀ-Ü])|\n\s*\n")
_CLAUSE_END = re.compile(
    r";|,(?!\d|\s*(?:regardless|independentemente|unabhängig)\b)|"
    r"\b(?:and then|then|depois|em seguida|anschließend|danach)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_BATCH = re.compile(
    r"(?:\d\s*(?:[-–—/,]|\s+(?:to|bis|a|até|and|or|und|oder|ou))|"
    r"\b(?:at least|at most|up to|around|about|approximately|até|cerca de|pelo menos|"
    r"no máximo|bis zu|etwa|mindestens|höchstens))\s*$",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(?:not|never|don't|do not|não|nunca|nicht|nie)\b", re.IGNORECASE)


def _fingerprint(recipe: dict, *, legacy: bool = False) -> str:
    fields = recipe if legacy else {key: value for key, value in recipe.items() if key != "title"}
    data = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(data.encode("utf-8")).hexdigest()


def scaling_plan_matches(recipe: dict, plan: dict | None) -> bool:
    """Check whether a stored scaling plan belongs to these recipe fields."""
    return (
        isinstance(plan, dict) and plan.get("status") in PLAN_STATUSES
        and plan.get("recipe_fingerprint") in (_fingerprint(recipe), _fingerprint(recipe, legacy=True))
    )


def _record_fields(collection: str, index: int, record: dict) -> dict:
    return {f"/{collection}/{index}/{name}": record[name] for name in REVIEW_FIELDS[collection]}


def _evidence_fields(recipe: dict, index: int, ingredient: dict) -> dict:
    fields = {"/servings": recipe["servings"], **_record_fields("ingredients", index, ingredient)}
    tool_ids = set()
    for step_index, step in enumerate(recipe["steps"]):
        if ingredient["id"] in step["ingredient_ids"]:
            fields.update(_record_fields("steps", step_index, step))
            tool_ids.update(step["tool_ids"])
    for tool_index, tool in enumerate(recipe["tools"]):
        if tool["id"] in tool_ids:
            fields.update(_record_fields("tools", tool_index, tool))
    return fields


def _evidence_source(field: dict | None, quote: str) -> str | None:
    if not field or field["status"] not in {"stated", "reviewed", "derived"}:
        return None
    if UNREADABLE.search(quote):
        return None
    if field["status"] == "stated":
        original = (field.get("evidence") or {}).get("quote") or ""
        if quote in original:
            return original
    value = field["value"] or ""
    return value if quote in value else None


def _restore_quote(field: dict | None, quote: str) -> str | None:
    if _evidence_source(field, quote) is not None:
        return quote
    if not field or field["status"] not in {"stated", "reviewed", "derived"} or UNREADABLE.search(quote):
        return None
    candidates = [field["value"] or ""]
    if field["status"] == "stated":
        candidates.append((field.get("evidence") or {}).get("quote") or "")
    for source in candidates:
        matches = _source_quotes(source, quote)
        if len(matches) == 1:
            return matches[0].group()
    return None


def _confirms_batch_size(quote: str, source: str, count: int) -> bool:
    # A matching digit in a temperature, batch count, or range is not a batch size.
    number = rf"(?<![\d.,/\-–—])(?P<count>{count})(?![\d.,/\-–—])"
    patterns = (
        rf"\b{_PER_BATCH}(?:\s+(?:of|de|für)|\s*[,;:])?\s+{number}\s+{_SERVINGS}\b",
        rf"{number}\s+{_SERVINGS}\s+{_PER_BATCH}\b",
        rf"\b(?:each|one|a|cada|um|uma|eine|jede)\s+{_BATCH}\s+"
        rf"(?:serves|yields|makes|rende|ergibt)\s+{number}(?:\s+{_SERVINGS}\b|(?!\s+\w))",
    )
    for pattern in patterns:
        for sentence in _SENTENCE_END.split(source):
            if _NEGATION.search(sentence):
                continue
            for match in re.finditer(pattern, sentence, re.IGNORECASE):
                if match[0] in quote and not _AMBIGUOUS_BATCH.search(sentence[:match.start("count")]):
                    return True
    return False


def _phrase(value: str) -> str:
    return r"\s+".join(re.escape(word) for word in value.split())


def _supports_ingredient_mode(fields: dict, references: list[dict], index: int, ingredient: dict, wording: re.Pattern) -> bool:
    prefix = f"/ingredients/{index}/"
    name, quantity, unit = (ingredient[key]["value"] for key in ("name", "quantity", "unit"))
    if not name:
        return False
    measure = rf"(?P<amount>{QUANTITY_PATTERN})"
    if unit:
        units = "|".join(_phrase(alias) for alias in sorted(unit_aliases(unit), key=len, reverse=True))
        measure += rf"\s*(?P<unit>{units})"
    prep = ingredient["preparation"]["value"]
    modifier = rf"(?:{_phrase(prep)}\s+)?" if prep else ""
    name = rf"{modifier}{_phrase(name)}(?!\w)"
    named_measure = (
        re.compile(rf"(?<![\w.,/⁄∕–—−-])(?<![0-9]\s){measure}\s+"
                   rf"(?:(?:of(?:\s+the)?|de|da|das|do|dos)\s+)?{name}", re.IGNORECASE),
        re.compile(rf"(?<!\w){name}\s*\(\s*{measure}\s*\)", re.IGNORECASE),
    )
    for item in references:
        path, quote = item["path"], item["quote"]
        source = _evidence_source(fields[path], quote)
        for sentence in _SENTENCE_END.split(source):
            if quote not in sentence or _NEGATION.search(sentence) or not wording.search(" ".join(quote.split())):
                continue
            own_value = path.startswith(prefix) and quote in (fields[path]["value"] or "")
            if own_value or (path.startswith((prefix, "/steps/")) and any(
                quantity_matches(quantity, unit, match["amount"], match.groupdict().get("unit"))
                for pattern in named_measure for match in pattern.finditer(quote)
            )):
                return True
    return False


_FIXED_AMOUNT_WARNING = "The recipe specifies a fixed amount. Check the proportional scaling rule."
_PER_BATCH_AMOUNT_WARNING = "The recipe specifies an amount per batch. Check the proportional scaling rule."
_REVIEWED_AMOUNT_REASON = "Scale the numeric amount set in Review with the servings."
SCALING_RULE_DISPLAY_TEXTS = (_FIXED_AMOUNT_WARNING, _PER_BATCH_AMOUNT_WARNING, _REVIEWED_AMOUNT_REASON)


def proportional_source_conflict(recipe: dict, index: int, ingredient: dict) -> str | None:
    """Do not multiply an amount with an explicit fixed or per-batch instruction."""
    fields = _evidence_fields(recipe, index, ingredient)
    prefix = f"/ingredients/{index}/"
    for wording, reason in (
        (_FIXED_AMOUNT, _FIXED_AMOUNT_WARNING),
        (_PER_BATCH_AMOUNT, _PER_BATCH_AMOUNT_WARNING),
    ):
        references = []
        for path, field in fields.items():
            if not path.startswith((prefix, "/steps/")) or field["status"] not in {"stated", "reviewed", "derived"}:
                continue
            value = field["value"] or ""
            sources = [value]
            if field["status"] == "stated" and field.get("evidence"):
                sources.append(field["evidence"].get("quote") or "")
            for source in sources:
                for sentence in _SENTENCE_END.split(source):
                    for clause in _CLAUSE_END.split(sentence):
                        quote = clause.strip()
                        if wording.search(quote) and not UNREADABLE.search(quote):
                            references.append({"path": path, "quote": quote})
        if _supports_ingredient_mode(fields, references, index, ingredient, wording):
            return reason
    return None


def _normalize_rule(recipe: dict, index: int, ingredient: dict, rule: dict) -> dict:
    mode, reason = rule.get("mode"), rule.get("reason")
    batch_servings = rule.get("batch_servings")
    if not isinstance(mode, str) or mode not in MODES or not isinstance(reason, str) or not reason.strip():
        raise ValueError("A scaling rule has an unsupported mode or missing reason.")
    if "batch_servings" not in rule or (mode == "per_batch" and (
        type(batch_servings) is not int or batch_servings <= 0
    )) or (mode != "per_batch" and batch_servings is not None):
        raise ValueError("Only per-batch scaling rules can specify a positive batch size.")
    evidence = rule.get("evidence")
    if not isinstance(evidence, list):
        raise ValueError("Scaling evidence must be a list of source fields and quotes.")

    fields = _evidence_fields(recipe, index, ingredient)
    references = []
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("A scaling rule cites invalid evidence.")
        path, quote = item.get("path"), item.get("quote")
        if not isinstance(path, str) or not isinstance(quote, str) or not quote.strip():
            raise ValueError("A scaling rule cites invalid evidence.")
        quote = _restore_quote(fields.get(path), quote)
        if quote is None:
            raise ValueError("A scaling rule must quote verified text from a related recipe field.")
        reference = {"path": path, "quote": quote}
        if reference not in references:
            references.append(reference)

    quantity, unit = ingredient["quantity"]["value"], ingredient["unit"]["value"]
    amount_paths = {f"/ingredients/{index}/quantity", f"/ingredients/{index}/unit"}
    if any(ingredient[key]["status"] == "needs_review" for key in ("name", "quantity", "unit")):
        mode, reason = "review", "Resolve the source ingredient or measure before scaling."
    elif any(path.startswith("/steps/") and path.endswith(("/instruction", "/condition"))
             and field["status"] == "needs_review" for path, field in fields.items()):
        mode, reason = "review", "Resolve the linked preparation instruction or condition before scaling."
    elif not quantity or scale_quantity(quantity, unit, Fraction(1)) is None:
        if mode != "review":
            mode, reason = "review", "The source has no supported numeric amount to scale."
    elif (mode == "review" and ingredient["quantity"]["status"] == "reviewed"
          and all(item["path"] in amount_paths for item in references)):
        # The numeric baseline and its unit alone give no evidence of a special use.
        mode, reason = "proportional", _REVIEWED_AMOUNT_REASON
    elif mode == "fixed" and not _supports_ingredient_mode(fields, references, index, ingredient, _FIXED_AMOUNT):
        mode, reason = "review", "Check the amount. The recipe does not support a fixed quantity."
    elif mode == "per_batch" and not any(
        _confirms_batch_size(item["quote"], _evidence_source(fields[item["path"]], item["quote"]), batch_servings)
        for item in references
    ):
        mode, reason = "review", "Check the amount. The recipe does not establish the proposed servings per batch."
    elif mode == "per_batch" and not _supports_ingredient_mode(
        fields, references, index, ingredient, _PER_BATCH_AMOUNT
    ):
        mode, reason = "review", "Check the amount. The recipe does not establish how much this ingredient uses per batch."

    return {
        "ingredient_id": ingredient["id"], "mode": mode, "reason": reason.strip(),
        "batch_servings": batch_servings if mode == "per_batch" else None, "evidence": references,
    }


def validate_scaling_plan(recipe: dict, proposal: dict) -> dict:
    """Require a single anchored rule for every ingredient in recipe order."""
    if not isinstance(proposal, dict) or not isinstance(proposal.get("rules"), list):
        raise ValueError("A scaling plan must contain ingredient rules.")

    ingredients = {item["id"]: (index, item) for index, item in enumerate(recipe["ingredients"])}
    rules = {}
    for rule in proposal["rules"]:
        ingredient_id = rule.get("ingredient_id") if isinstance(rule, dict) else None
        if not isinstance(ingredient_id, str) or ingredient_id not in ingredients or ingredient_id in rules:
            raise ValueError("A scaling plan must specify each existing ingredient exactly once.")
        index, ingredient = ingredients[ingredient_id]
        rules[ingredient_id] = _normalize_rule(recipe, index, ingredient, rule)
    if ingredients.keys() != rules.keys():
        raise ValueError("A scaling plan must specify each existing ingredient exactly once.")

    return _plan(recipe, [rules[ingredient_id] for ingredient_id in ingredients])


def recover_scaling_plan(recipe: dict, proposals: list[dict]) -> dict:
    """Keep validated provider rules after retry and hold unsupported amounts."""
    ingredients = {item["id"]: (index, item) for index, item in enumerate(recipe["ingredients"])}
    recovered = {}
    failures = {}
    for proposal in proposals:
        candidates = {}
        for rule in proposal["rules"]:
            ingredient_id = rule.get("ingredient_id") if isinstance(rule, dict) else None
            if isinstance(ingredient_id, str) and ingredient_id in ingredients:
                candidates.setdefault(ingredient_id, []).append(rule)
        for ingredient_id, (index, ingredient) in ingredients.items():
            rules = candidates.get(ingredient_id, [])
            if not rules:
                failures[ingredient_id] = "The model omitted this ingredient. Confirm how its amount should scale."
            elif len(rules) != 1:
                failures[ingredient_id] = "The model proposed multiple rules for this ingredient. Confirm how its amount should scale."
            else:
                try:
                    recovered[ingredient_id] = _normalize_rule(recipe, index, ingredient, rules[0])
                except ValueError as error:
                    failures[ingredient_id] = f"{error} Confirm how this ingredient should scale."

    held = [ingredient_id for ingredient_id in ingredients if ingredient_id not in recovered]
    for ingredient_id in held:
        recovered[ingredient_id] = _review_rule(
            ingredient_id, failures.get(ingredient_id, "No scaling rule was available. Confirm how this amount should scale."),
        )
    plan = _plan(recipe, [recovered[ingredient_id] for ingredient_id in ingredients])
    if held:
        plan["recovered_ingredients"] = held
    return plan


def _review_rule(ingredient_id: str, reason: str) -> dict:
    return {"ingredient_id": ingredient_id, "mode": "review", "batch_servings": None, "evidence": [], "reason": reason}


def rebind_scaling_plan(previous_recipe: dict, recipe: dict, plan: dict | None) -> dict | None:
    """Reuse classifications when the reviewed recipe has the same scaling inputs."""
    if not scaling_plan_matches(previous_recipe, plan):
        return None
    if scaling_plan_matches(recipe, plan):
        return plan
    relevant = (set(previous_recipe) | set(recipe)) - {"title"}
    # An entered baseline is used by the arithmetic. It is not evidence for
    # a per-batch yield and cannot change rules cited against the source.
    if (previous_recipe["servings"]["status"] == "missing" and previous_recipe["servings"]["value"] is None
            and recipe["servings"]["status"] == "reviewed"):
        relevant.discard("servings")
    if not all(same_recipe_inputs(previous_recipe.get(key), recipe.get(key)) for key in relevant):
        return None
    try:
        revised = validate_scaling_plan(recipe, plan)
        if revised["rules"] != plan["rules"]:
            return None
        verification = plan.get("verification")
        if isinstance(verification, dict) and verification.get("status") == "completed":
            if validate_scaling_plan(recipe, verification)["rules"] != verification["rules"]:
                return None
    except ValueError:
        return None
    return {**plan, **revised}


def rebase_scaling_plan(previous_recipe: dict, recipe: dict, plan: dict | None) -> dict | None:
    """Keep unchanged classifications when the reviewed recipe cannot be reclassified."""
    if not scaling_plan_matches(previous_recipe, plan):
        return None
    if scaling_plan_matches(recipe, plan):
        return plan
    before = {item["id"]: (index, item) for index, item in enumerate(previous_recipe["ingredients"])}
    unchanged = set()
    for index, item in enumerate(recipe["ingredients"]):
        ingredient_id = item["id"]
        if ingredient_id not in before:
            continue
        previous_index, previous_item = before[ingredient_id]
        if (_evidence_fields(previous_recipe, previous_index, previous_item) == _evidence_fields(recipe, index, item)
                and _linked_steps(previous_recipe, ingredient_id) == _linked_steps(recipe, ingredient_id)):
            unchanged.add(ingredient_id)

    def rebase_rules(proposal: dict) -> list[dict]:
        previous_rules = {rule["ingredient_id"]: rule for rule in validate_scaling_plan(previous_recipe, proposal)["rules"]}
        return [
            previous_rules[item["id"]] if item["id"] in unchanged else _review_rule(
                item["id"], "The recipe changed. Confirm how this ingredient should scale.",
            ) for item in recipe["ingredients"]
        ]

    try:
        updated = _plan(recipe, rebase_rules(plan))
    except ValueError:
        return None
    def track_held(previous: dict, current: dict):
        previously_held = previous.get("recovered_ingredients") or []
        held = [item["id"] for item in recipe["ingredients"] if item["id"] not in unchanged or item["id"] in previously_held]
        if held:
            current["recovered_ingredients"] = held

    track_held(plan, updated)
    verification = plan.get("verification")
    if isinstance(verification, dict) and verification.get("status") == "completed":
        try:
            updated["verification"] = {**verification, "rules": rebase_rules(verification)}
        except ValueError:
            pass
        else:
            track_held(verification, updated["verification"])
    return updated


def _linked_steps(recipe: dict, ingredient_id: str) -> list[dict]:
    return [step for step in recipe["steps"] if ingredient_id in step["ingredient_ids"]]


def _plan(recipe: dict, rules: list[dict]) -> dict:
    return {
        "status": "needs_review" if any(rule["mode"] == "review" for rule in rules) else "ready",
        "recipe_fingerprint": _fingerprint(recipe),
        "rules": rules,
    }


def ingredient_scale_factor(rule: dict, original_servings: int, target_servings: int) -> Fraction | None:
    """Apply a validated rule. None means that the source amount needs review."""
    if original_servings <= 0 or target_servings <= 0:
        raise ValueError("Serving counts must be positive integers.")
    mode = rule["mode"]
    if mode == "proportional":
        return Fraction(target_servings, original_servings)
    if mode == "fixed":
        return Fraction(1)
    if mode == "per_batch" and rule["batch_servings"] == original_servings:
        return Fraction((target_servings + original_servings - 1) // original_servings)
    return None


def verified_scaling_rules(recipe: dict, plan: dict) -> tuple[dict, dict]:
    """Read Gemini's separately classified rules from the saved plan."""
    verification = plan.get("verification")
    unavailable = {"provider": "gemini", "status": "unavailable", "material_conflicts": []}
    if not isinstance(verification, dict):
        return {**unavailable, "reason": "No Gemini verification is stored for this plan."}, {}
    if verification.get("status") != "completed":
        reason = verification.get("reason")
        return {**unavailable, "reason": reason if isinstance(reason, str) else "Gemini verification is unavailable."}, {}
    try:
        rules = validate_scaling_plan(recipe, verification)["rules"]
    except ValueError:
        return {**unavailable, "reason": "The stored Gemini rules failed validation."}, {}
    return (
        {"provider": "gemini", "status": "completed", "material_conflicts": []},
        {rule["ingredient_id"]: rule for rule in rules},
    )


def _ingredient_outcome(ingredient: dict, rule: dict, original_servings: int, target_servings: int) -> dict:
    quantity, unit = (ingredient[key]["value"] for key in ("quantity", "unit"))
    multiplier = ingredient_scale_factor(rule, original_servings, target_servings)
    outcome = {"quantity": quantity, "unit": unit, "batches": None}
    if multiplier is None or quantity is None:
        return outcome
    amount = scale_quantity(quantity, unit, multiplier)
    if amount is not None:
        outcome["quantity"], outcome["unit"] = amount
    if rule["mode"] == "per_batch":
        outcome.update(batches=int(multiplier), per_batch_quantity=quantity, per_batch_unit=unit)
    return outcome


def compare_scaling_outcomes(
    ingredient: dict, luna_rule: dict, gemini_rule: dict, original_servings: int, target_servings: int,
) -> dict:
    """Compare calculated amounts and required batches at the requested yield."""
    luna = _ingredient_outcome(ingredient, luna_rule, original_servings, target_servings)
    gemini = _ingredient_outcome(ingredient, gemini_rule, original_servings, target_servings)
    same_amount = (luna["quantity"], luna["unit"]) == (gemini["quantity"], gemini["unit"])
    if not same_amount and luna["quantity"] is not None and gemini["quantity"] is not None:
        same_amount = quantity_matches(luna["quantity"], luna["unit"], gemini["quantity"], gemini["unit"])
    same_batches = (luna["batches"] or 1) == (gemini["batches"] or 1)
    return {
        "luna_rule": luna_rule,
        "gemini_rule": gemini_rule,
        "luna_outcome": luna,
        "gemini_outcome": gemini,
        "materially_different": not (same_amount and same_batches),
    }
