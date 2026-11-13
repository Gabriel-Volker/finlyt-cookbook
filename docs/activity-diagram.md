# Cookbook Activity Diagram

Web workflow from recipe import to JSON export.

```mermaid
flowchart TD
    start(["Start"]) --> entry{"Upload or example?"}

    subgraph reading ["Import and read"]
        upload["Validate PDF, PNG or JPEG"]
        gemini["Transcribe with Gemini"]
        luna["Transcribe with Luna"]
        compare["Compare texts A and B with Luna"]
        extract["Extract with Luna and validate evidence"]
        example["Load the example report"]
        upload -->|"Parallel OCR"| gemini
        upload -->|"Parallel OCR"| luna
        gemini --> compare
        luna --> compare
        compare --> extract
    end

    entry -->|"Upload file"| upload
    entry -->|"Try example"| example

    subgraph review ["Review"]
        display["Show recipe and review flags"]
        inspect["Inspect excerpts and source page"]
        resolve["Confirm or correct a flagged field"]
        saveReview["Save decision and original evidence"]
        display -.->|"Select item"| inspect
        inspect -->|"Resolve a field"| resolve
        resolve --> saveReview
        saveReview -.->|"Review another field"| display
    end

    extract --> display
    example --> display

    subgraph adapt ["Adapt"]
        options["Choose servings and vegan option"]
        valid{"Serving counts valid?"}
        correct["Ask for valid serving counts"]
        requested{"Any adaptation requested?"}
        base["Apply saved decisions to the original recipe"]
        scale["Scale measures and steps if requested"]
        vegan["Apply Python vegan rules if requested"]
        changes["Show changes and warnings"]
        options --> valid
        valid -->|"No"| correct
        correct --> options
        valid -->|"Yes or not required"| requested
        requested -->|"Yes"| base
        base --> scale
        scale --> vegan
        vegan --> changes
        changes -.->|"Change options"| options
    end

    display -->|"Open Adapt"| options
    saveReview -->|"Open Adapt"| options
    requested -->|"No"| download(["Export JSON report"])
    changes -->|"Include adaptation"| download

    style reading fill:#E9F2FA,stroke:#8AA9C6,color:#1E1E1E
    style review fill:#F0F4FA,stroke:#9AAFC8,color:#1E1E1E
    style adapt fill:#E9F2FA,stroke:#8AA9C6,color:#1E1E1E
    style correct fill:#FFECBD,stroke:#D5A43A,color:#1E1E1E
```

- For PDF uploads, Gemini receives the file and Luna receives rasterized pages, with a PDF fallback above the local raster budget. Luna compares the two transcriptions before a separate extraction call.
- Review keeps the original extraction and evidence alongside the reviewer's decisions. Unresolved fields and document-level alerts remain visible.
- Adapt uses the reviewed recipe when decisions exist. Scaling requires a usable original serving count. When both options are selected, Python scales first and then applies vegan substitutions and step rules. The export includes the original report, review decisions, and any adaptation.
