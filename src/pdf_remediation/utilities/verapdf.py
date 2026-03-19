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
from .report import run_report_generation
from .resources import (
    ROOT_DIR,
    append_to_csv,
    get_configuration_file,
    get_pdf_file_paths,
    get_relative_report_path,
    print_console_key_value_rows,
    print_console_message,
    print_console_section,
)

def in_memory_validation(pdfPath: str, profile: str = "ua1", format: str = "xml") -> tuple:

    jarPath = ROOT_DIR / "lib/greenfield-apps-1.28.0.jar"
    try:
        command = []
        if profile == "wcag":
            profile_path = get_configuration_file("WCAG-2-2-Complete.xml")
            command = ["java", "-jar", jarPath, "--profile", str(profile_path), "--format", format, pdfPath]
        elif profile == "ua1":
            command = ["java", "-jar", jarPath, "--flavour", "ua1", "--format", format, pdfPath]
        else:
            profile_path = get_configuration_file(profile)
            command = ["java", "-jar", jarPath, "--profile", str(profile_path), "--format", format, pdfPath]

        result = subprocess.run(
            command,
            capture_output=True,  # capture output
            text=True  # read output as text
        )

        exitCode = result.returncode
        output = result.stdout
        result = []

        if exitCode > 1:
            result = ['Error', 0, []]

        if exitCode == 0: 
            #print("Validation successfull.")
            result = [True, 0, []]
        elif exitCode == 1:
            # print("Non-valid PDF/UA document")
            rules = parseValidationReport(output)
            result = [False, len(rules), rules]
        else:
            result = ['Error', 0, []]

        return result

    except FileNotFoundError:
        return ['Error', 0, []]
    except Exception as e:
        # print(f"Unexpected error: {e}")
        return ['Error', 0, []]

def runJavaValidation(pdfPath: str, reportPath: str, profile: str = "ua1", format: str = "xml") -> tuple[int, str, str]:
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
            command = ["java", "-jar", str(jarPath), "--profile", str(profile_path), "--format", format, pdfPath]
        else:
            command = ["java", "-jar", str(jarPath), "--flavour", "ua1", "--format", format, pdfPath]

        result = subprocess.run(
            command,
            capture_output=True,  # capture output
            text=True  # read output as text
        )
        # check the validation output
        # print("STDOUT:\n", result.stdout)
        # print("STDERR:\n", result.stderr)

        if result.returncode <= 1:
            parent_path = Path(pdfPath).parent.as_posix()
            parent_path_str = parent_path.replace("/", "-")

            filename = parent_path_str + '-' + Path(pdfPath).stem.split('.')[0] + f".{format}"
            reportPath = Path(reportPath) / profile / filename
            reportPath.parent.mkdir(parents=True, exist_ok=True)

            with open(reportPath, "w", encoding="utf-8") as file:
                file.write(result.stdout)

        return result.returncode, result.stdout, result.stderr  # java exit code and output, error

    except FileNotFoundError:
        print_console_message("error", "Java not found.")
        return -1, "", "Java not found."
    except Exception as e:
        # print(f"Unexpected error: {e}")
        return -1, "", str(e)

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

def find_existing_xml_report(pdfPath: str, subfolderPath: str, profile: str) -> str | None:
    """
    Search for the most recently written XML validation report for the given PDF and profile.
    Returns the XML content string, or None if not found.
    """
    parent_path = Path(pdfPath).parent.as_posix()
    parent_path_str = parent_path.replace("/", "-")
    expected_filename = parent_path_str + "-" + Path(pdfPath).stem.split(".")[0] + ".xml"

    search_root = Path(subfolderPath).parent
    if not search_root.exists():
        return None

    matches = list(search_root.glob(f"reports/*/xml/{profile}/{expected_filename}"))
    matches += list(search_root.glob(f"*/reports/*/xml/{profile}/{expected_filename}"))
    if not matches:
        return None

    most_recent = max(matches, key=lambda p: p.stat().st_mtime)
    return most_recent.read_text(encoding="utf-8")

def validatePdf(pdfPath: str, reportPath: str, subfolderPath: str,
                profiles: list = ["ua1", "wcag"], format: str = "xml",
                xml_only: bool = False) -> list:
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
    filename = pdfPath

    for profile in profiles:
        if xml_only:
            xml_content = find_existing_xml_report(pdfPath, subfolderPath, profile)
            if xml_content is not None:
                try:
                    rules = parseValidationReport(xml_content)
                    if rules:
                        results[profile] = [filename, False, len(rules), rules]
                    else:
                        results[profile] = [filename, True, 0, []]
                except ET.ParseError:
                    results[profile] = [filename, 'Error', 0, []]
                continue

        exitCode, output, _error = runJavaValidation(pdfPath, reportPath, profile, "xml")

        if exitCode == 0:
            results[profile] = [filename, True, 0, []]
        elif exitCode == 1:
            try:
                rules = parseValidationReport(output)
            except ET.ParseError:
                rules = []
                results[profile] = [filename, 'Error', 0, []]
                continue
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
    df.drop_duplicates(inplace=True, ignore_index=True)
    df.to_csv(folder / "vera_validation_results.csv", index=False)

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

    # Both
    both_passed = df[
        (df["ua1"].astype(str).str.upper() == "TRUE") &
        (df["wcag"].astype(str).str.upper() == "TRUE")
    ]
    both_passed_count = len(both_passed)
    both_failed_count = total_files - both_passed_count
    both_success_rate = (both_passed_count / total_files) * 100 if total_files > 0 else 0
    append_to_csv(folder / "summary-total.csv", ["processed total", "passed", "fail", "success %"])
    append_to_csv(folder / "summary-total.csv", [total_files, both_passed_count, both_failed_count, f"{both_success_rate:.0f}%"])

    print_console_section("VALIDATION SUMMARY", "success")
    print_console_key_value_rows([
        (
            "UA1",
            (
                f"{ua1_success_rate:.0f}% | Passed: {ua1_passed_count}, "
                f"Failed: {ua1_failed_count}, Error: {ua1_error_count}, "
                f"Violations: {ua1_violation_total}"
            )
        ),
        (
            "WCAG",
            (
                f"{wcag_success_rate:.0f}% | Passed: {wcag_passed_count}, "
                f"Failed: {wcag_failed_count}, Error: {wcag_error_count}, "
                f"Violations: {wcag_violation_total}"
            )
        ),
        (
            "Both",
            f"{both_success_rate:.0f}% | Passed: {both_passed_count}, Failed: {both_failed_count}"
        ),
        ("Reports", folder),
    ])

def get_recursive_pdf_file_count(folder_path: Path) -> int:
    '''
    Return the recursive PDF file total for a folder.
    '''
    if not folder_path.exists():
        return 0

    return len(get_pdf_file_paths(folder_path))

def get_subfolder_pdf_file_count(
        folder_path: Path,
        ignored_subfolders: set[str] = None) -> int:
    '''
    Return recursive PDF total across immediate subfolders, excluding ignored names.
    '''
    if not folder_path.exists():
        return 0

    ignored_subfolders = ignored_subfolders or set()
    pdf_total = 0
    for subfolder_path in folder_path.iterdir():
        if not subfolder_path.is_dir():
            continue
        if subfolder_path.name in ignored_subfolders:
            continue
        pdf_total += get_recursive_pdf_file_count(subfolder_path)

    return pdf_total

def write_workspace_file_count_report(report_path: Path, workspace_folder_path: Path) -> None:
    '''
    Write recursive PDF counts to workspace-file-count.csv.
    '''
    ignored_subfolders = {"reports", "debug"}
    workspace_folder_count = get_subfolder_pdf_file_count(
        workspace_folder_path,
        ignored_subfolders
    )
    count_targets = [("Total Files", workspace_folder_count)]
    if workspace_folder_path.exists():
        for subfolder_path in sorted(workspace_folder_path.iterdir(), key=lambda path: path.name):
            if not subfolder_path.is_dir():
                continue
            if subfolder_path.name in ignored_subfolders:
                continue
            count_targets.append((subfolder_path.name, subfolder_path))

    with open(report_path / "workspace-file-count.csv", "w", newline="", encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow([folder_name for folder_name, _ in count_targets])
        writer.writerow([
            folder_value if isinstance(folder_value, int)
            else get_recursive_pdf_file_count(folder_value)
            for _, folder_value in count_targets
        ])

def validate_pdf_multiprocess(
        workspace_folder_path: Path,
        file_paths: list,
        timestamp: str = None,
        subfolder: str = None,
        report_base_path: Path = None,
        relative_base_paths: list[Path] = None,
        xml_only: bool = False) -> None:
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
    :param report_base_path: Optional folder where reports should be written.
    :type report_base_path: Path
    :param relative_base_paths: Optional base paths used to compute relative file paths.
    :type relative_base_paths: list[Path]
    '''
    profiles = ["ua1", "wcag"]
    # Prepare the validation file paths, which are tuples of (inputPdfPath, reportPath).
    validation_file_paths = []

    # Create a timestamped report folder inside the reports folder.
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_root_path = report_base_path if report_base_path else workspace_folder_path.parent / "reports"
    report_path = report_root_path / \
        f"{timestamp}-{subfolder if subfolder else 'files'}"
    report_path_xml = report_path / "xml"
    report_path_xml.mkdir(parents=True, exist_ok=True)

    for file_path in file_paths:
        validation_file_paths.append(
            (str(file_path),
             str(report_path_xml),
             str(workspace_folder_path),
             profiles,
             "xml",
             xml_only)
        )

    print_console_section("VALIDATING PDFS", "info")
    results = progress_starmap(validatePdf, validation_file_paths, total=len(file_paths))

    csv_results = []
    for result in results:
        csv_result = result[:]
        csv_result[0] = get_relative_report_path(
            csv_result[0],
            workspace_folder_path,
            relative_base_paths
        )
        csv_results.append(csv_result)
        
        # delete columns with rule details to reduce memory usage
        del csv_result[5:]

    write_validation_report(report_path, csv_results)
    run_report_generation(report_path, profiles)
    write_workspace_file_count_report(report_path, workspace_folder_path)

    return results
