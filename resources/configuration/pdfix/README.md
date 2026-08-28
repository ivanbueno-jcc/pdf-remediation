# PDFix SDK – Batch Actions Presets

This repository contains **official batch action presets** for **automated PDF remediation and PDF/UA compliance** using **PDFix SDK**.

The presets are designed to work **directly with PDFix SDK** (C++, .NET, Java, CLI integrations) and do **not rely on external tools**.  
Each workflow is defined declaratively in JSON and can be executed programmatically as part of server-side, batch, or CI/CD processing.


## Purpose

Different PDF authoring tools produce PDFs with **systematic and predictable accessibility issues**.

This repository provides:

- a **universal default remediation workflow** suitable for *any* PDF
- **source-specific remediation workflows** optimized for various creation tools

Each preset reflects **real-world PDF/UA failure patterns** and follows PDFix best practices.

- `make_accessible.json` 
  - Default PDF/UA Remediation Preset

    *Official default remediation preset for automated PDF/UA-1 compliance using PDFix SDK. The preset performs conservative, non-destructive cleanup, structure repair, tagging completion, and accessibility fixes.*

- `fix_indesign.json`
  - Fix InDesign Files
    
    *Specialized remediation preset for PDFs exported from Adobe InDesign. The preset fixes common structural, table, annotation, and PDF/UA issues introduced by InDesign exports and is intended to be used on top of the default PDF/UA remediation workflow.*

## Execution with PDFix SDK

Batch action presets in this repository define what actions are executed and in what order.
They are consumed by PDFix SDK or PDFix CLI to automatically remediate PDF documents.

In SDK-based integrations, actions are executed sequentially as defined in the JSON file.
Only the name and params fields are required; execution logic is handled by PDFix.

### Example: Run batch actions using PDFix CLI
```
./pdfix_app batch -i input.pdf -o output.pdf -c make_accessible.json
```

This command applies the selected batch action workflow to the input PDF and produces an accessible, remediated output document according to the rules defined in the JSON configuration.

For detailed documentation on batch actions and accessibility workflows execution, see:
https://pdfix.net/products/pdfix-sdk/accessibility-actions/

## Source-Specific Workflows

Source-specific presets are designed to be applied on top of the default remediation, not as replacements.

### Example: InDesign

fix_indesign.json addresses common Adobe InDesign issues such as:
- incorrect table headers (TD vs TH)
- missing table Scope attributes
- missing Note IDs
- decorative content incorrectly exposed to screen readers
- incomplete annotation descriptions

## Design Principles

All presets in this repository follow these rules:
- Non-destructive by default
- Preserve existing structure where possible
- Deterministic behavior (no random heuristics)
- Aligned with PDF/UA-1 requirements
- Optimized for veraPDF validation
- Production-tested logic

## License

This repository contains configuration presets only.

Use of these presets requires a valid PDFix SDK license.
See https://pdfix.net for licensing and documentation.

## Links

- PDFix SDK: https://pdfix.net
- PDF/UA standard: https://www.iso.org/standard/64599.html
