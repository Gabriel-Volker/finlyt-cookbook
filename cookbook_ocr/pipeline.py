import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .providers import GeminiClient, OpenAIClient


PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


def validate_comparison(result: dict, a: str, b: str) -> None:
    """Require each citation to appear literally in its own transcription."""
    for items in (result["review_items"], result["equivalent_variations"]):
        for item in items:
            page = item["page"]
            if page is not None and not re.fullmatch(r"[1-9][0-9]*", page):
                raise ValueError("Comparison returned an invalid page index.")
            excerpts = (item["a_excerpt"], item["b_excerpt"])
            if excerpts == (None, None):
                raise ValueError("Comparison item has no evidence in either transcription.")
            for label, excerpt, source in zip(("A", "B"), excerpts, (a, b)):
                if excerpt is not None and (not excerpt.strip() or excerpt not in source):
                    raise ValueError(f"Comparison returned a non-verbatim excerpt for {label}.")


def compare_transcriptions(
    openai: OpenAIClient, prompt: str, schema: dict, a: str, b: str
) -> dict:
    response = openai.respond(
        input=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"A": a, "B": b}, ensure_ascii=False)},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "transcription_comparison",
                "strict": True,
                "schema": schema,
            }
        },
    )
    result = json.loads(response)
    validate_comparison(result, a, b)
    return result


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
        luna_reading = executor.submit(openai.transcribe, document, filename, transcription_prompt)
        a, b = gemini_reading.result(), luna_reading.result()

    comparison = compare_transcriptions(openai, comparison_prompt, schema, a, b)
    return {
        "status": "needs_review" if comparison["review_items"] else "no_discrepancies",
        "transcriptions": {"A": a, "B": b},
        "comparison": comparison,
    }
