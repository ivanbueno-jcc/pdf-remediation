# PDF Validation and Remediation

## Installation

1. Install Java to run VeraPDF
2. Install uv for running the Python package.

    a. Windows Powershell

        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

    b. Mac Terminal

        curl -LsSf https://astral.sh/uv/install.sh | sh

3. Clone this repo, and go to the root directory of this project.
4. Run: 
    
    ```uv run -m pdf_remediation.Validate benchmark```
5. OPTIONAL
    
    benchmark is a folder under resources/input, containing the PDF files.

    If you want to validate against another set of files, add a new folder under resources/input.

    Then run:

        uv run -m pdf_remediation.Validate YOUR-NEW-FOLDER