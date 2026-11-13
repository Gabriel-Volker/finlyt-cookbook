# Cookbook Activity Diagram

Web workflow from recipe import to JSON export.

```mermaid
flowchart TD
    start(["Start"]) --> entry{"Upload or example?"}

    subgraph reading ["Import and read"]
        upload["Validate PDF, PNG or JPEG"]
        gemini["Transcribe with Gemini"]
        luna["Transcribe with Luna"]
        unreadable{"Severe blur and no readable text?"}
        rescan(["Request a sharper copy"])
        compare["Compare texts A and B with Luna"]
        extract["Extract with Luna and validate evidence"]
        plan["Ask Luna for a vegan plan"]
        example["Load the example report"]
        upload -->|"Parallel OCR"| gemini
        upload -->|"Parallel OCR"| luna
        gemini --> unreadable
        luna --> unreadable
        unreadable -->|"Yes"| rescan
        unreadable -->|"No or uncertain"| compare
        compare --> extract
        extract --> plan
    end

    entry -->|"Upload file"| upload
    entry -->|"Try example"| example

    subgraph review ["Review"]
        display["Show recipe and review flags"]
        inspect["Inspect excerpts and source page"]
        resolve["Confirm or correct a flagged field"]
        saveReview["Save decision and original evidence"]
        refreshPlan["Refresh the plan for the reviewed recipe"]
        display -.->|"Select item"| inspect
        inspect -->|"Resolve a field"| resolve
        resolve --> saveReview
        saveReview --> refreshPlan
        refreshPlan -.->|"Review another field"| display
    end

    plan --> display
    example --> display

    subgraph adapt ["Adapt"]
        options["Choose servings and vegan option"]
        valid{"Serving counts valid?"}
        correct["Ask for valid serving counts"]
        requested{"Any adaptation requested?"}
        base["Apply saved decisions to the original recipe"]
        vegan["Apply the saved Luna plan if selected"]
        scale["Scale measures and steps if requested"]
        changes["Show changes and warnings"]
        options --> valid
        valid -->|"No"| correct
        correct --> options
        valid -->|"Yes or not required"| requested
        requested -->|"Yes"| base
        base --> vegan
        vegan --> scale
        scale --> changes
        changes -.->|"Change options"| options
    end

    display -->|"Open Adapt"| options
    refreshPlan -->|"Open Adapt"| options
    requested -->|"No"| download(["Export JSON report"])
    changes -->|"Include adaptation"| download

    style reading fill:#E9F2FA,stroke:#8AA9C6,color:#1E1E1E
    style review fill:#F0F4FA,stroke:#9AAFC8,color:#1E1E1E
    style adapt fill:#E9F2FA,stroke:#8AA9C6,color:#1E1E1E
    style correct fill:#FFECBD,stroke:#D5A43A,color:#1E1E1E
```

- For PDF uploads, Gemini receives the file and Luna receives rasterized pages, with a PDF fallback above the local raster budget. Luna compares the two transcriptions, extracts the source recipe, and prepares a vegan plan from the validated fields.
- The blur check can reject an upload only when both readers explicitly report unreadable text on every page and the image check corroborates severe blur. A PDF text layer prevents blur rejection.
- Review keeps the original extraction and evidence alongside the reviewer's decisions. Unresolved fields and document-level alerts remain visible.
- Adapt uses the reviewed recipe when decisions exist. Scaling requires a usable original serving count. Python applies the stored vegan plan before scaling if both options are selected. The export includes the original report, review decisions, the plan, and any adaptation.
