import argparse
import json
import os
import sys
from pathlib import Path

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two OCR readings of a recipe.")
    parser.add_argument("recipe", type=Path, help="PDF, PNG or JPEG recipe")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this file")
    args = parser.parse_args()

    try:
        if args.output and args.output.resolve() == args.recipe.resolve():
            raise ValueError("Use a different path for the JSON report and the input file.")
        document = args.recipe.read_bytes()
        if not document:
            raise ValueError("The recipe file is empty.")
        gemini_key, openai_key = load_api_keys()
        report = run_pipeline(
            document, args.recipe.name, GeminiClient(gemini_key), OpenAIClient(openai_key)
        )
        result = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(result, encoding="utf-8")
        else:
            print(result, end="")
    except (OSError, ValueError, ProviderError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    review_count = len(report["comparison"]["review_items"])
    if review_count:
        print(f"Human review required for {review_count} item(s).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
