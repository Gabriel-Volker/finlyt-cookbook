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

In **Review**, select a flagged field, inspect its evidence, and enter the value you verified on the source page. Use **Confirm** to accept a reading or **Correct** to supply a corrected value. The report retains the original `recipe`, OCR texts, and comparison. The `review` section contains the reviewed recipe, the decisions, and paths still awaiting review. **Adapt** uses the reviewed values. Saving a changed review refreshes the vegan plan against those values. The [activity diagram](docs/activity-diagram.md) shows these steps.

For a local run, use Python 3.10 or later and the same `.env`. Environment variables take precedence. Pass the full input path if the file is outside the project.

```powershell
python -m pip install -r requirements.txt
python -m cookbook_ocr Recipe.pdf --output outputs/recipe-review.json
```

The command accepts PDF, PNG, and JPEG. Password-protected PDFs need to be unlocked first. Luna falls back to direct PDF input if a file exceeds the local raster budget of 32 pages or 36 MB of PNG data. Omit `--output` to write JSON to standard output.

The import rejects a severely blurred file only when the image check finds strong blur on every page and both OCR readers explicitly report that they recovered no text on any page. Any recovered text, a PDF text layer, or incomplete evidence keeps the file out of the blur rejection path. The web app asks for a sharper copy, and the CLI exits with an error before writing a recipe report. This conservative check can let some unreadable files through.

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

Omit `--original-servings` if the recipe states a single positive whole number of servings or the reviewer supplied one. The Sauerbraten gives no yield. The examples use four as a user-provided baseline for the calculation.

After extraction, Luna and Gemini classify how each ingredient scales independently for the original recipe and for any vegan variant. Python compares the resulting quantities and required batches at the requested serving count. If they differ, the affected amount stays at its source value for review. Equivalent results require no additional review. Saving a correction refreshes the plans for the revised recipe. Each rule has a reason and can cite source text. The export keeps the plans so the CLI and Adapt can reuse them for different serving counts without another model call. For a saved report without a current plan, the CLI uses `OPENAI_API_KEY` to generate one and `GEMINI_API_KEY`, when available, for verification.

Python uses exact fractions for proportional quantities, including mixed fractions and ranges. A fixed rule keeps the original amount. An evidenced per-batch rule uses a whole number of batches, keeps the quantity per batch in the method, and shows the total in the ingredient list. A review rule retains the source amount and asks for a decision. A recipe that gives no usable quantity stays unquantified. The app validates cited text and asks for review when it cannot establish fixed or per-batch behavior. Adjusted steps use matched ingredient amounts, including split amounts that reconcile to the original total. Ambiguous allocations and capacity remain review items. Cooking times, temperatures and vessel dimensions stay as written.

After extraction, Luna proposes vegan substitutions and related changes to the instructions. Gemini checks that proposal against the ingredient's cooking function, quantity and updated steps. It flags concrete issues with source citations and accepts workable alternatives. The `vegan_plan` records changes, reasons and verification findings. Python validates the plan and preserves numeric measures. Adapt applies the stored plan before scaling so Python can match the new ingredient names in the steps. If a verifier is unavailable, the report records that status and keeps the validated Luna plan available. A matching plan in an exported report can be replayed offline. If vegan planning fails during import, the extraction and serving adjustment remain available.

The original `recipe` and OCR evidence remain in the report. Read the result in `adaptation.recipe`, the before and after values and reasons in `adaptation.changes`, and any open questions in `adaptation.review_items`. Adapting an exported report reapplies the saved review decisions to the original `recipe` before running the adaptation.

Run the local tests with `python -m unittest discover -s tests -v`.

To run the tests in Docker from PowerShell, build the image and mount the test directory.

```powershell
docker build -t finlyt-cookbook .
docker run --rm --network none --entrypoint python --mount "type=bind,source=$PWD/tests,target=/app/tests,readonly" finlyt-cookbook -m unittest discover -s tests -v
```
