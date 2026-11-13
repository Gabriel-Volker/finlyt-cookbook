import argparse
import json
import os
import sys
from pathlib import Path

from .adaptation import adapt_recipe
from .pipeline import plan_vegan_recipe, refresh_scaling_plans, run_pipeline
from .providers import GeminiClient, OpenAIClient, ProviderError
from .review import review_recipe
from .scaling_plan import scaling_plan_matches
from .vegan import unavailable_vegan_plan, vegan_plan_matches, vegan_plan_usable
from .vegan_verification import verify_vegan_plan


def _load_api_keys(*names: str) -> tuple[str, ...]:
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
    for name in names:
        value = os.environ.get(name, "").strip() or values.get(name, "")
        if not value:
            raise ValueError(f"Set {name} in the environment or in the project's .env.")
        keys.append(value)
    return tuple(keys)


def load_api_keys() -> tuple[str, str]:
    gemini, openai = _load_api_keys("GEMINI_API_KEY", "OPENAI_API_KEY")
    return gemini, openai


def load_openai_key() -> str:
    return _load_api_keys("OPENAI_API_KEY")[0]


def optional_gemini_client() -> GeminiClient | None:
    try:
        return GeminiClient(_load_api_keys("GEMINI_API_KEY")[0])
    except (OSError, ValueError):
        return None


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
    parser.add_argument("--vegan", action="store_true", help="Apply the Luna vegan plan")
    args = parser.parse_args()
    if args.original_servings is not None and args.servings is None:
        parser.error("--original-servings requires --servings.")

    blocked_plan = None
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
        recipe = report["recipe"]
        if "review" in report:
            review = report["review"]
            if not isinstance(review, dict):
                raise ValueError("The review must contain the decisions made on the original recipe.")
            report["review"] = review_recipe(recipe, review.get("decisions"))
            recipe = report["review"]["recipe"]
        if args.servings is not None or args.vegan:
            try:
                plan = report.get("vegan_plan")
                if args.vegan:
                    if not vegan_plan_matches(recipe, plan) and args.recipe.suffix.lower() == ".json":
                        try:
                            client = OpenAIClient(load_openai_key())
                        except (OSError, ValueError):
                            plan = unavailable_vegan_plan(
                                recipe, "provider_unavailable", "Set OPENAI_API_KEY to prepare a current vegan plan.",
                            )
                        else:
                            plan = plan_vegan_recipe(client, recipe, report.get("extraction_issues"))
                    if vegan_plan_matches(recipe, plan) and plan["status"] == "ready":
                        if not vegan_plan_usable(recipe, plan):
                            plan = verify_vegan_plan(optional_gemini_client(), recipe, plan)
                    if not vegan_plan_matches(recipe, plan):
                        plan = unavailable_vegan_plan(
                            recipe, "verification_unavailable", "No current verified vegan plan is available.",
                        )
                    report["vegan_plan"] = plan
                    if not vegan_plan_usable(recipe, plan):
                        blocked_plan = plan
                scaling = report.get("scaling_plan")
                if (blocked_plan is None and args.servings is not None and recipe["ingredients"]
                        and args.recipe.suffix.lower() == ".json"):
                    variant = "vegan" if args.vegan else "original"
                    source = adapt_recipe(recipe, vegan=True, vegan_plan=plan)["recipe"] if args.vegan else recipe
                    stored = scaling if isinstance(scaling, dict) else {}
                    if not scaling_plan_matches(source, stored.get(variant)):
                        scaling = report["scaling_plan"] = {
                            **stored, **refresh_scaling_plans(
                                OpenAIClient(load_openai_key()), recipe, plan if args.vegan else None,
                                stored, gemini=optional_gemini_client(),
                            ),
                        }
                if blocked_plan is None:
                    report["adaptation"] = adapt_recipe(
                        recipe, servings=args.servings,
                        original_servings=args.original_servings, vegan=args.vegan,
                        vegan_plan=plan, scaling_plan=scaling,
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

    if blocked_plan is not None:
        print(f"Vegan adaptation {blocked_plan['status']} ({blocked_plan['reason_code']}). "
              f"{blocked_plan['reason']}", file=sys.stderr)
        return 1

    review_count = len(report.get("comparison", {}).get("review_items", []))
    review = report.get("review")
    if review_count:
        message = (f"Original comparison flagged {review_count} transcription item(s)." if review
                   else f"Human review required for {review_count} transcription item(s).")
        print(message, file=sys.stderr)
    unresolved_fields = (
        review["unresolved_paths"] if review else
        report.get("extraction_issues") or (report.get("status") == "needs_review" and not review_count)
    )
    if unresolved_fields:
        print("Human review required for unresolved recipe fields.", file=sys.stderr)
    if report.get("adaptation", {}).get("status") == "needs_review":
        print("Human review required for the recipe adaptation.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
