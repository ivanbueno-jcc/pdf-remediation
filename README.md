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
    
    a. benchmark is a folder under resources/input.

    b. It contains the PDF files.

    c. Add a new folder under resources/input for different sets of PDFs.

    d. Then run:

        uv run -m pdf_remediation.Validate YOUR-NEW-FOLDER