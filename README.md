# Finlyt Cookbook Challenge

The OCR command sends the same recipe to Gemini 3.1 Flash-Lite and GPT-6 Luna in parallel. It then sends both text transcriptions to GPT-6 Luna under the labels A and B. The [comparison prompt](prompts/compare_transcriptions.system.txt) and [JSON schema](prompts/compare_transcriptions.schema.json) define the review report.

Use Python 3.10 or later. Copy `.env.example` to `.env` in the project root and fill in the two `KEY=value` entries. You can also set those variables in the process environment, which takes precedence. Keep the supplied `Recipe.pdf` locally in the project root or pass its full path.

```powershell
Copy-Item .env.example .env
python -m cookbook_ocr Recipe.pdf --output outputs/recipe-review.json
```

The command accepts PDF, PNG, and JPEG. Omit `--output` to write JSON to standard output.

When the report status is `needs_review`, inspect each entry in `comparison.review_items` against the source page. It includes a page index when available, the reason, and the corresponding excerpts from both readings. The command checks that each excerpt occurs verbatim in its assigned transcription. Equivalent quantities and formatting are listed under `equivalent_variations`. The `no_discrepancies` status means the comparator found no issues in the two readings. Human review is recorded as a pending action in the report.

Run the local tests with `python -m unittest discover -s tests -v`.
