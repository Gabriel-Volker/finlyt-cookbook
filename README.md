# Finlyt Cookbook Challenge

Cookbook turns a recipe into a reviewable ingredient list and method, then applies serving changes and optional vegan substitutions. Each accepted source value links to a reading and quote. The web workspace and CLI share the same Python pipeline.

## Run the workspace

Run these three commands from the repository root with Docker running. The bundled example works with the blank `.env` file. For a live import, fill in `GEMINI_API_KEY` and `OPENAI_API_KEY` before starting the container. If you already have a `.env`, start at the build command.

```powershell
Copy-Item .env.example .env
docker build -t finlyt-cookbook .
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 --entrypoint python finlyt-cookbook -m cookbook_ocr.web --host 0.0.0.0
```

Select **Try example** in the workspace. Use **Review** to inspect a source excerpt. In **Adapt**, turn on **Make vegan**, select six servings, and export the result. For the live test, place your local copy of `Recipe.pdf` at the repository root or upload it from its existing location. `Recipe.pdf` and `.env` are excluded from Git and the Docker build. [Local workspace](http://127.0.0.1:8000/)

Run locally with Python 3.10 or later after installing the dependencies. The CLI reads the same `.env`, with environment variables taking precedence. Pass the full input path if the file is outside the project.

```powershell
python -m pip install -r requirements.txt
python -m cookbook_ocr Recipe.pdf --output outputs/recipe-review.json
```

Use `python -m cookbook_ocr.web` to run the browser workspace without Docker. Omit the CLI's `--output` option to write the report to standard output. To pipe UTF-8 JSON on Windows, run Python with `-X utf8`.

## Architecture and trade-offs

| Stage | Challenge and design decision | Alternative considered |
| --- | --- | --- |
| **Ingest** | The photographed Sauerbraten pages have no text layer. The recipe continues across the page break, and the drawings contain labels. Gemini 3.1 Flash-Lite reads the source PDF and GPT-6.1 Terra reads lossless rendered pages. Preserve physical page indices and the readable header and labels for review. One earlier Luna reading produced `Bratenstand` where the manuscript says `Bratenfond`. A disagreement can affect the associated ingredient and sauce step. | Conventional OCR missed complete quantity–ingredient pairs and much of the preparation on this scan. Rasterizing for the OpenAI reader offered a useful second input route. The recorded Gemini benchmark favored its direct PDF input on cost. |
| **Extract** | Luna compares differing readings under the anonymous labels A and B, then extracts a structured recipe. Python checks each source quote and its page, checks ingredient–measure associations, validates references and deduplicates equivalent detections. For example, four belongs to the onions in Sauerbraten. The recipe gives no meat weight or original yield, so those values stay unknown. | A single prompt that reads the photo and emits final JSON would be shorter to implement. Separate readings and extraction make it possible to tell where a disagreement entered the report. Guessing a yield would turn the scaling factor into an unmarked assumption. |
| **Manipulate** | Luna proposes a vegan variant, Gemini checks the full recipe and method, and Python applies the accepted changes. The system then scales the selected variant with stored rules and exact fractions. Replacing the roast can affect both the marinade and sauce, so an ingredient swap alone cannot preserve the method. Batch size and vessel capacity can warrant review. | Blind proportional scaling would also change fixed or per-batch amounts. A list of preset vegan replacements would miss the culinary function of each ingredient. An LLM rewrite on every serving change would make arithmetic and replay harder to verify. |
| **Output** | Review shows original evidence, disagreements, and any human corrections. Adapt shows ingredients, instructions, and why each value changed or needs review. Prepared translations reuse the currently calculated numbers. Export stores the source, review decisions, and selected output. | A polished recipe card alone would hide missing quantities and the source of a correction. Translation is a display layer because scaling from rewritten text can lose the original quantity and evidence. |

The web service keeps up to eight recent successful reports in memory and reuses a report for an identical upload. The collaboration log records where I steered Codex, reviewed code, found failures, and changed the approach. [Activity diagram](docs/activity-diagram.md) · [Known problems](PROBLEMS.md) · [AI collaboration log](AI_LOG.md) · [Decision log](DECISION_LOG.md)

## Review and adaptation

In **Review**, click a value, step, or utensil to open its editor with the source evidence. The field selector shows only fields for that item. Use **Confirm value** to accept a reading or **Save correction** to supply a corrected value. You can also edit which ingredients and utensils a step uses. The report retains the original `recipe`, readings, and comparison. The `review` section contains the reviewed recipe, the decisions, and paths still awaiting review. Changes save immediately. Opening **Adapt** refreshes plans for the reviewed values while the recipe remains available. If a plan identifies a field that needs attention, use the link in Adapt to open that field.

**English output** is checked by default on the Import screen. Uncheck it to display the original recipe language. The choice applies to Review, Adapt, and JSON export. Import prepares both output languages in one batch, including the available vegan recipe. If a field fails validation, a retry reuses the validated fields from that batch. Switching languages reuses the prepared outputs. Serving changes run the scaling calculation in Python and insert the current measures into the validated translations without calling a model. New or corrected text is prepared in both languages when the recipe or plans change. The web export stores the selected result in `output.recipe`, the display notes in `output.texts`, and the choice in `output.language`. Review edits the source values, and the original recipe, OCR excerpts, and adaptation data retain their source wording. The app checks translated numbers, explicit negations, and common limit phrases in English, German, and Portuguese. The bundled English example works offline.

The web workspace accepts PDF, PNG, JPEG, and UTF-8 plain text (`.txt`) up to 14 MB. Plain text bypasses visual OCR. Digital PDFs of up to 32 pages do the same when every page has usable native text and no displayed raster images. The pipeline uses an LLM to extract the structured recipe and checks its citations against that text. Mixed PDFs and scans use the visual route, where page layout matters. On that route, PyMuPDF renders the pages and Pillow encodes them. Terra receives the original PDF if the file exceeds the local raster budget of 32 pages or 36 MB of PNG data. Unlock password-protected PDFs before import.

The import rejects a severely blurred file only when the image check finds strong blur on every page and both OCR readers explicitly report that they recovered no text on any page. Any recovered text, a PDF text layer, or incomplete evidence keeps the file out of the blur rejection path. The web app asks for a sharper copy, and the CLI exits with an error before writing a recipe report. This conservative check can let some unreadable files through.

The `recipe` object contains the title, servings, ingredients, utensils, and ordered steps. Ingredients have quantities, units, preparation notes, and any explicitly stated optionality. Steps reference ingredient and utensil IDs and preserve stated durations and conditions. The extraction may suggest a tool implied by an action in a linked step. The report marks it `inferred` and cites the action as evidence. Missing yields stay unknown until the cook supplies a baseline.

Each extracted field carries a value, status, and evidence with its transcription label, page index when identifiable, and verbatim quote. A `missing` field has a null value. When a flagged discrepancy overlaps an extracted field, the field has status `needs_review` and a null value, with the evidence and one-based review item indices retained. Review items for passages outside the recipe, such as the cookbook attribution, remain visible in `comparison.review_items`.

The CLI restores source whitespace in extraction quotes when it can match every other character exactly. It checks that quantities and units refer to the same ingredient occurrence and that cited numeric equivalents include the complete amount. It retries comparison or extraction once if that stage returns invalid evidence. If validation still fails for a field, the report withholds that value and lists it in `extraction_issues`. The report also flags missing ingredients or steps when extraction yields an incomplete recipe.

When the report status is `needs_review`, inspect the flagged fields, `extraction_issues`, and each entry in `comparison.review_items`. Each comparison entry includes a page index when available, the reason, and the excerpts from both readings. The command checks that each excerpt occurs verbatim in its assigned transcription. Equivalent quantities and formatting are listed under `equivalent_variations`.

The OCR providers receive fixed system instructions separately from the attachment. The comparison and extraction calls receive the transcriptions as source data in a user message. They use strict output schemas and run without tools. The CLI verifies excerpts and step references before it writes a report. These checks reduce the risk of document instructions affecting the output. Review the original page when a value is in doubt.

## Adjust the recipe

The same command accepts a saved extraction report. A current plan in the report can be replayed without API keys. Use the bundled extraction report below or replace its path with your own. The adaptation options also work when importing a recipe.

```powershell
python -m cookbook_ocr examples/sauerbraten-extraction.json --servings 6 --original-servings 4 --output outputs/recipe-6.json
python -m cookbook_ocr examples/sauerbraten-extraction.json --vegan --output outputs/recipe-vegan.json
python -m cookbook_ocr examples/sauerbraten-extraction.json --servings 6 --original-servings 4 --vegan --output outputs/recipe-vegan-6.json
```

Omit `--original-servings` if the recipe states a single positive whole number of servings or the reviewer supplied one. The Sauerbraten gives no yield. The examples use four as a user-provided baseline for the calculation.

After extraction, Luna and Gemini classify how each ingredient scales independently for the original recipe and for any vegan variant. Python compares the resulting quantities and required batches at the requested serving count. If they differ, the affected amount stays at its reviewed value until someone checks it. Equivalent results require no additional review. When some rules fail validation, the valid rules can still be used and the remaining ingredients are marked for review. A quantity correction that conflicts with a recognizable amount in a linked step stays unscaled until the step is checked. Each rule has a reason and can cite source text. The export keeps the plans so the CLI and Adapt can reuse them for different serving counts without another model call. For a saved report without a current plan, the CLI uses `OPENAI_API_KEY` to generate one and `GEMINI_API_KEY`, when available, for verification.

Python uses exact fractions for proportional quantities, including mixed fractions and ranges. A fixed rule keeps the original amount. An evidenced per-batch rule uses a whole number of batches, keeps the quantity per batch in the method, and shows the total in the ingredient list. A review rule retains the source amount and asks for a decision. A recipe that gives no usable quantity stays unquantified. The app checks the source for explicit amounts that apply once or per batch, even when the two models agree on proportional scaling. Adjusted steps use matched ingredient amounts, including split amounts that reconcile to the original total. Recognized but unresolved measures in linked steps are flagged together with ambiguous allocations and capacity. Cooking times, temperatures and vessel dimensions stay as written.

After extraction, Luna proposes identifiable vegan foods that perform the required cooking function and updates the relevant steps. The choice of ingredient can be verified even when the quantity is missing. In that case, Adapt retains the missing value and asks the cook to confirm the measure. Gemini must explicitly approve every proposed ingredient replacement. It reads the final ingredient list and preparation in context, including unchanged steps. It checks for remaining animal ingredients, fabricated products, incompatible substitutions, and changes to measures or techniques. Python validates field references, checks numeric measures, and requires the exact approvals before applying the plan. The two model checks reduce the risk of a bad substitution. They do not verify products against an external catalog.

When the evidence is insufficient, the models abstain. OCR uses `[unreadable]` for unreadable text. Comparison marks uncertain passages `uncertain` and preserves the excerpts. Extraction preserves absent values as `missing` and withholds disputed values as `needs_review`. Scaling uses `review` for rules it cannot resolve and keeps the source amount. An unavailable plan includes a `reason_code` and a human-readable `reason`.

For a vegan plan, `needs_review` indicates missing or ambiguous information that prevents establishing a viable replacement or technique. `unavailable` covers cases such as `no_viable_substitute`, a rejected proposal, or an unavailable verifier. Both states have empty `changes`. Adapt disables the vegan option and explains the reason while keeping original scaling available. The API returns HTTP 422 with the status and reason. The CLI saves the report with the blocked plan, omits `adaptation`, and exits with code 1. If the planner returns invalid output, the application retries once and logs the validation error.

The original `recipe` and OCR evidence remain in the report. Read the result in `adaptation.recipe`, the before and after values and reasons in `adaptation.changes`, and any open questions in `adaptation.review_items`. Adapting an exported report reapplies the saved review decisions to the original `recipe` before running the adaptation.

## Reliability, cost, and latency

**Hallucination and trust.** A syntactically valid JSON response can still be wrong. Literal evidence helps detect invented text. The quantity checks tie numbers to specific ingredient occurrences, and Python can restore an omitted unreadable marker to the review list. Two readers can still misread the same word. Source quotes establish traceability, while statuses such as `stated`, `missing`, and `needs_review` describe how to treat a field. These statuses are evidence and review signals. The application has no calibrated probability of correctness.

**Non-determinism and reproducibility.** OCR and judgments about preparation or substitutions can vary between live runs. The export records both readings, the validated recipes and plans, and human review decisions. Replay uses that snapshot and performs calculations in Python. Tests exercise exact fractions, metric units, split amounts, duplicates, evidence checks, and ambiguous fields using supplied provider responses. Live examples exercise the same pipeline with real model output.

**Validation without ground truth.** Agreement measures consistency and gives the reviewer places to inspect. To estimate accuracy, manually annotate a small set of handwritten scans, clean digital PDFs, and plain-text recipes across languages. Label each ingredient, amount, unit, source span, and preparation link, plus items a reviewer would withhold. Run each sample repeatedly and compare field accuracy, errors accepted without a flag, review rate, and stability across runs. Keep separate measurements for clean and poor inputs. The three Portuguese scans below broaden the sample across languages and layouts. This sample is too small for an accuracy estimate.

**Model and code responsibilities.** The models interpret handwriting, align differing readings, extract fields, and reason about the cooking function of substitutions or scaling rules. Python owns file routing, quote and schema validation, exact arithmetic, deterministic application of saved changes, and export. In production I would benchmark Claude Sonnet as the second visual reader, evaluate conventional OCR on printed scans, and move checks for explicit document invariants into code. In financial statements that includes validating units such as EUR and TEUR and reconciling totals before publishing an analysis.

| Measurement on the two-page Sauerbraten, 13 Nov 2026 | Time | Estimated API cost |
| --- | --- | --- |
| Earlier dual-reading, comparison, and extraction using Gemini and Luna | 67.54 s | USD 0.00467 with observed cache reads, or USD 0.00550 pricing those reads as cache writes |
| Separate Terra direct-PDF transcription benchmark | 18.09 s | USD 0.03789 for that single call |
| Current Terra-based full pipeline, including preparation of adaptation plans | About 4 min in one observed run | Full-run cost unmeasured |
| Local serving change after import | About 110 ms in a Chromium test | Zero model calls |

The historical costs describe different stages and model configurations. The current pipeline can also incur verification and translation costs. It batches bilingual fields into one request and reuses validated plans and translations. [Benchmark scope and token counts](AI_LOG.md)

## With another week

1. Build the annotated evaluation set above, then use reviewed failures as regression tests. Measure silent quantity and ingredient-link errors before adding more model calls.
2. Add page batching and resumable retries for large imports. Validate page coverage against the source on each route. Record wall time and provider token usage by stage, including retries and cache hits, to compare model and routing choices.
3. Strengthen links between ingredients, individual measures, and preparation clauses. Preserve uncertainty when translating or scaling a compound line, and make unresolved contradictions prominent in the cooking view and export.
4. Evaluate conservative inferred tools and yields against the annotations. Keep inferred suggestions distinct from statements in the source and require human acceptance before using them in a consequential calculation.

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
