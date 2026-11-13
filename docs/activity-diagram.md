# Cookbook Activity Diagram

Web workflow from recipe import to JSON export.

```mermaid
flowchart TD
    start(["Start"]) --> entry{"Upload or example?"}

    subgraph reading ["Import and read"]
        upload["Validate PDF, PNG or JPEG"]
        gemini["Transcribe with Gemini"]
        terra["Transcribe with Terra"]
        unreadable{"Severe blur and no readable text?"}
        rescan(["Request a sharper copy"])
        compare["Compare texts A and B with Luna"]
        extract["Extract with Luna and validate evidence"]
        plan["Ask Luna for a vegan plan"]
        viable{"Luna found a viable plan?"}
        verifyVegan["Ask Gemini to check the vegan proposal"]
        verified{"Every replacement approved?"}
        abstain["Save the reason and keep original scaling available"]
        scalePlan["Classify scaling rules independently with Luna and Gemini"]
        savePlans["Save plans and verification results"]
        prepareOutput["Prepare both output languages and validate measures"]
        example["Load the example report"]
        upload -->|"Parallel OCR"| gemini
        upload -->|"Parallel OCR"| terra
        gemini --> unreadable
        terra --> unreadable
        unreadable -->|"Yes"| rescan
        unreadable -->|"No or uncertain"| compare
        compare --> extract
        extract --> plan
        plan --> viable
        viable -->|"Yes"| verifyVegan
        viable -->|"No or uncertain"| abstain
        verifyVegan --> verified
        verified -->|"Yes"| scalePlan
        verified -->|"No or uncertain"| abstain
        abstain --> scalePlan
        scalePlan --> savePlans
        savePlans --> prepareOutput
    end

    entry -->|"Upload file"| upload
    entry -->|"Try example"| example

    subgraph review ["Review"]
        display["Show recipe and review flags in the selected language"]
        inspect["Inspect excerpts and source page"]
        resolve["Confirm or correct a recipe field"]
        saveReview["Save decision and original evidence"]
        display -.->|"Select item"| inspect
        inspect -->|"Resolve a field"| resolve
        resolve --> saveReview
        saveReview -.->|"Review another field"| display
    end

    prepareOutput --> display
    example --> display

    subgraph adapt ["Adapt"]
        options["Choose servings, vegan option, and output language"]
        refreshPlan["Refresh plans for the reviewed recipe if needed"]
        valid{"Serving counts valid?"}
        correct["Ask for valid serving counts"]
        requested{"Any adaptation requested?"}
        base["Apply saved decisions to the original recipe"]
        vegan["Apply the verified vegan plan if selected"]
        scale["Compare outcomes and apply scaling rules in Python"]
        outputLanguage["Select prepared language and insert current measures"]
        output["Show recipe and keep original evidence"]
        changes["Show changes and warnings"]
        refreshPlan --> options
        options --> valid
        valid -->|"No"| correct
        correct --> options
        valid -->|"Yes or not required"| requested
        requested -->|"Yes"| base
        base --> vegan
        vegan --> scale
        scale --> changes
        changes --> outputLanguage
        requested -->|"No"| outputLanguage
        outputLanguage --> output
        changes -.->|"Change options"| options
    end

    display -->|"Open Adapt"| refreshPlan
    saveReview -->|"Open Adapt"| refreshPlan
    changes -.->|"Open flagged field"| inspect
    output --> download(["Export JSON report"])

    style reading fill:#E9F2FA,stroke:#8AA9C6,color:#1E1E1E
    style review fill:#F0F4FA,stroke:#9AAFC8,color:#1E1E1E
    style adapt fill:#E9F2FA,stroke:#8AA9C6,color:#1E1E1E
    style correct fill:#FFECBD,stroke:#D5A43A,color:#1E1E1E
```

- For PDF uploads, Gemini receives the file and Terra receives rasterized pages, with a PDF fallback above the local raster budget. Luna compares the two transcriptions, extracts the source recipe, and prepares a vegan plan from the validated fields.
- The blur check can reject an upload only when both readers explicitly report unreadable text on every page and the image check corroborates severe blur. A PDF text layer prevents blur rejection.
- Review keeps the original extraction and evidence alongside the reviewer's decisions. Unresolved fields and document-level alerts remain visible.
- Adapt uses the reviewed recipe when decisions exist. Scaling requires a usable original serving count. Python applies the stored vegan plan before using the scaling rules for that variant. The export includes the original report, review decisions, both plans, and any adaptation.
