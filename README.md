# Finlyt Cookbook Challenge

The OCR command sends the same recipe to Gemini 3.1 Flash-Lite and GPT-6 Luna in parallel. It then sends both text transcriptions to GPT-6 Luna under the labels A and B. The [comparison prompt](prompts/compare_transcriptions.system.txt) and [JSON schema](prompts/compare_transcriptions.schema.json) define the review report.

Use Python 3.10 or later. Copy `.env.example` to `.env` in the project root and fill in the two `KEY=value` entries. You can also set those variables in the process environment, which takes precedence. Keep the supplied `Recipe.pdf` locally in the project root or pass its full path.

```powershell
Copy-Item .env.example .env
python -m cookbook_ocr Recipe.pdf --output outputs/recipe-review.json
```

The command accepts PDF, PNG, and JPEG. Omit `--output` to write JSON to standard output.

The `recipe` object contains the title, servings, ingredients, utensils, and ordered steps. Ingredients have quantities, units, preparation notes, and any explicitly stated optionality. Steps reference ingredient and utensil IDs and preserve stated durations and conditions.

Each extracted field carries a value, status, and evidence with its transcription label, page index when identifiable, and verbatim quote. A `missing` field has a null value. When a flagged discrepancy overlaps an extracted field, the field has status `needs_review` and a null value, with the evidence and one-based review item indices retained. Review items for passages outside the recipe, such as the cookbook attribution, remain visible in `comparison.review_items`.

The CLI restores source whitespace in extraction quotes when it can match every other character exactly. It retries comparison or extraction once if that stage returns invalid evidence. Any extracted field still lacking valid evidence is withheld and identified in `extraction_issues` by its path within `recipe`.

When the report status is `needs_review`, inspect the flagged fields, `extraction_issues`, and each entry in `comparison.review_items`. Each comparison entry includes a page index when available, the reason, and the excerpts from both readings. The command checks that each excerpt occurs verbatim in its assigned transcription. Equivalent quantities and formatting are listed under `equivalent_variations`.

The OCR providers receive fixed system instructions separately from the attachment. The comparison and extraction calls receive the transcriptions as source data in a user message. They use strict output schemas and run without tools. The CLI verifies excerpts and step references before it writes a report. These checks reduce the risk of document instructions affecting the output. Review the original page when a value is in doubt.

## Adjust the recipe

The same command accepts a saved extraction report and adapts it locally without API keys. You can also pass the adaptation options directly with a PDF or image.

```powershell
python -m cookbook_ocr outputs/recipe-review.json --servings 6 --original-servings 4 --output outputs/recipe-6.json
python -m cookbook_ocr outputs/recipe-review.json --vegan --output outputs/recipe-vegan.json
python -m cookbook_ocr outputs/recipe-review.json --servings 6 --original-servings 4 --vegan --output outputs/recipe-vegan-6.json
```

Omit `--original-servings` if the recipe states a single positive whole number of servings. The Sauerbraten gives no yield. The examples use four only as a user-provided baseline for the calculation.

Scaling uses exact fractions for numeric measures and ranges. It converts between g and kg or mL and L when doing so makes a measure easier to use. Household units retain their scale and volume stays separate from mass. Unstated quantities remain empty. Vague measures, ambiguous separators, and conflicting step measures can require review. Steps are updated where a measure matches the linked ingredient or a portioning instruction matches the original yield. Cooking times and temperatures are kept during scaling.

Vegan adaptation runs after scaling. Python rules cover a limited set of ingredient names and cooking methods in German, English, and Portuguese. For Sauerbraten, the rules substitute seitan, smoked tofu, vegan butter and vegan-labelled wine, and adapt marinating, browning and making the sauce. Known animal-derived ingredients without a matching rule are flagged for review. Check ingredient labels and any unsupported terms before relying on the vegan result.

The original `recipe` and OCR evidence remain in the report. Read the result in `adaptation.recipe`, the before and after values and reasons in `adaptation.changes`, and any open questions in `adaptation.review_items`. Adapting a report again starts from its original `recipe`.

Run the local tests with `python -m unittest discover -s tests -v`.
