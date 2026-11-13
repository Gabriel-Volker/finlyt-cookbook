# Decision log

## 2026-11-13 · Handwriting recognition

Times are local to São Paulo (UTC−03:00).

- **10:31–10:32** — Generated the German OCR test outputs with Docling. EasyOCR produced Markdown at 10:31, and Tesseract produced a second output at 10:32.
- **10:47** — Recorded the initial decision to prioritize handwriting recognition and evaluate ways to detect extraction errors.

After reading the challenge brief, I ran a quick Docling test with German OCR on the handwritten recipe. The output lost both half-liter measurements in the marinade and misread the eight-day timing. Based on that result, I set local OCR aside for this manuscript. Handwriting recognition falls outside the main use case I had in mind for Docling and the document processing work I did in SynapseFlux.

My first priority is to get the text recognition stage working. I plan to try a vision-capable LLM or a cloud service built for handwriting recognition. My initial idea is to send the same pages to two different recognition tools, then ask an LLM to compare their outputs and flag inconsistencies in quantities, units, times, and ingredient names. A disagreement would point me to a passage to check against the original page. This may catch more errors, at the cost of another recognition pass and a comparison call. I want to see which errors it catches before deciding whether the extra cost is worth it.

In a production pipeline, I would consider using Docling with OCR disabled for documents that already have a usable text layer. I am leaving that route for future work and focusing this project on the handwritten input first.
