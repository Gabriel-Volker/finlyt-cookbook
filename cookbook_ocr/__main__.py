import argparse
import json
import os
import sys
from pathlib import Path

from .adaptation import adapt_recipe
from .pipeline import run_pipeline
from .providers import GeminiClient, OpenAIClient, ProviderError


def load_api_keys() -> tuple[str, str]:
    env_file = Path(__file__).resolve().parents[1] / ".env"
    values = {}
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, separator, value = line.partition("=")
            if separator:
                values[name.strip()] = value.strip()

    keys = []
    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
        value = os.environ.get(name, "").strip() or values.get(name, "")
        if not value:
            raise ValueError(f"Set {name} in the environment or in the project's .env.")
        keys.append(value)
    return keys[0], keys[1]


def positive_servings(value: str) -> int:
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Enter a positive whole number of servings.") from None
    if count < 1:
        raise argparse.ArgumentTypeError("Enter a positive whole number of servings.")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract or adapt a recipe.")
    parser.add_argument("recipe", type=Path, help="PDF, PNG, JPEG or a saved JSON extraction report")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this file")
    parser.add_argument("--servings", type=positive_servings, help="Scale to this number of servings")
    parser.add_argument("--original-servings", type=positive_servings, help="Provide the original serving count")
    parser.add_argument("--vegan", action="store_true", help="Apply vegan ingredient and cooking rules")
    args = parser.parse_args()
    if args.original_servings is not None and args.servings is None:
        parser.error("--original-servings requires --servings.")

    try:
        if args.output and args.output.resolve() == args.recipe.resolve():
            raise ValueError("Use a different path for the JSON report and the input file.")
        if args.recipe.suffix.lower() == ".json":
            report = json.loads(args.recipe.read_text(encoding="utf-8-sig"))
            if not isinstance(report, dict) or not isinstance(report.get("recipe"), dict):
                raise ValueError("The JSON input must be an extraction report containing a recipe object.")
        else:
            document = args.recipe.read_bytes()
            if not document:
                raise ValueError("The recipe file is empty.")
            gemini_key, openai_key = load_api_keys()
            report = run_pipeline(
                document, args.recipe.name, GeminiClient(gemini_key), OpenAIClient(openai_key)
            )
        report.pop("adaptation", None)
        if args.servings is not None or args.vegan:
            try:
                report["adaptation"] = adapt_recipe(
                    report["recipe"], servings=args.servings,
                    original_servings=args.original_servings, vegan=args.vegan,
                )
            except (KeyError, TypeError, AttributeError):
                raise ValueError("The extraction report contains an invalid recipe object.") from None
        result = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(result, encoding="utf-8")
        else:
            print(result, end="")
    except (OSError, ValueError, ProviderError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    review_count = len(report.get("comparison", {}).get("review_items", []))
    if review_count:
        print(f"Human review required for {review_count} transcription item(s).", file=sys.stderr)
    if report.get("extraction_issues") or (report.get("status") == "needs_review" and not review_count):
        print("Human review required for unresolved recipe fields.", file=sys.stderr)
    if report.get("adaptation", {}).get("status") == "needs_review":
        print("Human review required for the recipe adaptation.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
