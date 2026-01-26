# pylint: skip-file
'''
VeraPDF PDF/UA Validation Utility
'''
import csv
from datetime import datetime
import pandas as pd
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
from parallelbar import progress_starmap
from .resources import ROOT_DIR, get_configuration_file
from ..report import run_report_generation

def runJavaValidation(pdfPath: str, reportPath: str, profile: str = "ua1", format: str = "xml") -> tuple:
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
    jarPath = ROOT_DIR / "lib/greenfield-apps-1.28.0.jar"
    try:
        command = []
        if profile == "wcag":
            profile_path = get_configuration_file("WCAG-2-2-Complete.xml")
            command = ["java", "-jar", jarPath, "--profile", str(profile_path), "--format", format, pdfPath]
        else:
            command = ["java", "-jar", jarPath, "--flavour", "ua1", "--format", format, pdfPath]

        result = subprocess.run(
            command,
            capture_output=True,  # capture output
            text=True  # read output as text
        )
        # check the validation output
        # print("STDOUT:\n", result.stdout)
        # print("STDERR:\n", result.stderr)

        if result.returncode <= 1:
            filename = Path(pdfPath).stem.split('.')[0] + f".{format}"
            reportPath = Path(reportPath) / profile / filename
            reportPath.parent.mkdir(parents=True, exist_ok=True)

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
        rules_data = {}
        rules_data['specification'] = rule.get("specification")  # add specification text to the dictionary
        rules_data['clause'] = rule.get("clause")  # add clause text to the dictionary
        rules_data['tags'] = rule.get("tags")  # add tags text to the dictionary
        rules_data['test'] = rule.get("testNumber")  # add test text to the dictionary
        description = rule.find("description")
        if description is not None:
            rules_data['description'] = description.text  # add description text to the dictionary
        rules.append(rules_data)
    # print the result
    # for i, rule in enumerate(rules, 1):
    #     print(f"Rule {i}: {rule}")

    return rules

def validatePdf(pdfPath: str, reportPath: str, subfolderPath: str, profiles: list = ["ua1", "wcag"], format: str = "xml") -> list:
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
    results = {}

    for profile in profiles:
        exitCode, output, error = runJavaValidation(pdfPath, reportPath, profile, "xml")
        filename = pdfPath

        if exitCode > 1:
            results[profile] = [filename, 'Error', 0, []]

        rules = []
        if exitCode == 0: 
            #print("Validation successfull.")
            results[profile] = [filename, True, 0, []]
        elif exitCode == 1:
            # print("Non-valid PDF/UA document")
            rules = parseValidationReport(output)
            results[profile] = [filename, False, len(rules), rules]
        else:
            results[profile] = [filename, 'Error', 0, []]

    flattened_result = [
        filename,
        results['ua1'][1],
        results['ua1'][2],
        results['wcag'][1],
        results['wcag'][2],
        results['ua1'][3],
        results['wcag'][3]
    ]

    return flattened_result

def write_validation_report(folder: str, results: list) -> None:
    '''
    Write the validation results to CSV reports.
    
    :param folder: Description
    :type folder: str
    :param results: Description
    :type results: list
    '''

    df = pd.DataFrame(results, columns=['path', 'ua1', 'ua1_failed_rules_count', 'wcag', 'wcag_failed_rules_count'])
    total_files = len(df)
    ua1_failed = df['ua1'] == False
    ua1_error = df['ua1'] == 'Error'
    wcag_failed = df['wcag'] == False
    wcag_error = df['wcag'] == 'Error'
    ua1_failed_count = ua1_failed.sum()
    wcag_failed_count = wcag_failed.sum()
    ua1_error_count = ua1_error.sum()
    wcag_error_count = wcag_error.sum()
    ua1_passed_count = total_files - ua1_failed_count - ua1_error_count
    wcag_passed_count = total_files - wcag_failed_count - wcag_error_count
    ua1_success_rate = (ua1_passed_count / total_files) * 100 if total_files > 0 else 0
    wcag_success_rate = (wcag_passed_count / total_files) * 100 if total_files > 0 else 0
    ua1_violation_total = df['ua1_failed_rules_count'].sum()
    wcag_violation_total = df['wcag_failed_rules_count'].sum()
    print()
    print("Validation Summary:")
    print(f"  UA1 ({ua1_success_rate:.0f}%) - Passed: {ua1_passed_count}, Failed: {ua1_failed_count}, Error: {ua1_error_count}, Total Violations: {ua1_violation_total}")
    print(f"  WCAG ({wcag_success_rate:.0f}%) - Passed: {wcag_passed_count}, Failed: {wcag_failed_count}, Error: {wcag_error_count}, Total Violations: {wcag_violation_total}")
    print()

    # Write results to CSV
    with open(folder / "vera_validation_results.csv",
              mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(['path', 'ua1', 'ua1_failed_rules_count', 'wcag', 'wcag_failed_rules_count'])
        writer.writerows(results)

    print(f"Reports: {folder}")

def validate_pdf_multiprocess(
        workspace_folder_path: Path,
        file_paths: list,
        timestamp: str = None,
        subfolder: str = None) -> None:
    '''
    Multiprocess to validate PDF files using VeraPDF.
    
    :param workspace_folder_path: Description
    :type workspace_folder_path: Path
    :param file_paths: Description
    :type file_paths: list
    :param timestamp: Description
    :type timestamp: str
    :param subfolder: Description
    :type subfolder: str
    '''
    profiles = ["ua1", "wcag"]
    # Prepare the validation file paths, which are tuples of (inputPdfPath, reportPath).
    validation_file_paths = []

    # Create a timestamped report folder inside the reports folder.
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = workspace_folder_path.parent / "reports" / \
        f"{timestamp}-{subfolder if subfolder else 'files'}"
    report_path_xml = report_path / "xml"
    report_path_xml.mkdir(parents=True, exist_ok=True)

    for file_path in file_paths:
        validation_file_paths.append(
            (str(file_path),
             str(report_path_xml),
             str(workspace_folder_path),
             profiles)
        )

    print()
    print("Validating PDFs...")
    results = progress_starmap(validatePdf, validation_file_paths, total=len(file_paths))

    csv_results = []
    for result in results:
        csv_result = result[:]
        csv_result[0] = csv_result[0].replace(str(workspace_folder_path), "")
        csv_results.append(csv_result)
        
        # delete columns with rule details to reduce memory usage
        del csv_result[5:]

    write_validation_report(report_path, csv_results)

    run_report_generation(report_path, profiles)

    return results
