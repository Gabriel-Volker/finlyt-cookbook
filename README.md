# Finlyt Cookbook Challenge

The OCR command sends the original PDF to Gemini 3.1 Flash-Lite and renders its pages for GPT-6 Luna in parallel. The Luna request contains the pages in order as lossless RGB PNGs. PyMuPDF renders each page and Pillow encodes it with PNG compression level 3. The command then sends both text transcriptions to GPT-6 Luna under the labels A and B. The [comparison prompt](prompts/compare_transcriptions.system.txt) and [JSON schema](prompts/compare_transcriptions.schema.json) define the review report.

Copy `.env.example` to `.env` and fill in the two `KEY=value` entries. Run these commands from the project root with Docker running and a local copy of `Recipe.pdf` in that directory.

```powershell
Copy-Item .env.example .env
docker build -t finlyt-cookbook .
New-Item -ItemType Directory -Force outputs | Out-Null
docker run --rm --env-file .env --mount "type=bind,source=$PWD/Recipe.pdf,target=/input/Recipe.pdf,readonly" --mount "type=bind,source=$PWD/outputs,target=/output" finlyt-cookbook /input/Recipe.pdf --output /output/recipe-review.json
```

The container reads the recipe and writes its JSON report through the bind mount. The build context includes only code, prompts, and dependency declarations.

## Recipe workspace

Start the web workspace in Docker and open `http://localhost:8000`.

```powershell
docker build -t finlyt-cookbook .
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 --entrypoint python finlyt-cookbook -m cookbook_ocr.web --host 0.0.0.0
```

To run it directly after installing the Python dependencies, use `python -m cookbook_ocr.web`. Upload a PDF, PNG, or JPEG up to 14 MB. Review the extracted ingredients and steps with their source excerpts, then adjust servings, apply vegan substitutions, and export the JSON report. **Try example** opens a bundled Sauerbraten example. The example and its adaptations work without API keys. Imported files and results are held for the current browser session.

In **Review**, click a value, step, or utensil to open its editor with the source evidence. The field selector shows only fields for that item. Use **Confirm value** to accept a reading or **Save correction** to supply a corrected value. You can also edit which ingredients and utensils a step uses. The report retains the original `recipe`, OCR texts, and comparison. The `review` section contains the reviewed recipe, the decisions, and paths still awaiting review. **Adapt** uses the reviewed values. Saving a correction refreshes the adaptation plans. The [activity diagram](docs/activity-diagram.md) shows these steps.

**Output in English** is checked by default across Review, Adapt, and JSON export. Uncheck it to use the original recipe language. The web export stores the selected result in `output.recipe`, the display notes in `output.texts`, and the choice in `output.language`. Review edits the source values, and the original recipe, OCR excerpts, and adaptation data retain their source wording. Luna translates the displayed recipe and explanations, with protected numeric measures checked before display or export. The bundled English example works offline.

For a local run, use Python 3.10 or later and the same `.env`. Environment variables take precedence. Pass the full input path if the file is outside the project.

```powershell
python -m pip install -r requirements.txt
python -m cookbook_ocr Recipe.pdf --output outputs/recipe-review.json
```

The command accepts PDF, PNG, and JPEG. Password-protected PDFs need to be unlocked first. Luna falls back to direct PDF input if a file exceeds the local raster budget of 32 pages or 36 MB of PNG data. Omit `--output` to write JSON to standard output.

The import rejects a severely blurred file only when the image check finds strong blur on every page and both OCR readers explicitly report that they recovered no text on any page. Any recovered text, a PDF text layer, or incomplete evidence keeps the file out of the blur rejection path. The web app asks for a sharper copy, and the CLI exits with an error before writing a recipe report. This conservative check can let some unreadable files through.

The `recipe` object contains the title, servings, ingredients, utensils, and ordered steps. Ingredients have quantities, units, preparation notes, and any explicitly stated optionality. Steps reference ingredient and utensil IDs and preserve stated durations and conditions.

Each extracted field carries a value, status, and evidence with its transcription label, page index when identifiable, and verbatim quote. A `missing` field has a null value. When a flagged discrepancy overlaps an extracted field, the field has status `needs_review` and a null value, with the evidence and one-based review item indices retained. Review items for passages outside the recipe, such as the cookbook attribution, remain visible in `comparison.review_items`.

The CLI restores source whitespace in extraction quotes when it can match every other character exactly. It checks that quantities and units refer to the same ingredient occurrence and that cited numeric equivalents include the complete amount. It retries comparison or extraction once if that stage returns invalid evidence. If validation still fails for a field, the report withholds that value and lists it in `extraction_issues`. The report also flags missing ingredients or steps when extraction yields an incomplete recipe.

When the report status is `needs_review`, inspect the flagged fields, `extraction_issues`, and each entry in `comparison.review_items`. Each comparison entry includes a page index when available, the reason, and the excerpts from both readings. The command checks that each excerpt occurs verbatim in its assigned transcription. Equivalent quantities and formatting are listed under `equivalent_variations`.

The OCR providers receive fixed system instructions separately from the attachment. The comparison and extraction calls receive the transcriptions as source data in a user message. They use strict output schemas and run without tools. The CLI verifies excerpts and step references before it writes a report. These checks reduce the risk of document instructions affecting the output. Review the original page when a value is in doubt.

## Adjust the recipe

The same command accepts a saved extraction report. A current plan in the report can be replayed without API keys. You can also pass the adaptation options directly with a PDF or image.

```powershell
python -m cookbook_ocr outputs/recipe-review.json --servings 6 --original-servings 4 --output outputs/recipe-6.json
python -m cookbook_ocr outputs/recipe-review.json --vegan --output outputs/recipe-vegan.json
python -m cookbook_ocr outputs/recipe-review.json --servings 6 --original-servings 4 --vegan --output outputs/recipe-vegan-6.json
```

Omit `--original-servings` if the recipe states a single positive whole number of servings or the reviewer supplied one. The Sauerbraten gives no yield. The examples use four as a user-provided baseline for the calculation.

After extraction, Luna and Gemini classify how each ingredient scales independently for the original recipe and for any vegan variant. Python compares the resulting quantities and required batches at the requested serving count. If they differ, the affected amount stays at its source value for review. Equivalent results require no additional review. Saving a correction refreshes the plans for the revised recipe. Each rule has a reason and can cite source text. The export keeps the plans so the CLI and Adapt can reuse them for different serving counts without another model call. For a saved report without a current plan, the CLI uses `OPENAI_API_KEY` to generate one and `GEMINI_API_KEY`, when available, for verification.

Python uses exact fractions for proportional quantities, including mixed fractions and ranges. A fixed rule keeps the original amount. An evidenced per-batch rule uses a whole number of batches, keeps the quantity per batch in the method, and shows the total in the ingredient list. A review rule retains the source amount and asks for a decision. A recipe that gives no usable quantity stays unquantified. The app checks the source for explicit amounts that apply once or per batch, even when the two models agree on proportional scaling. Adjusted steps use matched ingredient amounts, including split amounts that reconcile to the original total. Recognized but unresolved measures in linked steps are flagged together with ambiguous allocations and capacity. Cooking times, temperatures and vessel dimensions stay as written.

After extraction, Luna proposes identifiable vegan foods that perform the required cooking function and updates the relevant steps. The choice of ingredient can be verified even when the quantity is missing. In that case, Adapt retains the missing value and asks the cook to confirm the measure. Gemini must explicitly approve every proposed ingredient replacement. It checks the full variant for remaining animal ingredients, fabricated products, incompatible substitutions, and changes that require an unsupported measure or technique. Python validates the field references, checks the numeric measures, and requires the exact approvals before applying the plan. The two model checks reduce the risk of a bad substitution. They do not verify products against an external catalog.

When the evidence is insufficient, the models abstain. OCR uses `[unreadable]` for unreadable text. Comparison marks uncertain passages `uncertain` and preserves the excerpts. Extraction preserves absent values as `missing` and withholds disputed values as `needs_review`. Scaling uses `review` for rules it cannot resolve and keeps the source amount. An unavailable plan includes a `reason_code` and a human-readable `reason`.

For a vegan plan, `needs_review` indicates missing or ambiguous information that prevents establishing a viable replacement or technique. `unavailable` covers cases such as `no_viable_substitute`, a rejected proposal, or an unavailable verifier. Both states have empty `changes`. Adapt disables the vegan option and explains the reason while keeping original scaling available. The API returns HTTP 422 with the status and reason. The CLI saves the report with the blocked plan, omits `adaptation`, and exits with code 1. If the planner returns invalid output, the application retries once and logs the validation error.

The original `recipe` and OCR evidence remain in the report. Read the result in `adaptation.recipe`, the before and after values and reasons in `adaptation.changes`, and any open questions in `adaptation.review_items`. Adapting an exported report reapplies the saved review decisions to the original `recipe` before running the adaptation.

## Example outputs

The Sauerbraten examples use the saved extraction from `Recipe.pdf`. The source gives no serving count, so the six-serving run uses four as a demonstration baseline. The vegan results use the verified replacements. Missing amounts, including the roast quantity, remain unset and appear in the adaptation's review items.

| Recipe | Saved results |
| --- | --- |
| Sauerbraten | [Extraction](examples/sauerbraten-extraction.json), [six servings](examples/sauerbraten-6-servings.json), [vegan](examples/sauerbraten-vegan.json), [vegan for six](examples/sauerbraten-vegan-6-servings.json) |
| Torta de coco | [Extraction and scaling from 10 to 20 portions](examples/torta-de-coco.json) |
| Casadinhos | [Extraction and scaling from 55 to 110 pieces](examples/casadinhos.json) |
| Pé-de-moleque | [Extraction and scaling from 30 to 60 pieces](examples/pe-de-moleque.json) |

The three Portuguese scans use the recorded extractions and scaling rules from the live runs. For Casadinhos and Pé-de-moleque, the baseline was entered manually using the source yield in pieces. Their stored vegan proposals predate the current verification protocol, so these reports expose the original scaling and mark vegan adaptation as unavailable.

Run the local tests with `python -m unittest discover -s tests -v`.

To run the tests in Docker from PowerShell, build the image and mount the test directory.

```powershell
docker build -t finlyt-cookbook .
docker run --rm --network none --entrypoint python --mount "type=bind,source=$PWD/tests,target=/app/tests,readonly" finlyt-cookbook -m unittest discover -s tests -v
```
