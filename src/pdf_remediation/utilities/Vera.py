from .Resources import ROOT_DIR
import subprocess, sys
import xml.etree.ElementTree as ET
from pathlib import Path

def runJavaValidation(pdfPath: str, reportPath: str, format: str = "xml"):
    """
    Executes a Java-based PDF/UA validation tool on the specified PDF file.

    Parameters:
        pdfPath (str): Path to the PDF file to be validated.

    Returns:
        tuple: A tuple containing:
            - returncode (int): Exit code from Java process (0=success, 1=validation failed, -1=error)
            - stdout (str): Standard output from the validation tool
            - stderr (str): Standard error output from the validation tool

    Notes:
        Requires Java to be installed and accessible in the system PATH.
        The validation tool JAR is expected to be in a specific location relative to Python's prefix.
        The flavour identifies the accessibility stndard ["ua1", "ua2"]. For WCAG use --profile with 
        valid path to validation profile. For more information see https://github.com/veraPDF/veraPDF-validation-profiles
    """
    jarPath = ROOT_DIR / "lib/greenfield-apps-1.27.0-SNAPSHOT.jar"
    try:
        result = subprocess.run(
            ["java", "-jar", jarPath, "--flavour", "ua1", "--format", format, pdfPath],  # JAR execution cmd
            capture_output=True,  # capture output
            text=True  # read output as text
        )
        # check the validation output
        # print("STDOUT:\n", result.stdout)
        # print("STDERR:\n", result.stderr)

        if result.returncode <= 1:
            filename = Path(pdfPath).stem.split('.')[0] + f".{format}"
            reportPath = Path(reportPath) / filename
            
            with open(reportPath, "w", encoding="utf-8") as file:
                file.write(result.stdout)

        return result.returncode, result.stdout, result.stderr  # java exit code and output, error

    except FileNotFoundError:
        print("Error: Java not found.")
        return -1
    except Exception as e:
        # print(f"Unexpected error: {e}")
        return -1

def parseValidationReport(xmlReport: str):
    """
    Parses the XML validation report from the PDF/UA validation tool.

    Parameters:
        xmlReport (str): XML string containing the validation results.

    Returns:
        list: A list of dictionaries, each representing a validation rule with its attributes.

    Notes:
        Each rule dictionary contains attributes like 'clause', 'test', 'result', etc.
        The XML structure is expected to match the output format of the validation tool.
    """    
    root = ET.fromstring(xmlReport)

    # Extract data from the <rule>
    rules = []
    for rule in root.findall(".//rule"):
        rule_data = rule.attrib  # read attributes as a dictionary
        rules.append(rule_data)

    # print the result
    for i, rule in enumerate(rules, 1):
        print(f"Rule {i}: {rule}")
        
    return rules

def validatePdf(pdfPath: str, outputPdfPath: str, reportPath: str, format: str = "xml") -> list:
    """
    Validates a PDF document against PDF/UA standards using a Java validation tool.

    Parameters:
        doc (PdfDoc): The PDF document object to validate.
        pdfPath (str): Temporary path where the PDF will be saved for validation.

    Returns:
        list: A list of validation rule violations (empty list if validation passed).

    Raises:
        Exception: If the document cannot be saved or if validation fails unexpectedly.
    """    
    exitCode, output, error = runJavaValidation(pdfPath, reportPath, "xml")

    if exitCode > 1:
        # print(error)
        # raise Exception((f"Validation failed with error {exitCode}"))
        return [pdfPath.split('/')[-1], 'Error']

    # optional - generate HTML validation report
    # runJavaValidation(pdfPath, reportPath, "html")

    rules = []
    if exitCode == 0: 
        #print("Validation successfull.")
        return [pdfPath.split('/')[-1], True]
    elif exitCode == 1:
        # print("Non-valid PDF/UA document")
        # rules = parseValidationReport(output)
        return [pdfPath.split('/')[-1], False]
    else:
        return [pdfPath.split('/')[-1], 'Error']
            
    # return rules

def hello(number: int) -> None:
    print(f"Hello, world! {number}")