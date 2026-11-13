# Decision log

Summaries of work with Codex and AI test results are in the [AI collaboration log](AI_LOG.md).

Times are local to São Paulo (UTC−03:00). Earlier decisions use the date when the exact time was not recorded.

## 2026-11-13 · Handwriting recognition

- **10:31–10:32** — Generated the German OCR test outputs with Docling. EasyOCR produced Markdown at 10:31, and Tesseract produced a second output at 10:32.
- **10:47** — Recorded the initial decision to prioritize handwriting recognition and evaluate ways to detect extraction errors.

After reading the challenge brief, I ran a quick Docling test with German OCR on the handwritten recipe. The output lost both half-liter measurements in the marinade and misread the eight-day timing. Based on that result, I set local OCR aside for this manuscript. Handwriting recognition falls outside the main use case I had in mind for Docling and the document processing work I did in SynapseFlux.

My first priority is to get the text recognition stage working. I plan to try a vision-capable LLM or a cloud service built for handwriting recognition. My initial idea is to send the same pages to two different recognition tools, then ask an LLM to compare their outputs and flag inconsistencies in quantities, units, times, and ingredient names. A disagreement would point me to a passage to check against the original page. This may catch more errors, at the cost of another recognition pass and a comparison call. I want to see which errors it catches before deciding whether the extra cost is worth it.

In a production pipeline, I would consider using Docling with OCR disabled for documents that already have a usable text layer. I am leaving that route for future work and focusing this project on the handwritten input first.

## 2026-11-13 · Structured extraction

Based on the [Gemini transcription test](AI_LOG.md#2026-11-13--gemini-transcription-test), Codex proposed using Gemini as the first reader for this manuscript. The next check is whether structured extraction preserves the same quantities, conditions, and missing values in JSON.

## 2026-11-13 · Compare two independent readings

Gabriel plans to run two vision-language models on the same source, then send both transcriptions to a text LLM for comparison. When the readings differ, the affected fields must be marked unreliable and require human review.

Codex proposed [Claude Haiku 5.5](https://platform.claude.com/docs/en/models/haiku-5-5/overview) as the second reader. It is from a different provider and can receive the same PDF and transcription instructions as Gemini. Its performance on this recipe is still to be tested.

Codex also proposed keeping each reading with its page reference and having the comparison model align ingredient quantities, units, and preparation conditions. Normalize equivalent units in code, flag material differences and one-sided omissions, and show both excerpts beside the source page for review. If both readings omit a value, keep it unknown. Agreement between the readings is evidence to consider alongside checks against the source.

A possible future extension is to send only the flagged regions to a more capable and expensive VLM for a third reading. Gabriel wants to document this option and defer its implementation.

## 2026-11-13 · GPT as a second reader and unreadable scans

Gabriel asked whether GPT could replace the proposed second VLM and use his existing ChatGPT subscription. Codex proposed testing GPT through a fresh ChatGPT or Codex session with the rendered pages and the same transcription prompt. The repository's automated pipeline could use a vision-capable GPT through the OpenAI Responses API. That route needs a Platform API key and API billing or [credits claimed from an eligible ChatGPT Pro or Business subscription](https://developers.openai.com/api/docs/guides/chatgpt-api-credits). GPT remains a candidate until it is tested on this recipe.

Gabriel proposed evaluating [Consensus Entropy OCR (Zhang et al., CVPR 2026)](https://tianyilt.github.io/consensus-entropy/) and automatically rejecting documents that are too blurred to read. The paper measures disagreement across independent transcriptions and uses a threshold to route uncertain cases to a stronger model. With two readings, disagreement provides a reason to review the source.

Codex proposed treating automatic rejection as an extension of the paper's routing idea. Assess readability per page and calibrate thresholds on clean and degraded scans. If blur makes essential fields unreadable, stop automatic processing and ask for a clearer scan. Use transcription disagreement to guide review of uncertain fields. Two models can agree on the same error, so evaluate blur and legibility with evidence from the page itself. This remains a proposal.

## 2026-11-13 11:42 · Production checks and demo scope

Gabriel would consider adding a conventional OCR engine as another independent reading in production. Comparing it with the VLM outputs could surface discrepancies for review.

For credit analysis, Gabriel would also add deterministic arithmetic checks on extracted balance sheets. For example, verify that total assets equal total liabilities plus equity after accounting for units and rounding. A failed check would send the relevant figures for review.

Both checks are outside the scope of the cookbook demo.

## 2026-11-13 13:29 · Review evidence from anonymous transcriptions

Gabriel wants the comparison LLM to receive both transcriptions without model identities. The [blind comparison test](AI_LOG.md#2026-11-13-132738132909--blind-transcription-comparison) used the labels A and B and repeated the test with the labels swapped.

Codex proposed requiring literal excerpts from each reading and validating those excerpts in code before showing them to a reviewer. Normalize equivalent quantities for comparison while preserving their original notation in the evidence. A reviewer can use the page index and quoted text to inspect the original document.

## 2026-11-13 13:45 · Models selected for OCR and comparison

Gabriel selected Gemini 3.1 Flash-Lite and GPT-6 Luna for independent transcriptions. GPT-6 Luna compares the anonymous text readings. Conflicts, omissions, and unreadable text go into the JSON report for human review, with page references where available and literal excerpts checked in code. The [implementation test](AI_LOG.md#2026-11-13-134528134556--implemented-ocr-and-review-pipeline) flagged a disagreement in the illustrated header.

## 2026-11-13 14:17 · Recipe fields and document instructions

Gabriel asked for a Python extraction stage that returns ingredients, quantities, utensils, steps, and servings in JSON, preserves their origin, and flags discrepancies. GPT-6 Luna extracts the recipe from the two anonymous transcriptions. Python verifies each quote against its assigned reading and derives the page index from that reading. It restores whitespace only when the other characters match exactly. Step references must resolve to existing ingredients or utensils.

An unstated field stays null. When a discrepancy affects a field, the report withholds the value and links the source evidence to its review item. If evidence fails validation, extraction gets one retry. Any field that still lacks a valid quote stays null and receives a review entry at its JSON path.

Both OCR providers receive the fixed prompt separately from the attachment. The comparison and extraction models receive the readings as untrusted source material, use strict schemas, and run without tools. Exact evidence supports inspection of the output. It cannot establish that two OCR readings are accurate when both make the same mistake.

## 2026-11-13 14:36 · Deterministic recipe changes

Gabriel chose to require an original serving count whenever the recipe does not give a single reliable yield. Codex used exact rational arithmetic and limited unit conversions to g/kg and mL/L. Quantities in steps are changed when they can be tied to the same ingredient measure. Cooking temperatures and times are independent of the serving factor. Household units are kept in their original system, and unquantified ingredients stay unquantified.

Apply scaling first and then vegan rules. The rules preserve ingredient IDs and update references and techniques in the steps. For Sauerbraten, that includes sautéing smoked tofu and removing the original meat-only marinating and resting times. The vegan vocabulary is limited. Recognized unsupported animal ingredients are sent for review. The adapted report records each changed field and keeps the original recipe and evidence for comparison.

## 2026-11-13 14:49 · Keep direct PDF input

Gabriel made pipeline rasterization conditional on cost savings on Gemini as well as a fast conversion. On this scan, Gemini used more input tokens for PNGs at default detail and the same number at low detail. The [benchmark](AI_LOG.md#2026-11-13-144829144924--gemini-pdf-and-png-comparison) records the result. Direct PDF remains the pipeline input.

## 2026-11-13 15:04 · Use rasterized pages for Luna

Gabriel chose a separate input format for each OCR reader. Gemini receives the original PDF. Luna receives lossless PNGs rendered in memory with PyMuPDF and encoded with Pillow at compression level 3. The local budget is 32 pages and 36 MB of PNG data. Above either limit, Luna receives the original PDF and the CLI reports the fallback. The Docker image packages the two rendering dependencies, with recipe files and credentials supplied at runtime.

Keep the scaling calculation deterministic. Recognized seasoning and vessel-dependent oil amounts retain the source measure and require review. The same rule applies to the amount in the step. The demo leaves adjustments for pan capacity, batch size, and ingredient amounts split across steps to the cook.

## 2026-11-13 15:10 · Mixed fractions, split measures, and vessel capacity

Gabriel asked to preserve mixed fractions, adjust ingredient amounts divided across steps, and flag cookware capacity when scaling. The extraction validator checks that a quoted measure includes the complete number. The comparison prompt treats equivalent notations as the same amount while requiring evidence for both readings.

Reuse one ingredient ID when the recipe divides a stated total between steps. Scale the written parts using exact arithmetic when compatible units reconcile to that total. Leave ambiguous allocations for review. For an increased yield, flag cookware when the larger batch may need a capacity check. Keep any stated vessel size as evidence for the review and leave vessel dimensions and cooking times as written.

## 2026-11-13 15:28 · Browser recipe workspace

Gabriel chose the Figma prototype and requested a blue accent. The web workspace follows its import, review, and adaptation views. A small Python HTTP server serves local assets and calls the existing extraction and adaptation functions. The workspace holds imported files and reports for the current browser session. The bundled example provides a way to explore review and adaptation without credentials.

## 2026-11-13 15:43 · Evaluate blur detection on scanned pages

Gabriel proposed using OpenCV to identify scans that are too blurred to read. Codex recommends trying Laplacian variance as a warning on consistently sized grayscale text regions from the existing rasterization. Combine that warning with unreadable passages, missing critical fields, and disagreements on amounts or units. Show the affected page and let the user review it or upload a clearer image. Evaluate any automatic rejection threshold on manually labeled scans across languages, handwriting, resolutions, contrast, noise, and blur types, and measure how many readable pages it would reject. [Consensus Entropy OCR](https://tianyilt.github.io/consensus-entropy/) remains a separate proposal for uncertainty in the transcriptions. The [AI log](AI_LOG.md) records the offline experiment. The application has no blur filter yet.

## 2026-11-13 16:03 · Use reviewed fields in Adapt

Gabriel asked to let the reviewer confirm or correct flagged fields and preserve the original evidence. Store reviewer decisions alongside the extraction and reapply them to the original recipe before adaptation. Keep unresolved fields and document findings available for inspection. Treat an original serving count confirmed in Review as the scaling baseline. The browser keeps the decisions for the current session and includes them in the JSON export.

## 2026-11-13 16:18 · Plan vegan substitutions from the recipe

Gabriel chose Luna to propose vegan substitutes and cooking changes in advance for each recipe. Generate the plan from the structured recipe during import and refresh it when a reviewer changes the source fields. Save the plan alongside the recipe. Apply the validated substitutions before scaling so Python can reconcile the ingredient names and measures in the updated instructions. Flag uncertain replacements or quantities for review. Serving adjustment remains available when vegan planning fails.

## 2026-11-13 16:30 · Preserve uploads when blur evidence is uncertain

Gabriel asked to reject unreadable blur while preserving legible recipes. Require severe visual blur and explicit agreement from both readers on every page before asking for a sharper copy. Keep the upload eligible for review if either reader recovers any text, if the PDF has a text layer, or if a check is inconclusive. The detector uses the existing Pillow dependency and the same PDF pages sent to Luna. Keep settings that describe a particular example in the bundled example report.

## 2026-11-13 16:41 · Use explicit ingredient scaling rules

Gabriel chose Luna to classify each ingredient and deterministic Python to calculate target quantities. Save the rules by ingredient and recipe variant. Require explicit evidence for fixed amounts and documented batch sizes. Preserve uncertain quantities for review. Gabriel is considering a Gemini verification pass. Evaluate independent classifications on manually checked recipes before deciding whether to add the call. Track material disagreements and shared errors as well as latency and cost.

## 2026-11-13 16:45 · Treat viable vegan alternatives as valid

Gabriel raised the risk of unnecessary human review when two different vegan substitutions both work. If a verifier is added, ask it to assess the proposed ingredient and preparation against the recipe and cite any concrete incompatibility or missing information. Reserve review for issues that affect the adapted recipe. Include recipes with multiple viable substitutes when measuring unnecessary reviews.

## 2026-11-13 16:52 · Use Gemini for targeted verification

Gabriel chose Gemini to check Luna's vegan proposal for concrete incompatibilities with the recipe and to classify scaling rules independently. Keep a source quantity pending when the scaling rules yield different amounts or batch requirements at the requested serving count. Store both outcomes for review. Keep the Luna plan available if verification fails and expose that status separately.
