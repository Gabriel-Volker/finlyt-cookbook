import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .adaptation import adapt_recipe
from .extraction import PAGE_MARKER, UNREADABLE, build_recipe, has_unresolved_fields
from .providers import GeminiClient, OpenAIClient, ProviderError
from .quality import assess_blur
from .raster import prepare_pdf
from .scaling_plan import scaling_plan_matches, validate_scaling_plan
from .vegan import validate_vegan_plan, vegan_plan_matches


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


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


def validate_comparison(result: dict, a: str, b: str) -> None:
    """Require each citation to appear literally in its own transcription and page."""
    source_pages = []
    for source in (a, b):
        markers = list(PAGE_MARKER.finditer(source))
        page_ends = [marker.start() for marker in markers[1:]] + [len(source)]
        source_pages.append({
            marker.group(1): source[marker.end():next_start]
            for marker, next_start in zip(markers, page_ends)
        })

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


def compare_transcriptions(
    openai: OpenAIClient, prompt: str, schema: dict, a: str, b: str
) -> dict:
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
    return result


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


def plan_vegan_recipe(openai: OpenAIClient, recipe: dict) -> dict:
    """Prepare and validate a recipe-specific plan before Adapt uses it."""
    if not recipe["ingredients"] or not recipe["steps"]:
        return {"status": "unavailable", "reason": "Vegan planning needs extracted ingredients and steps."}

    def omit_evidence(value):
        if isinstance(value, dict):
            return {key: omit_evidence(item) for key, item in value.items()
                    if key not in {"evidence", "review_item_ids"}}
        if isinstance(value, list):
            return [omit_evidence(item) for item in value]
        return value

    prompt = (PROMPTS_DIR / "plan_vegan.system.txt").read_text(encoding="utf-8")
    schema = json.loads((PROMPTS_DIR / "plan_vegan.schema.json").read_text(encoding="utf-8"))
    try:
        candidate = _structured_response(
            openai, prompt, schema, "vegan_plan", {"recipe": omit_evidence(recipe)},
            max_output_tokens=8192,
        )
        return validate_vegan_plan(recipe, candidate)
    except ProviderError:
        return {"status": "unavailable", "reason": "Luna could not prepare a vegan plan."}
    except ValueError:
        return {"status": "unavailable", "reason": "Luna's vegan plan failed validation."}


def plan_recipe_scaling(openai: OpenAIClient, recipe: dict) -> dict:
    if not recipe["ingredients"]:
        return {"status": "unavailable", "reason": "Scaling needs extracted ingredients."}
    prompt = (PROMPTS_DIR / "plan_scaling.system.txt").read_text(encoding="utf-8")
    schema = json.loads((PROMPTS_DIR / "plan_scaling.schema.json").read_text(encoding="utf-8"))
    try:
        proposal = _structured_response(openai, prompt, schema, "scaling_plan", {"recipe": recipe},
                                        max_output_tokens=8192)
        return validate_scaling_plan(recipe, proposal)
    except ProviderError:
        return {"status": "unavailable", "reason": "Luna could not prepare a scaling plan."}
    except ValueError:
        return {"status": "unavailable", "reason": "Luna's scaling plan failed validation."}


def refresh_scaling_plans(openai: OpenAIClient | None, recipe: dict, vegan_plan: dict | None,
                          stored: dict | None = None) -> dict:
    """Reuse matching plans and classify the original and vegan recipes independently."""
    stored = stored if isinstance(stored, dict) else {}
    variants = {"original": recipe}
    if vegan_plan_matches(recipe, vegan_plan):
        variants["vegan"] = adapt_recipe(recipe, vegan=True, vegan_plan=vegan_plan)["recipe"]

    def plan_variant(variant, variant_recipe):
        existing = stored.get(variant)
        if scaling_plan_matches(variant_recipe, existing):
            return existing
        if openai is None:
            return {"status": "unavailable", "reason": "Set OPENAI_API_KEY to prepare the scaling plan."}
        return plan_recipe_scaling(openai, variant_recipe)

    if variants.get("vegan") == recipe:
        plan = plan_variant("original", recipe)
        return {"original": plan, "vegan": plan}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {variant: executor.submit(plan_variant, variant, source) for variant, source in variants.items()}
        return {variant: future.result() for variant, future in futures.items()}


def run_pipeline(
    document: bytes, filename: str, gemini: GeminiClient, openai: OpenAIClient
) -> dict:
    transcription_prompt = (PROMPTS_DIR / "transcribe_recipe.txt").read_text(encoding="utf-8")
    comparison_prompt = (PROMPTS_DIR / "compare_transcriptions.system.txt").read_text(
        encoding="utf-8"
    )
    schema = json.loads(
        (PROMPTS_DIR / "compare_transcriptions.schema.json").read_text(encoding="utf-8")
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        gemini_reading = executor.submit(gemini.transcribe, document, filename, transcription_prompt)
        prepared = prepare_pdf(document) if Path(filename).suffix.lower() == ".pdf" else None
        options = {"prepared_pdf": prepared} if prepared is not None else {}
        luna_reading = executor.submit(openai.transcribe, document, filename, transcription_prompt, **options)
        a, b = gemini_reading.result(), luna_reading.result()

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
    transcriptions = {"A": a, "B": b}
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
    if no_readable_text and not (recipe["ingredients"] and recipe["steps"]):
        extraction_issues.append({
            "field": "/recipe", "reason": "Neither reader recovered usable text. Check the original or try a sharper copy.",
        })
    needs_review = comparison["review_items"] or extraction_issues or has_unresolved_fields(recipe)
    vegan_plan = plan_vegan_recipe(openai, recipe)
    scaling_plan = refresh_scaling_plans(openai, recipe, vegan_plan)
    return {
        "status": "needs_review" if needs_review else "no_discrepancies",
        "transcriptions": transcriptions,
        "comparison": comparison,
        "recipe": recipe,
        "extraction_issues": extraction_issues,
        "vegan_plan": vegan_plan,
        "scaling_plan": scaling_plan,
    }
