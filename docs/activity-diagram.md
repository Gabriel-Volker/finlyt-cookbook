# Cookbook Activity Diagram

The web workflow from import to export. Import prepares the recipe and plans. Review saves human decisions with the source evidence. Adapt uses those decisions and the selected plan.

```mermaid
flowchart TD
    start(["Open Cookbook"]) --> entry{"Upload or try example?"}
    entry -->|"Try example"| saved["Load the bundled report"]
    entry -->|"Upload"| upload["Validate the uploaded file"]
    upload --> route{"Read text directly?"}
    route -->|"TXT or eligible PDF"| native["Read native text with page markers"]
    route -->|"Scan or image"| ocr["Run Gemini and Terra OCR in parallel"]
    ocr --> blur{"Do both readers flag every page and the blur check concur?"}
    blur -->|"Yes"| rescan(["Ask for a sharper copy"])
    blur -->|"Otherwise"| compare["Check unreadable spans and compare differing readings with Luna"]
    native --> extract["Extract with Luna and validate source evidence"]
    compare --> extract
    extract --> veganPlan["Propose vegan substitutions and method changes with Luna"]
    veganPlan --> measures["Validate edits and any equivalent measures against the source"]
    measures --> ready{"Is the proposal ready for verification?"}
    ready -->|"Yes"| verify["Gemini checks suitability, preparation and proposed measures"]
    ready -->|"No"| original["Keep the original recipe and record why the vegan plan is unavailable"]
    verify --> approved{"Did Gemini approve the plan?"}
    approved -->|"Yes"| vegan["Prepare the verified vegan variant"]
    approved -->|"No"| original
    vegan --> scalePlan["Classify scaling for each available variant with Luna and Gemini"]
    original --> scalePlan
    scalePlan --> translations["Prepare both display languages for the available variants"]
    translations --> review["Review the recipe, source evidence and warnings"]
    saved --> review

    review -->|"Edit a field"| correction["Save the review decision with source evidence"]
    correction --> review
    review -->|"Open Adapt"| current["Reuse current plans or refresh stale plans and translations"]
    current --> options["Choose servings, output language and any available vegan option"]
    options --> apply["Apply verified vegan changes, then scale in Python when possible"]
    apply --> display["Display adjusted recipe and warnings using prepared translations"]
    display -->|"Change settings"| options
    display -->|"Review a warning"| review
    display -->|"Export"| report(["Download JSON with evidence and changes"])
```

- Gemini reads the original PDF. Terra receives rendered pages or the original PDF above the raster budget. The blur check requires both OCR readers to flag every page and visual evidence of severe blur.
- Luna can propose a different quantity or unit for a suitable vegan substitute when the source amount is confirmed. Gemini checks the specific equivalence and the final preparation. Adapt displays the reason when a culinary blocker, review issue, or verification failure prevents it from offering a vegan version.
- Scaling uses a stated or user-provided baseline and the measures from the selected variant. Python adjusts linked steps and flags unresolved quantities, batch constraints and vessel capacity when needed.
- Each upload runs the pipeline. Changing servings uses the local Python calculation, and changing the language uses the display output prepared for that upload. Export preserves the original evidence, review decisions and adapted values.
