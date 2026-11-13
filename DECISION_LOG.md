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
