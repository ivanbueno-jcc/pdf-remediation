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

## Commands

### Validate files

```uv run -m pdf_remediation.Validate [folder]```

### Remediate files

```uv run -m pdf_remediation.Fix [folder]```

* __[folder]__ must be located in resources/input.

### Generate a Summary Report

```uv run -m pdf_remediation.Report [folder]```

## Set the license

Create a .env in the root directory with the following content:
```
PDFIX_LICENSE_NAME = "xxx"
PDFIX_LICENSE_KEY = "xxx"
```

### Get License Info and Status

```uv run -m pdf_remediation.License```

### Activate license

```uv run -m pdf_remediation.LicenseActivate [license-key]```

### Deactivate License

```uv run -m pdf_remediation.LicenseDeactivate```