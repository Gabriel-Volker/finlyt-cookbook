# AI collaboration log

Gabriel's ideas and project decisions are in the [decision log](DECISION_LOG.md).

Times are local to São Paulo (UTC−03:00). Earlier chat entries use the date when the exact time was not recorded.

## 2026-11-13 · Gemini transcription test

The transcription was saved at **10:57:58**.

Codex sent the original two-page PDF to `gemini-3.1-flash-lite` in a single API call. The prompt asked for a German transcription in page order and gave no reference answers. I used the manuscript as the reference for the comparison with Docling's German EasyOCR and Tesseract runs.

- **Quantities.** Gemini recovered all three ingredient–quantity pairs: ½ L vinegar, ½ L water, and four sliced onions. Neither Docling run recovered a complete pair reliably.
- **Preparation.** Gemini preserved the hot and cold marinade conditions, the searing sequence, the optional additions, the sauce preparation, and the instruction to put the unsliced roast in the sauce one hour before serving. The OCR outputs retained fragments of these instructions.
- **Missing values.** Gemini left out the meat weight and serving count, both absent from the manuscript.
- **Timing.** The Gemini API call took 3.71 seconds. The earlier local Docling runs took 25.0 seconds with EasyOCR and 23.6 seconds with Tesseract.

## 2026-11-13 · Codex chat log begins

Gabriel asked Codex to add the Gemini comparison to this log and to start recording brief summaries of project conversations from that request onward.

- Gabriel asked about the illustrated header on page 1. Codex checked the saved transcription, which starts at “Sauerbraten.” The test prompt asked Gemini to skip artwork labels and decorative headings outside the recipe, so the run did not evaluate transcription of the header or the cookbook attribution.
- Gabriel asked for separate files for AI conversations and his own ideas. Codex kept the test and chat summaries here and moved the project reasoning to the decision log. Both logs will be updated as the project progresses.

## 2026-11-13 · Full PDF transcription and second VLM

The full transcription was saved at **11:14:46**.

Gabriel asked Codex to rerun Gemini with the illustrated header included and to research a second reader for the comparison workflow. Codex sent the original two-page PDF to `gemini-3.1-flash-lite` with a prompt requesting all readable text in German, including page numbers, the illustrated header, and illustration labels. The prompt supplied no expected text. It requested separate sections for decorative text and recipe content.

The first request reached its 50-second timeout. A retry with the same prompt succeeded in 42.53 seconds and reported 1,178 input tokens and 403 output tokens.

Codex checked the output against the scan. Gemini recovered the illustrated title, the cookbook attribution to Großmutter Josefine and the year 1887, both printed page numbers, and the labels Rosinen, Essig, Korinthen, and Rotwein. All eight lines of recipe content from the earlier run also matched exactly. The raw output is stored locally with the other test results.

Codex proposed Claude Haiku 5.5 as the second VLM based on its PDF support, published price, and performance on a handwriting benchmark. Gabriel described a third call to a text LLM to compare the two transcriptions, with human review for disagreements. The workflow and possible escalation to a stronger VLM are recorded in the [decision log](DECISION_LOG.md).

## 2026-11-13 · GPT access and Consensus Entropy OCR

Gabriel asked about using his ChatGPT subscription for a GPT second reader and asked to record Consensus Entropy OCR with automatic rejection of scans that are too blurred. Codex checked the OpenAI documentation and the authors' CVPR 2026 project page. A separate ChatGPT or Codex session can be used for a comparison under the subscription. Automated Responses API calls need a Platform API key and use API billing or eligible subscription credits. The proposed blur policy is recorded in the [decision log](DECISION_LOG.md) as an extension to the paper's uncertainty routing. This discussion produced no new transcription run.

## 2026-11-13 11:42 · ChatGPT Pro API setup and logging

Gabriel confirmed he has ChatGPT Pro and asked how to use its API credits for the project. Codex checked [OpenAI's API credits guide](https://developers.openai.com/api/docs/guides/chatgpt-api-credits) and documented how to claim the benefit, use a key from the linked API organization, and set `OPENAI_API_KEY` on Windows. This entry records setup guidance.

Gabriel asked for timestamps in both logs and one Git commit per prompt from this request onward. He also proposed a conventional OCR comparison and balance-sheet reconciliation for production credit analysis. The [decision log](DECISION_LOG.md) records why both are outside the cookbook demo's scope.

## 2026-11-13 11:51:22–11:51:45 · GPT-6 Luna transcription test

Gabriel confirmed that his API key and credits were ready and asked Codex to test `gpt-6-luna`. Codex sent the same original two-page PDF and the exact prompt used for the full Gemini transcription, including the request for the illustrated header. The OpenAI Responses API returned a completed transcription in 23.10 seconds, with 14,833 input tokens and 1,941 output tokens, including 1,552 reasoning tokens. The raw output was saved locally as `Recipe-gpt-6-luna-full-de.md`.

- **Recipe.** All eight recipe lines match Gemini after normalizing the notation for one half and the spaces around one hyphen. Both models recovered ½ L of vinegar, ½ L of water, and four sliced onions. They also preserved the marinade conditions, searing, optional ingredients, sauce, and timing before serving.
- **Header.** GPT recovered the title, the attribution, and the year 1887, but marked the name as `Jose[unleserlich]ines`. Gemini returned `Josefines`, which agrees with the scan. This is a real difference that the proposed comparison step should flag for human review.
- **Illustrations.** Both recovered the printed page numbers and the four readable labels. GPT grouped the second page's illustration labels before the recipe text, while Gemini followed their placement below it in the scan.

Codex compared the two saved outputs and checked the differences against the original pages. These observations come from one completed run per model with the full transcription prompt.

## 2026-11-13 11:56:38–11:56:51 · GPT-6.1 Sol transcription and cost comparison

Gabriel asked for the cost of the Luna run and a comparison with `gpt-6.1-sol`. Codex ran Sol on the same PDF with the same prompt and Responses API settings, changing only the model. Sol completed in 12.85 seconds. Its raw output is stored locally as `Recipe-gpt-6.1-sol-full-de.md`.

| Model | Input tokens | Cache write tokens | Output tokens | Reasoning tokens within output | API time | Estimated USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| GPT-6 Luna | 14,833 | 14,830 | 1,941 | 1,552 | 23.10 s | $0.00282455 |
| GPT-6.1 Sol | 14,833 | 14,830 | 553 | 131 | 12.85 s | $0.04261100 |

Both responses reported zero cached input tokens and three ordinary input tokens. The estimates use the [standard API rates](https://developers.openai.com/api/docs/pricing) and [30-minute cache write rates](https://developers.openai.com/api/docs/guides/prompt-caching). Sol cost about 15.1 times as much and returned 10.25 seconds sooner in these single runs.

The recipe body matches on both pages after joining words split at line breaks and normalizing spacing. Both models recovered the same ingredients, quantities, and preparation instructions. Sol read `Josefines` as written in the illustrated header. Luna returned `Jose[unleserlich]ines`. Sol also kept the second page's illustration labels below the recipe, where they appear in the scan. Sol preserved more of the original line breaks and added a period to the illustrated title.

## 2026-11-13 11:59 · Lunch break

Gabriel paused work on the project for lunch.

## 2026-11-13 13:18 · Resumed after lunch

Gabriel returned from lunch and asked Codex to push the project commits to GitHub.

## 2026-11-13 13:27:38–13:29:09 · Blind transcription comparison

Gabriel asked Codex to test a GPT comparison using the full Gemini and Luna transcriptions. Codex sent only the two texts in fields `A` and `B` to `gpt-6-luna`, with neither model names nor the PDF in the request. The [system prompt](prompts/compare_transcriptions.system.txt) gives general rules for aligning recipe passages and identifying conflicts, omissions, and unreadable text. The [output schema](prompts/compare_transcriptions.schema.json) keeps review items and equivalent variations in separate arrays.

Early runs exposed inconsistent page labels and a synthetic result that normalized a fraction inside a quoted excerpt. Codex added a page-index convention and explicit instructions to copy evidence from each input. The test rejected any non-null excerpt that was absent verbatim from its assigned input.

The final runs started at 13:28:58. With the original order (7.48 s) and with A and B swapped (10.34 s), the comparator returned exactly one review item on page 1. One text had `Josefines` and the other had `Jose[unleserlich]ines`. It classified `½ l` and `1/2 l` as equivalent in both runs.

The same prompt was tested on a synthetic pear compote recipe (5.71 s). It flagged tablespoon versus teaspoon, an omitted optional ingredient, covered versus uncovered cooking, and a note marked unreadable by both readers. It accepted `¾` versus `3/4` and `0,25 l` versus `250 mL`. All three final outputs passed the verbatim excerpt check. The tests cover these examples only.

## 2026-11-13 13:35:35–13:35:53 · GPT-6.1 Terra transcription and cost comparison

Gabriel asked Codex to test `gpt-6.1-terra` and compare it with Luna. Codex used the same PDF, full transcription prompt, and Responses API settings as in the Luna run, changing the model. The raw output was saved locally as `Recipe-gpt-6.1-terra-full-de.md`.

| Model | Input tokens | Cache write tokens | Output tokens | API time | Estimated USD |
| --- | ---: | ---: | ---: | ---: | ---: |
| GPT-6 Luna | 14,833 | 14,830 | 1,941 | 23.10 s | $0.00282455 |
| GPT-6.1 Terra | 27,509 | 27,506 | 700 | 18.09 s | $0.03788550 |

Both calls reported three ordinary input tokens and zero cached input tokens. Terra's output included 272 reasoning tokens. The estimates use the [standard API rates](https://developers.openai.com/api/docs/pricing) and [30-minute cache write rates](https://developers.openai.com/api/docs/guides/prompt-caching). Terra took 5.01 seconds less and cost 13.41 times as much in these single runs.

The recipe body matched on both pages after joining words split across lines and normalizing whitespace and hyphen spacing. Terra read `Josefines` correctly in the illustrated header and put the second page's labels below the recipe, where they appear in the scan. It marked part of the `Korinthen` label unreadable, which Luna read in full. The models therefore had different strengths on this scan even though they agreed on all recipe content.

## 2026-11-13 13:45:28–13:45:56 · Implemented OCR and review pipeline

Gabriel selected Gemini 3.1 Flash-Lite and GPT-6 Luna as the OCR readers, with Luna as the comparator. Codex implemented a Python CLI that runs the readers in parallel and sends their text to the existing comparison prompt and JSON schema under the labels A and B. The reader prompt now preserves the source language for any supported input. The CLI loads credentials from environment variables or a local `.env` and includes an empty `.env.example` for setup.

The live run on `Recipe.pdf` took 28.36 seconds and returned `needs_review` with one conflict on page 1. Gemini read `Josefines` and Luna read `Josetines`. The comparator flagged the name and treated `½ l` and `1/2 l` as equivalent. The CLI verified that the cited excerpts appeared verbatim in their respective transcriptions. Four local unit tests covered the anonymous request, review status, equivalent fractions, and invalid evidence.
