# Cookbook Activity Diagram

The web workflow from import to export. Import prepares the recipe and plans. Review saves human decisions with the source evidence. Adapt uses those decisions and the selected plan.

```mermaid
flowchart TD
    start(["Open Cookbook"]) --> entry{"Upload or try example?"}
    entry -->|"Try example"| saved["Load the bundled report"]
    entry -->|"Upload"| upload["Validate file and check import cache"]
    upload -->|"Cache hit"| savedReport["Reuse saved readings and plans"]
    upload -->|"New file"| route{"Read text directly?"}
    route -->|"TXT or eligible PDF"| native["Read native text with page markers"]
    route -->|"Scan or image"| ocr["Run Gemini and Terra OCR in parallel"]
    ocr --> blur{"Do both readers flag every page and the blur check concur?"}
    blur -->|"Yes"| rescan(["Ask for a sharper copy"])
    blur -->|"Otherwise"| compare["Check unreadable spans and compare differing readings with Luna"]
    native --> extract["Extract with Luna and validate source evidence"]
    compare --> extract
    extract --> veganPlan["Propose with Luna and verify the vegan recipe with Gemini when viable"]
    veganPlan --> scalePlan["Classify scaling for the original and any verified vegan variant"]
    scalePlan --> translations["Prepare both display languages in one batch"]
    savedReport --> translations
    translations --> review["Review the recipe, source evidence and warnings"]
    saved --> review

    review -->|"Edit a field"| correction["Save the review decision with source evidence"]
    correction --> review
    review -->|"Open Adapt"| current["Reuse current plans or refresh stale plans and translations"]
    current --> options["Choose servings, diet and output language"]
    options --> apply["Apply verified vegan changes, then scale in Python when possible"]
    apply --> display["Display adjusted recipe and warnings using prepared translations"]
    display -->|"Change settings"| options
    display -->|"Review a warning"| review
    display -->|"Export"| report(["Download JSON with evidence and changes"])
```

- Gemini reads the original PDF. Terra receives rendered pages or the original PDF above the raster budget. The blur check requires both OCR readers to flag every page and visual evidence of severe blur.
- The original recipe remains available when the vegan plan cannot be verified. Changing the yield requires a stated or user-provided baseline. The app flags effects it cannot apply safely.
- Changing servings uses the local Python calculation. Changing the language uses the prepared display output. Export preserves the original evidence and review decisions.
