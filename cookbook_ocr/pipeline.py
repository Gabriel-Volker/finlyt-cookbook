import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .adaptation import adapt_recipe
from .extraction import PAGE_MARKER, UNREADABLE, build_recipe, flag_ocr_uncertainty, has_unresolved_fields
from .providers import GeminiClient, OpenAIClient, ProviderError
from .quality import assess_blur
from .quantities import QUANTITY_PATTERN, known_units, quantity_matches
from .raster import prepare_pdf
from .recipe_text import decode_recipe_text, page_transcription
from .scaling_plan import rebind_scaling_plan, recover_scaling_plan, scaling_plan_matches, validate_scaling_plan
from .vegan import unavailable_vegan_plan, validate_vegan_plan, vegan_plan_usable, vegan_source_review
from .vegan_verification import verify_vegan_plan


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
LOGGER = logging.getLogger(__name__)
_MEASURE_UNITS = "|".join(re.escape(unit) for unit in sorted(known_units(), key=len, reverse=True))
_COMPARISON_MEASURE = re.compile(
    rf"(?<![\d/⁄∕])(?P<quantity>{QUANTITY_PATTERN})(?![\d/⁄∕])"
    rf"(?:[ \t]*(?P<unit>{_MEASURE_UNITS})(?!\w))?", re.IGNORECASE,
)


class UnreadableRecipeError(ValueError):
    """The source appears severely blurred and neither reader recovered any text."""


def _all_pages_unreadable(transcription: str, count: int) -> bool:
    markers = list(PAGE_MARKER.finditer(transcription))
    if not markers or [marker[1] for marker in markers] != [str(page) for page in range(1, count + 1)]:
        return False
    if transcription[:markers[0].start()].strip():
        return False
    ends = [marker.start() for marker in markers[1:]] + [len(transcription)]
    for marker, end in zip(markers, ends):
        body = transcription[marker.end():end].strip()
        if not body or UNREADABLE.sub("", body).strip():
            return False
    return True


def _transcription_pages(source: str) -> dict:
    markers = list(PAGE_MARKER.finditer(source))
    ends = [marker.start() for marker in markers[1:]] + [len(source)]
    return {marker[1]: source[marker.end():end] for marker, end in zip(markers, ends)}


def validate_comparison(result: dict, a: str, b: str) -> None:
    """Require each citation to appear literally in its own transcription and page."""
    source_pages = [_transcription_pages(source) for source in (a, b)]

    for items in (result["review_items"], result["equivalent_variations"]):
        for item in items:
            page = item["page"]
            if page is not None and not re.fullmatch(r"[1-9][0-9]*", page):
                raise ValueError("Comparison returned an invalid page index.")
            excerpts = (item["a_excerpt"], item["b_excerpt"])
            if excerpts == (None, None):
                raise ValueError("Comparison item has no evidence in either transcription.")
            for label, excerpt, source, pages in zip(("A", "B"), excerpts, (a, b), source_pages):
                if excerpt is None:
                    continue
                if not excerpt.strip() or excerpt not in source:
                    raise ValueError(f"Comparison returned a non-verbatim excerpt for {label}.")
                if page is not None and pages and excerpt not in pages.get(page, ""):
                    raise ValueError(f"Comparison cited the wrong page for {label}.")


def _cuts_a_measure(source: str, excerpt: str, page: str | None) -> bool:
    pages = _transcription_pages(source)
    if page is not None and pages:
        source = pages.get(page, "")
    # The quoted amount must include any whole part and the complete numeric token.
    measures = [match.span("quantity") for match in _COMPARISON_MEASURE.finditer(source)]
    return any(
        start < boundary < end
        for quote in re.finditer(re.escape(excerpt), source)
        for boundary in quote.span()
        for start, end in measures
    )


def _add_quantity_conflict(reviews: list, page: str | None, a: str, b: str) -> None:
    if not any(
        item["page"] in (None, page) and item["a_excerpt"] and item["b_excerpt"]
        and (item["a_excerpt"] in a or a in item["a_excerpt"])
        and (item["b_excerpt"] in b or b in item["b_excerpt"])
        for item in reviews
    ):
        reviews.append({
            "page": page, "section": "Quantities", "kind": "conflict", "a_excerpt": a, "b_excerpt": b,
            "reason": "The cited measurements could not be verified as numerically equivalent.",
        })


def _numbered_lines(source: str) -> dict:
    lines = {}
    for line in source.splitlines():
        line = line.strip()
        measure = list(_COMPARISON_MEASURE.finditer(line))
        if len(measure) != 1 or UNREADABLE.search(line):
            continue
        match = measure[0]
        before, after = line[:match.start()], line[match.end():]
        if not any(char.isalpha() for char in before + after):
            continue
        context = tuple(" ".join(part.casefold().split()) for part in (before, after))
        lines.setdefault(context, []).append((line, match["quantity"], match["unit"]))
    return lines


def _check_numeric_equivalences(result: dict, a_source: str, b_source: str) -> dict:
    equivalents = []
    reviews = list(result["review_items"])
    for item in result["equivalent_variations"]:
        a, b = ([(match["quantity"], match["unit"]) for match in _COMPARISON_MEASURE.finditer(item[key])]
                for key in ("a_excerpt", "b_excerpt"))
        equal = len(a) == len(b) and all(
            first == second or quantity_matches(*first, *second) for first, second in zip(a, b)
        )
        complete = item["kind"] == "formatting" if not a else not any(
            _cuts_a_measure(source, item[key], item["page"])
            for source, key in ((a_source, "a_excerpt"), (b_source, "b_excerpt"))
        )
        if equal and complete and (item["kind"] != "unit_conversion" or all(unit for _, unit in a + b)):
            equivalents.append(item)
        else:
            _add_quantity_conflict(reviews, item["page"], item["a_excerpt"], item["b_excerpt"])

    a_pages, b_pages = _transcription_pages(a_source), _transcription_pages(b_source)
    for page in a_pages:
        if page not in b_pages:
            continue
        a_lines, b_lines = _numbered_lines(a_pages[page]), _numbered_lines(b_pages[page])
        for context, a_entries in a_lines.items():
            b_entries = b_lines.get(context, ())
            if len(a_entries) != 1 or len(b_entries) != 1:
                continue
            a_line, *a_measure = a_entries[0]
            b_line, *b_measure = b_entries[0]
            if a_measure != b_measure and not quantity_matches(*a_measure, *b_measure):
                _add_quantity_conflict(reviews, page, a_line, b_line)
    return {**result, "review_items": reviews, "equivalent_variations": equivalents}


def compare_transcriptions(
    openai: OpenAIClient, prompt: str, schema: dict, a: str, b: str
) -> dict:
    result = {"review_items": [], "equivalent_variations": []}
    if a.strip() != b.strip():
        transcriptions = {"A": a, "B": b}
        result = _structured_response(openai, prompt, schema, "transcription_comparison", transcriptions)
        try:
            validate_comparison(result, a, b)
        except ValueError:
            retry_prompt = (
                prompt + "\n\nThe previous response failed evidence validation. "
                "Use the shortest exact excerpts that show the issue. "
                "Keep spaces and line breaks unchanged inside each excerpt. "
                "Verify the page for each excerpt, or use null when the readings place it on different pages."
            )
            result = _structured_response(
                openai, retry_prompt, schema, "transcription_comparison", transcriptions
            )
            validate_comparison(result, a, b)
    result = flag_ocr_uncertainty(result, _transcription_pages(a) or {None: a}, _transcription_pages(b) or {None: b})
    return _check_numeric_equivalences(result, a, b)


def _structured_response(
    openai: OpenAIClient, prompt: str, schema: dict, name: str, data: dict,
    **options,
) -> dict:
    response = openai.respond(
        input=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": name,
                "strict": True,
                "schema": schema,
            }
        },
        **options,
    )
    return json.loads(response)


def plan_vegan_recipe(openai: OpenAIClient, recipe: dict, extraction_issues: list | None = None) -> dict:
    """Prepare and validate a recipe-specific plan before Adapt uses it."""
    incomplete = [
        {"path": item["field"], "reason": item["reason"]}
        for item in extraction_issues or []
        if item.get("field") in {"/recipe", "/ingredients", "/steps"}
    ]
    if incomplete:
        return unavailable_vegan_plan(
            recipe, "insufficient_information", "The extraction is incomplete. Review the source before adapting it.",
            incomplete,
        )
    if not recipe["ingredients"] or not recipe["steps"]:
        return unavailable_vegan_plan(
            recipe, "insufficient_information", "Vegan planning needs extracted ingredients and steps.",
        )
    if blocked := vegan_source_review(recipe):
        return blocked

    def omit_evidence(value):
        if isinstance(value, dict):
            return {key: omit_evidence(item) for key, item in value.items()
                    if key not in {"evidence", "review_item_ids"}}
        if isinstance(value, list):
            return [omit_evidence(item) for item in value]
        return value

    prompt = (PROMPTS_DIR / "plan_vegan.system.txt").read_text(encoding="utf-8")
    schema = json.loads((PROMPTS_DIR / "plan_vegan.schema.json").read_text(encoding="utf-8"))
    data = {"recipe": omit_evidence(recipe)}
    first_plan = None
    for attempt in range(2):
        try:
            candidate = _structured_response(openai, prompt, schema, "vegan_plan", data, max_output_tokens=8192)
            plan = validate_vegan_plan(recipe, candidate)
            if candidate["status"] == "ready" and plan["status"] != "ready":
                first_plan = plan
                issues = " ".join(f"{item['path']}: {item['reason']}" for item in plan["review_items"])
                raise ValueError(issues)
            if (attempt == 0 and candidate.get("status") == "needs_review"
                    and _needs_timing_reconsideration(recipe, plan)):
                first_plan = plan
                prompt += (
                    "\n\nReconsider the recipe's animal-specific timing. A stated timing is not missing information. "
                    "Changing the preparation method for a plant ingredient is within scope. Try a known vegan "
                    "method that preserves the culinary purpose of those steps, with a qualitative cue in place of "
                    "an inapplicable animal-specific time. Update the linked condition or duration and the step "
                    "together. Leave timings for unchanged preparations intact. If there is still no viable "
                    "technique, explain the remaining culinary constraint."
                )
                continue
            return plan
        except ProviderError:
            return first_plan or unavailable_vegan_plan(recipe, "provider_unavailable", "Luna could not prepare a vegan plan.")
        except ValueError as error:
            detail = "The response was not valid JSON." if isinstance(error, json.JSONDecodeError) else str(error)
            LOGGER.warning("Vegan plan validation failed on attempt %d: %s", attempt + 1, detail)
            if attempt == 0:
                prompt += (
                    f"\n\nThe previous response did not validate. {detail} "
                    "Use exact recipe paths and copy the before values verbatim. "
                    "For a blocked verdict, return no edits and identify what prevents choosing a suitable substitute. "
                    "Leave missing amounts untouched without blocking otherwise suitable replacements."
                )
    return first_plan or unavailable_vegan_plan(
        recipe, "invalid_output", "The vegan proposal could not be validated. Retry adaptation plans.",
    )


def _needs_timing_reconsideration(recipe: dict, plan: dict) -> bool:
    for item in plan["review_items"]:
        match = re.fullmatch(r"/steps/([0-9]+)/(condition|duration)", item["path"])
        if match:
            index, field = match.groups()
            source = recipe["steps"][int(index)][field]
            if source["status"] in {"stated", "reviewed"} and source["value"]:
                return True
    return False


def plan_recipe_scaling(openai: OpenAIClient, recipe: dict) -> dict:
    if not recipe["ingredients"]:
        return {"status": "unavailable", "reason_code": "insufficient_information",
                "reason": "Scaling needs extracted ingredients."}
    return _classify_scaling(
        recipe, "Luna", lambda prompt, schema, data: _structured_response(
            openai, prompt, schema, "scaling_plan", data, max_output_tokens=8192,
        ),
    )


def _classify_scaling(recipe: dict, provider: str, respond) -> dict:
    prompt = (PROMPTS_DIR / "plan_scaling.system.txt").read_text(encoding="utf-8")
    schema = json.loads((PROMPTS_DIR / "plan_scaling.schema.json").read_text(encoding="utf-8"))
    proposals = []
    for attempt in range(2):
        try:
            proposal = respond(prompt, schema, {"recipe": recipe})
            if isinstance(proposal, dict) and isinstance(proposal.get("rules"), list):
                proposals.append(proposal)
            return validate_scaling_plan(recipe, proposal)
        except ProviderError:
            if proposals:
                break
            return {"status": "unavailable", "reason_code": "provider_unavailable",
                    "reason": f"{provider} could not prepare a scaling plan."}
        except ValueError as error:
            LOGGER.warning("%s scaling plan validation failed on attempt %d: %s", provider, attempt + 1, error)
            if attempt == 0:
                prompt += (
                    "\n\nThe previous result did not validate. Return each ingredient ID exactly once. "
                    "Check that each quotation occurs literally inside one permitted field or its evidence. "
                    "Use separate citations for separate fields and select review if a rule lacks evidence."
                )
    if proposals:
        return recover_scaling_plan(recipe, proposals)
    return {"status": "unavailable", "reason_code": "invalid_output",
            "reason": f"{provider} could not return a usable scaling plan. Try refreshing the adaptation options."}


def _verify_scaling(gemini: GeminiClient, recipe: dict) -> dict:
    checked = _classify_scaling(recipe, "Gemini", gemini.respond_structured)
    if checked["status"] == "unavailable":
        return {"provider": "gemini", **checked}
    result = {"provider": "gemini", "status": "completed", "rules": checked["rules"]}
    if checked.get("recovered_ingredients"):
        result["recovered_ingredients"] = checked["recovered_ingredients"]
    return result


def refresh_scaling_plans(
    openai: OpenAIClient | None, recipe: dict, vegan_plan: dict | None,
    stored: dict | None = None, *, gemini: GeminiClient | None = None,
    retry_verification: bool = False, retry_recovered: bool = False,
) -> dict:
    """Reuse matching plans and classify the original and vegan recipes independently."""
    stored = stored if isinstance(stored, dict) else {}
    variants = {"original": recipe}
    if vegan_plan_usable(recipe, vegan_plan):
        variants["vegan"] = adapt_recipe(recipe, vegan=True, vegan_plan=vegan_plan)["recipe"]

    same_variant = "vegan" in variants and all(
        variants["vegan"].get(key) == recipe.get(key)
        for key in (variants["vegan"].keys() | recipe.keys()) - {"title"}
    )
    if same_variant:
        vegan_recipe = variants.pop("vegan")
        if not scaling_plan_matches(recipe, stored.get("original")):
            if shared := rebind_scaling_plan(vegan_recipe, recipe, stored.get("vegan")):
                stored = {**stored, "original": shared}
    plans = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        generations = {}
        verifications = {}
        for variant, source in variants.items():
            existing = stored.get(variant)
            if scaling_plan_matches(source, existing):
                plans[variant] = existing
                verification = existing.get("verification")
                if retry_recovered and existing.get("recovered_ingredients") and openai is not None:
                    generations[variant] = executor.submit(plan_recipe_scaling, openai, source)
                if not retry_verification or (
                    isinstance(verification, dict) and verification.get("status") == "completed"
                    and not (retry_recovered and verification.get("recovered_ingredients"))
                ):
                    continue
            elif openai is not None and source["ingredients"]:
                generations[variant] = executor.submit(plan_recipe_scaling, openai, source)
            else:
                reason_code, reason = (
                    ("provider_unavailable", "Set OPENAI_API_KEY to prepare the scaling plan.")
                    if source["ingredients"] else
                    ("insufficient_information", "Scaling needs extracted ingredients.")
                )
                plans[variant] = {"status": "unavailable", "reason_code": reason_code, "reason": reason}
                continue
            if gemini is not None:
                verifications[variant] = executor.submit(_verify_scaling, gemini, source)

        for variant, future in generations.items():
            result = future.result()
            existing = plans.get(variant)
            if not existing or scaling_plan_matches(variants[variant], result):
                verification = existing.get("verification") if existing else None
                if verification:
                    result = {**result, "verification": verification}
                plans[variant] = result
        for variant, future in verifications.items():
            verification = future.result()
            if scaling_plan_matches(variants[variant], plans[variant]):
                prior = plans[variant].get("verification")
                if not prior or prior.get("status") != "completed" or verification["status"] == "completed":
                    plans[variant] = {**plans[variant], "verification": verification}
    if same_variant:
        plans["vegan"] = rebind_scaling_plan(recipe, vegan_recipe, plans["original"]) or plans["original"]
    return plans


def _read_document(document: bytes, filename: str, gemini: GeminiClient, openai: OpenAIClient) -> tuple[dict, dict, bool]:
    suffix = Path(filename).suffix.lower()
    prepared = prepare_pdf(document, prefer_native_text=True) if suffix == ".pdf" else None
    if suffix == ".txt" or (prepared is not None and prepared.native_pages is not None):
        native = [decode_recipe_text(document)] if suffix == ".txt" else prepared.native_pages
        reading = page_transcription(native)
        return {"A": reading}, {"review_items": [], "equivalent_variations": []}, False

    transcription_prompt = (PROMPTS_DIR / "transcribe_recipe.txt").read_text(encoding="utf-8")
    comparison_prompt = (PROMPTS_DIR / "compare_transcriptions.system.txt").read_text(
        encoding="utf-8"
    )
    schema = json.loads(
        (PROMPTS_DIR / "compare_transcriptions.schema.json").read_text(encoding="utf-8")
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        gemini_reading = executor.submit(gemini.transcribe, document, filename, transcription_prompt)
        options = {"prepared_pdf": prepared} if prepared is not None else {}
        openai_reading = executor.submit(openai.transcribe, document, filename, transcription_prompt, **options)
        a, b = gemini_reading.result(), openai_reading.result()

    pages = prepared.pages if prepared is not None else [document]
    no_readable_text = bool(
        pages and _all_pages_unreadable(a, len(pages)) and _all_pages_unreadable(b, len(pages))
    )
    if no_readable_text and (prepared is None or not prepared.has_text_layer):
        assessments = assess_blur(pages)
        if len(assessments) == len(pages) and all(item.suspected for item in assessments):
            raise UnreadableRecipeError(
                "The document is too blurred to read. Upload a sharper photo or scan with the text in focus."
            )

    comparison = compare_transcriptions(openai, comparison_prompt, schema, a, b)
    return {"A": a, "B": b}, comparison, no_readable_text


def run_pipeline(
    document: bytes, filename: str, gemini: GeminiClient, openai: OpenAIClient
) -> dict:
    transcriptions, comparison, no_readable_text = _read_document(document, filename, gemini, openai)
    extraction_prompt = (PROMPTS_DIR / "extract_recipe.system.txt").read_text(encoding="utf-8")
    extraction_schema = json.loads(
        (PROMPTS_DIR / "extract_recipe.schema.json").read_text(encoding="utf-8")
    )
    extraction_issues = []
    for attempt in range(2):
        candidate = _structured_response(
            openai, extraction_prompt, extraction_schema, "recipe_extraction", transcriptions,
            max_output_tokens=12288,
        )
        try:
            recipe = build_recipe(
                candidate, transcriptions, comparison["review_items"],
                extraction_issues=extraction_issues if attempt else None,
            )
            break
        except ValueError as error:
            if attempt:
                raise
            extraction_prompt += (
                f"\n\nThe previous extraction failed validation. {error} "
                "Copy exact source text and quotes, and ensure all step references exist."
            )
    if not (any(item["name"]["value"] for item in recipe["ingredients"])
            and any(step["instruction"]["value"] for step in recipe["steps"])):
        reason = ("Neither reader recovered usable text. Check the original or try a sharper copy."
                  if no_readable_text else "The extraction did not recover both ingredients and preparation steps. Check the source.")
        extraction_issues.append({
            "field": "/recipe", "reason": reason,
        })
    needs_review = comparison["review_items"] or extraction_issues or has_unresolved_fields(recipe)
    vegan_plan = plan_vegan_recipe(openai, recipe, extraction_issues)
    if vegan_plan["status"] == "ready":
        vegan_plan = verify_vegan_plan(gemini, recipe, vegan_plan)
    scaling_plan = refresh_scaling_plans(openai, recipe, vegan_plan, gemini=gemini)
    return {
        "status": "needs_review" if needs_review else "no_discrepancies",
        "transcriptions": transcriptions,
        "comparison": comparison,
        "recipe": recipe,
        "extraction_issues": extraction_issues,
        "vegan_plan": vegan_plan,
        "scaling_plan": scaling_plan,
    }
