#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# PDFix Report Generator
#
# Copyright (c) PDFix s.r.o.
#
# This script is provided under the free PDFix license.
# You are free to use, modify, and redistribute this script as part of
# PDF validation and reporting workflows.
#
# Website: https://pdfix.net
# Contact: support@pdfix.net
#
# This software is provided "as is", without warranty of any kind.
# ---------------------------------------------------------------------------
"""
report_generator.py

Standalone report generator for veraPDF XML validation reports.

Purpose
-------
Given a directory of veraPDF XML reports, this script:
  - parses all reports
  - builds the same summary artifacts used by the "report" phase in your pipeline:
      * verapdf-compliance-report.txt
      * verapdf-clause-summary.csv
      * verapdf-file-summary.csv
      * output.txt (synthetic log, for HTML step parsing)
  - generates an HTML report file summarizing compliance statistics.

Inputs
------
  --xml-dir     Directory containing veraPDF XML reports
  --output-dir  Directory where CSV/TXT/HTML outputs will be written

Optional
--------
  --state       Label shown in the report header (default: "customer")
  --company     Company name shown in the report header (default: "PDFix")
  --logo-url    Optional logo URL/path for the report header

Example
-------
  python3 report_generator.py --xml-dir vera_xml --output-dir report_out --state California
"""

from __future__ import annotations

import os
import re
import csv
import time
import argparse
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
from .utilities.Resources import ROOT_DIR, OUTPUT_DIR, INPUT_DIR, REPORTS_DIR

# Inline logo (optimized WEBP 56x56)
INLINE_LOGO_DATA_URI = "data:image/webp;base64,UklGRpgFAABXRUJQVlA4WAoAAAAQAAAANwAANwAAQUxQSN4CAAABkGzbtmk769m2bdRi27Zt23wqxbb5GNu2k2fbZnJ34a69zzr35gMiwoHbSIqU7DEtVXffF+C/08jeKzDQy95ItTgPCrv2La+ipqYi79u1sEHOKsJiZHQhk7AweqSFfKyW/2Ykfy+3kofmZHSPeGeypgy84pgs47zI9E9jMk3rT2R2DZNtzWwSS5uZCmxeSrmPLj/njx1PdVxkrXKRvtuvGu2ANiDHSOWaVfeTwDODoVtdWXT8p1y1DE8h2vEMkWosi34KtG7x2iImKTB/52jIwPQaQygmCbBMZJwNj+ITqMZ/ZZyJljzLmIpdxmGWqGoSzTAjmcodidCIFpKZrxKi0VFzLhKg2GXtHFZDo+RBoogiZyWDmIACOwDo+Y3CrWCwucsEDlISJiIZlT/bo3+lqFqjCwB9FQLClFwTrpg/ICfkinnfnju/BVwDAMNv4lrCFT7f66LM7uTWZSsT8M0QwD6PCX3XAZD6K6swKUOA2/ZKEXn2AF7lTGzFGj1Adnin5KI7IGwOtDAR5V4AgdLH+0YAV4oPsYZlXOi6f5AqVIEkWM5kDVyNw5ZwvWZjNZMEPSB43BGEhuIzRuIBegvFH71FPWBGIZMmzx59EcmGMBOut51jvFJfBNcY0aehAKAxIIWRuIbPb6qrAEArmtEIQ3kiUjBdD/3UjmYSg3B+SdwJBs7BiRzS+dWIJlC9wQAEup6h1gsYKc2HboB0ssVfOj1Xsj4R62HzfhtABLy7wZXawFu0egjLxKSPAey4TMZetgak7poKSv0FS+FNjCcXvEN/USyXcrFs/05U7wn9hVUv4oLXVdAW4r24DXjH9xdKP/vHtVWDTbXCkjyG21/c+mtT+meeNSL4PhPbcthGSVuuf0r2a0Ed1ZhJSOaXHgCwGPdr2nxQ2wrA+YyCNIlEdZ5bhOYD6jzyY/Eq8sTVgucRtc0/apu31DbfqW2eVNf8qq55WU3zuZr+D/xvAlZQOCCUAgAAsA8AnQEqOAA4AD5tLpRGpCKiISgb/ACADYlsDbAE8mlb8A55SafDfx5/Gb5VKX/V/vh+Q3OnV//lft291v9u9mfmAfoB0nfMB+x3+A/sHvL/x3+AewD0AP3Q6wn0AP1m9Kr/qf8r4Dv2G/az2jP/t1gBWkZU0Lx4hQfAOsNGHJ9wwlJxOMAA/v6HwZ6lPUxaOlC2DmHebi7/IJbbENAasWeeHpLnuP2c+eyJeyC3ArDomVTuPWTOQdfnP+AOxO6aLhqdGdMFdfVrLODye5AJL9/errL7CYfTnt3Pq+6p1wOmYz2pS4Nl6toPGmsn+/oSTQOR8k8VAyEL4/++Af//2rTWkUmS0os0LgYgKomhgiOMw05rsbY1NeNWv8+WvLT1NR5//tK3DCLhLKXwHeL1lJXNzGTqqd3D/jNaLnK9Fviduujl1ZUyolWj/PTWyq2/+cXn/5n6oXQcBoy6MJGBDbR1dM8XRv+RaLuGiAyhf1Jt80KMUgB35gBfmOOVzDWBH9pvE7MNKh5ShQWPMDUf3jQFJE/WZH94uNrCmOAGgdIPmvA9y+vB7rA47Z/4ejD0cYsuPmeMXI/ZM7J+hdfMQIabKRndquyrfURC8Rnfg/zEmrVBypJlZpPQQ2IVrixSJ1vC/C+IMQnTY+c3O2MEY2vkeRfm+rEmXiXBnoppa+4E8pOgeeuJ8qARNEndhg/gzfztg43PYIdb9OzReDDbGGk/7ahP2ac+cQJNQ5LrRWK8GN57R9zd//2/cELA304Q1n3ebpbbxpnGjTchzUq7IAmwv0Nvi4cXp9U+a22AtgeXtAtcf0nMKP1The98AEFB591NsUSXp+vc5Rw0Ep3F629AZEpqUlecRc4i/+RnMwAAAAAA"


# ---------------------------------------------------------------------------
# Parsing veraPDF XML reports -> per-file issues summary
# ---------------------------------------------------------------------------

SUMMARY_LOCK = threading.Lock()


def collect_verapdf_issues_from_report(
    report_path: str,
    summary: Dict[str, List[dict]],
    lock: threading.Lock = SUMMARY_LOCK,
) -> None:
    """
    Parse a veraPDF XML report and store a per-file summary of issues into `summary`.

    - summary: shared dict {pdf_path: [ {clause, description}, ... ]}
    - one entry per clause per file (duplicates ignored)
    - issues are sorted by clause
    """
    tree = ET.parse(report_path)
    root = tree.getroot()

    # 1) Find original PDF name/path (veraPDF typically stores it in jobs/job/item/name)
    pdf_name_el = root.find(".//jobs/job/item/name")
    if pdf_name_el is not None and pdf_name_el.text:
        pdf_path = pdf_name_el.text.strip()
    else:
        # Fallback: use XML file path if the PDF name cannot be found
        pdf_path = report_path

    issues: List[dict] = []
    seen_clauses = set()

    # Typical veraPDF structure:
    #   <rule status="failed" clause="7.1"><description>...</description></rule>
    for rule in root.findall(".//rule"):
        status = (rule.get("status") or "").lower()
        if status and status != "failed":
            continue

        clause = (
            rule.get("clause")
            or rule.get("clauseId")
            or rule.get("test")
            or "unknown"
        )

        if clause in seen_clauses:
            continue

        desc_el = rule.find("description")
        description = (desc_el.text or "").strip() if desc_el is not None else ""

        seen_clauses.add(clause)
        issues.append({"clause": clause, "description": description})

    # Fallback: some report variants may use <check> instead of <rule>
    if not issues:
        for check in root.findall(".//check"):
            status = (check.get("status") or "").lower()
            if status and status != "failed":
                continue

            clause = check.get("clause") or "unknown"
            if clause in seen_clauses:
                continue

            desc_el = check.find("description")
            description = (desc_el.text or "").strip() if desc_el is not None else ""

            seen_clauses.add(clause)
            issues.append({"clause": clause, "description": description})

    issues.sort(key=lambda x: x["clause"])

    with lock:
        summary[pdf_path] = issues


def load_xml_reports(xml_dir: str, max_threads: int = 4) -> Dict[str, List[dict]]:
    """
    Parse all *.xml files in xml_dir into {pdf_path: issues[]} summary.
    """
    xml_dir = os.path.abspath(xml_dir)
    files = [
        os.path.join(xml_dir, f)
        for f in os.listdir(xml_dir)
        if os.path.isfile(os.path.join(xml_dir, f)) and f.lower().endswith(".xml")
    ]
    files.sort()

    summary: Dict[str, List[dict]] = {}
    lock = threading.Lock()

    # Simple threaded parse (keeps it quick even for many files)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=max_threads) as ex:
        futs = {ex.submit(collect_verapdf_issues_from_report, p, summary, lock): p for p in files}
        for fut in as_completed(futs):
            # Raise parsing errors early with file context
            try:
                fut.result()
            except Exception as e:
                src = futs[fut]
                raise RuntimeError(f"Failed to parse XML report: {src}: {e}") from e

    return summary


# ---------------------------------------------------------------------------
# Artifact writers (CSV/TXT) - same semantics as batch.py
# ---------------------------------------------------------------------------

def write_compliance_report(summary: Dict[str, List[dict]], txt_path: str) -> None:
    compliant_files = []
    non_compliant_files = []

    for pdf_path, issues in summary.items():
        if len(issues) == 0:
            compliant_files.append(pdf_path)
        else:
            non_compliant_files.append(pdf_path)

    compliant_files_sorted = sorted(compliant_files)
    non_compliant_files_sorted = sorted(non_compliant_files)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"Compliant File Count: {len(compliant_files_sorted)}\n")
        f.write(f"Non-Compliant File Count: {len(non_compliant_files_sorted)}\n\n")

        f.write("Compliant Files:\n")
        for path in compliant_files_sorted:
            f.write(os.path.basename(path) + "\n")
        f.write("\n")

        f.write("Non-Compliant Files:\n")
        for path in non_compliant_files_sorted:
            f.write(os.path.basename(path) + "\n")


def write_clause_summary_csv(summary: Dict[str, List[dict]], csv_path: str) -> None:
    # clause -> {"description": str, "count": int}
    clause_stats: Dict[str, dict] = {}

    for _pdf_path, issues in summary.items():
        seen_in_this_file = set()

        for issue in issues:
            clause = issue.get("clause")
            if not clause:
                continue
            if clause in seen_in_this_file:
                continue
            seen_in_this_file.add(clause)

            desc = (issue.get("description") or "").strip()

            if clause not in clause_stats:
                clause_stats[clause] = {"description": desc, "count": 1}
            else:
                clause_stats[clause]["count"] += 1
                if not clause_stats[clause]["description"] and desc:
                    clause_stats[clause]["description"] = desc

    sorted_clauses = sorted(clause_stats.keys())

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Clause", "Count", "Description"])
        for clause in sorted_clauses:
            info = clause_stats[clause]
            writer.writerow([clause, info["count"], info["description"]])


def write_file_summary_csv(summary: Dict[str, List[dict]], csv_path: str) -> None:
    all_clauses = {
        issue["clause"]
        for issues in summary.values()
        for issue in issues
        if "clause" in issue
    }
    clauses = sorted(all_clauses)

    fieldnames = ["file"] + clauses

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for pdf_path in sorted(summary.keys()):
            pdf_name = os.path.basename(pdf_path)
            row = {name: "" for name in fieldnames}
            row["file"] = pdf_name

            present_clauses = {issue["clause"] for issue in summary[pdf_path]}
            for clause in present_clauses:
                if clause in row:
                    row[clause] = "O"
            writer.writerow(row)


def write_synthetic_before_output_txt(path: str, num_files: int, duration_sec: float) -> None:
    """
    html_report.py parses "*-output.txt" to show step overview. Since we only generate
    summaries from existing XML, we write a minimal compatible log.
    """
    with open(path, "w", encoding="utf-8") as f:
        f.write(datetime.now().strftime("%Y-%m-%d-%H%M%S") + "\n")
        # mimic argparse Namespace printout line format used in your pipeline
        f.write("Namespace(operation='report-summary')\n")
        f.write(f"Found {num_files} file(s).\n")
        f.write(f"[TIME] processed in {duration_sec:.2f} seconds\n")


# ---------------------------------------------------------------------------
# HTML generator (copied/adapted from html_report.py to keep the same format)
# ---------------------------------------------------------------------------

def parse_output_txt(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []

    steps: List[dict] = []
    current: Optional[dict] = None

    op_re = re.compile(r"operation='([^']+)'")
    found_re = re.compile(r"Found\s+(\d+)\s+file\(s\)\.")
    time_re = re.compile(r"\[TIME\]\s+processed in\s+([0-9.]+)\s+seconds", re.IGNORECASE)

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\n")

            if "Namespace(" in line and "operation=" in line:
                m = op_re.search(line)
                if m:
                    if current:
                        steps.append(current)
                    current = {"operation": m.group(1), "found_files": None, "duration_sec": None}
                continue

            if "Found" in line and "file(s)" in line:
                m = found_re.search(line)
                if m and current:
                    current["found_files"] = int(m.group(1))
                continue

            if "[TIME]" in line:
                m = time_re.search(line)
                if m and current:
                    try:
                        current["duration_sec"] = float(m.group(1))
                    except ValueError:
                        current["duration_sec"] = None
                continue

    if current:
        steps.append(current)

    return steps


def parse_compliance_txt(path: str) -> Tuple[int, int, List[str], List[str]]:
    compliant_count = 0
    non_compliant_count = 0
    compliant_files: List[str] = []
    non_compliant_files: List[str] = []

    if not os.path.exists(path):
        return 0, 0, [], []

    mode = None

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            if line.startswith("Compliant File Count:"):
                try:
                    compliant_count = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
                continue

            if line.startswith("Non-Compliant File Count:"):
                try:
                    non_compliant_count = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
                continue

            if line.startswith("Compliant Files"):
                mode = "compliant"
                continue
            if line.startswith("Non-Compliant Files"):
                mode = "non_compliant"
                continue

            if mode == "compliant":
                compliant_files.append(line)
            elif mode == "non_compliant":
                non_compliant_files.append(line)

    return compliant_count, non_compliant_count, compliant_files, non_compliant_files


def parse_clause_summary_csv(path: str) -> List[dict]:
    clauses: List[dict] = []
    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                count = int(row.get("Count", "0") or 0)
            except ValueError:
                count = 0
            clauses.append({"clause": row.get("Clause", ""), "description": row.get("Description", ""), "count": count})
    return clauses


def parse_file_summary_csv(path: str) -> int:
    if not os.path.exists(path):
        return 0

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        file_names = set()
        for row in reader:
            file_name = (row.get("file") or "").strip()
            if file_name:
                file_names.add(file_name)
        return len(file_names)


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "N/A"
    return f"{seconds:.2f} s"


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def generate_phase_html(
    state_name: str,
    phase: str,
    company: str,    steps: List[dict],
    compliant_count: int,
    non_compliant_count: int,
    clause_stats: List[dict],
    file_summary_file_count: int,
) -> str:
    total_files = compliant_count + non_compliant_count
    total_files_text = str(total_files) if total_files > 0 else "N/A"

    compliant_ratio = (compliant_count / total_files) if total_files > 0 else 0.0
    non_compliant_ratio = 1.0 - compliant_ratio if total_files > 0 else 0.0

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    top_clauses = sorted(clause_stats, key=lambda c: c["count"], reverse=True)[:100]

    op_labels = {
        "font-fix-callas": "Font Fix (Callas)",
        "pdfix-make-accessible": "PDFix - Make Accessible",
        "validation-report": "veraPDF Validation",
        "report-summary": "veraPDF Summary",
    }

    def op_nice_name(op: str) -> str:
        return op_labels.get(op, op)

    html_parts: List[str] = []

    html_parts.append(
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\" />\n"
        f"  <title>{html_escape(company)} – PDF/UA Report – {html_escape(state_name)} – {html_escape(phase.capitalize())}</title>\n"
        "  <style>\n"
        "    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; "
        "           margin: 0; padding: 0; background: #f5f7fb; color: #111; }\n"
        "    .page { max-width: 1200px; margin: 0 auto; padding: 24px 32px 40px; }\n"
        "    .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }\n"
        "    .header-left { display: flex; align-items: center; gap: 16px; }\n"
        "    .logo { width: 56px; height: 56px; border-radius: 12px; background: #fff; "
        "             border: 1px solid #dde2f0; display: flex; align-items: center; "
        "             justify-content: center; font-weight: 700; font-size: 24px; color: #2563eb; }\n"
        "    .meta { font-size: 13px; color: #4b5563; }\n"
        "    h1 { font-size: 24px; margin: 0 0 4px; }\n"
        "    .tag { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }\n"
        "    .tag-report { background: #fef3c7; color: #92400e; }\n"
        "    .tag-after { background: #dcfce7; color: #166534; }\n"
        "    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }\n"
        "    .card { background: #fff; border-radius: 12px; padding: 16px 18px; box-shadow: 0 10px 25px rgba(15,23,42,0.04); "
        "            border: 1px solid #e5e7eb; }\n"
        "    .card-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #6b7280; margin-bottom: 4px; }\n"
        "    .card-value { font-size: 22px; font-weight: 600; margin-bottom: 2px; }\n"
        "    .card-note { font-size: 12px; color: #6b7280; }\n"
        "    .progress-bar { width: 100%; height: 10px; border-radius: 999px; background: #e5e7eb; overflow: hidden; margin-top: 8px; }\n"
        "    .progress-fill-ok { height: 100%; background: linear-gradient(90deg, #22c55e, #16a34a); }\n"
        "    .progress-fill-bad { height: 100%; background: linear-gradient(90deg, #f97316, #dc2626); }\n"
        "    h2 { font-size: 18px; margin: 24px 0 8px; }\n"
        "    h3 { font-size: 15px; margin: 18px 0 6px; }\n"
        "    table { border-collapse: collapse; width: 100%; font-size: 13px; }\n"
        "    th, td { border: 1px solid #e5e7eb; padding: 6px 8px; text-align: left; vertical-align: top; }\n"
        "    th { background: #f3f4f6; font-weight: 600; }\n"
        "    tbody tr:nth-child(even) { background: #f9fafb; }\n"
        "    .pill-ok { display:inline-block; padding: 2px 8px; border-radius:999px; background:#dcfce7; color:#166534; font-size:11px; }\n"
        "    .pill-bad { display:inline-block; padding: 2px 8px; border-radius:999px; background:#fee2e2; color:#b91c1c; font-size:11px; }\n"
        "    .flex { display:flex; gap:8px; align-items:center; }\n"
        "    .muted { color:#6b7280; font-size:12px; }\n"
        "    .section { margin-bottom: 28px; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        "  <div class=\"page\">\n"
    )

    phase_tag_class = "tag-after" if phase.lower() == "after" else "tag-report"
    phase_label = "After PDFix Processing" if phase.lower() == "after" else "Validation Report"

    html_parts.append("    <header class=\"header\">\n")
    html_parts.append("      <div class=\"header-left\">\n")

    # Inline embedded logo (no external dependency)
    html_parts.append(
        f"        <img src=\"{INLINE_LOGO_DATA_URI}\" alt=\"{html_escape(company)} logo\" "
        "class=\"logo\" style=\"object-fit:contain;\" />\n"
    )

    html_parts.append("        <div>\n")
    html_parts.append(f"          <h1>{html_escape(company)} – PDF/UA Report</h1>\n")
    html_parts.append(
        f"          <div class=\"meta\">State: <strong>{html_escape(state_name)}</strong> &nbsp;·&nbsp; "
        f"Phase: <span class=\"tag {phase_tag_class}\">{html_escape(phase_label)}</span></div>\n"
    )
    html_parts.append(f"          <div class=\"meta\">Generated at: {html_escape(generated_at)}</div>\n")
    html_parts.append("        </div>\n")
    html_parts.append("      </div>\n")
    html_parts.append("    </header>\n")

    html_parts.append("    <section class=\"section\">\n")
    html_parts.append("      <div class=\"cards\">\n")

    html_parts.append("        <div class=\"card\">\n")
    html_parts.append("          <div class=\"card-label\">Total PDFs Validated</div>\n")
    html_parts.append(f"          <div class=\"card-value\">{html_escape(total_files_text)}</div>\n")
    html_parts.append("          <div class=\"card-note\">Based on veraPDF compliance report.</div>\n")
    html_parts.append("        </div>\n")

    html_parts.append("        <div class=\"card\">\n")
    html_parts.append("          <div class=\"card-label\">Compliant</div>\n")
    html_parts.append(f"          <div class=\"card-value\">{compliant_count}</div>\n")
    html_parts.append("          <div class=\"card-note flex\">\n")
    html_parts.append("            <span class=\"pill-ok\">PDF/UA-1 Pass</span>\n")
    html_parts.append(f"            <span class=\"muted\">{compliant_ratio*100:.1f}% of files</span>\n")
    html_parts.append("          </div>\n")
    html_parts.append(
        "          <div class=\"progress-bar\"><div class=\"progress-fill-ok\" "
        f"style=\"width:{compliant_ratio*100:.1f}%\"></div></div>\n"
    )
    html_parts.append("        </div>\n")

    html_parts.append("        <div class=\"card\">\n")
    html_parts.append("          <div class=\"card-label\">Non-Compliant</div>\n")
    html_parts.append(f"          <div class=\"card-value\">{non_compliant_count}</div>\n")
    html_parts.append("          <div class=\"card-note flex\">\n")
    html_parts.append("            <span class=\"pill-bad\">Requires Fixes</span>\n")
    html_parts.append(f"            <span class=\"muted\">{non_compliant_ratio*100:.1f}% of files</span>\n")
    html_parts.append("          </div>\n")
    html_parts.append(
        "          <div class=\"progress-bar\"><div class=\"progress-fill-bad\" "
        f"style=\"width:{non_compliant_ratio*100:.1f}%\"></div></div>\n"
    )
    html_parts.append("        </div>\n")

    if steps:
        total_duration = sum((s.get("duration_sec") or 0.0) for s in steps)
        html_parts.append("        <div class=\"card\">\n")
        html_parts.append("          <div class=\"card-label\">Batch Runtime</div>\n")
        html_parts.append(f"          <div class=\"card-value\">{total_duration:.1f} s</div>\n")
        html_parts.append("          <div class=\"card-note\">Sum of all processing steps in this phase.</div>\n")
        html_parts.append("        </div>\n")

    html_parts.append("      </div>\n")
    html_parts.append("    </section>\n")

    html_parts.append("    <section class=\"section\">\n")
    html_parts.append("      <h2>Processing Steps Overview</h2>\n")
    if not steps:
        html_parts.append("      <p class=\"muted\">No processing steps could be parsed from the log.</p>\n")
    else:
        html_parts.append("      <table>\n")
        html_parts.append("        <thead>\n")
        html_parts.append("          <tr><th>Step</th><th>Operation ID</th><th>Files</th><th>Duration</th></tr>\n")
        html_parts.append("        </thead>\n")
        html_parts.append("        <tbody>\n")
        for s in steps:
            op = s.get("operation", "")
            html_parts.append("          <tr>\n")
            html_parts.append(f"            <td>{html_escape(op_nice_name(op))}</td>\n")
            html_parts.append(f"            <td><code>{html_escape(op)}</code></td>\n")
            files_txt = str(s.get("found_files")) if s.get("found_files") is not None else "N/A"
            html_parts.append(f"            <td>{html_escape(files_txt)}</td>\n")
            html_parts.append(f"            <td>{html_escape(format_duration(s.get('duration_sec')))}</td>\n")
            html_parts.append("          </tr>\n")
        html_parts.append("        </tbody>\n")
        html_parts.append("      </table>\n")
    html_parts.append("    </section>\n")

    html_parts.append("    <section class=\"section\">\n")
    html_parts.append("      <h2>Top Clauses Across All Files</h2>\n")
    if not clause_stats:
        html_parts.append("      <p class=\"muted\">No clause statistics available.</p>\n")
    else:
        html_parts.append("      <h3>Most Frequent Clauses</h3>\n")
        html_parts.append("      <table>\n")
        html_parts.append("        <thead><tr><th>Clause</th><th>Description</th><th>Files Affected</th></tr></thead>\n")
        html_parts.append("        <tbody>\n")
        for c in top_clauses:
            html_parts.append("          <tr>\n")
            html_parts.append(f"            <td><code>{html_escape(c['clause'])}</code></td>\n")
            html_parts.append(f"            <td>{html_escape(c['description'])}</td>\n")
            html_parts.append(f"            <td>{c['count']}</td>\n")
            html_parts.append("          </tr>\n")
        html_parts.append("        </tbody>\n")
        html_parts.append("      </table>\n")

        if file_summary_file_count:
            html_parts.append(
                "      <p class=\"muted\" style=\"margin-top:8px;\">"
                f"File-level details per clause are available for {file_summary_file_count} files "
                "(see the CSV summary generated alongside this report).</p>\n"
            )
    html_parts.append("    </section>\n")

    html_parts.append("    <footer class=\"section\">\n")
    html_parts.append("      <p class=\"muted\">Generated by PDFix batch reporting pipeline.</p>\n")
    html_parts.append("      <p class=\"muted\"><a href=\"pdfix.net\">pdfix.net</a> | <a href=\"mailto:support@pdfix.net\">support@pdfix.net</a></p>\n")
    html_parts.append("    </footer>\n")

    html_parts.append("  </div>\n</body>\n</html>\n")

    return "".join(html_parts)


def build_report(output_dir: str, state: str, company: str) -> str:
    """
    Build "report" HTML report from files in output_dir.
    Returns generated HTML path.
    """
    output_txt = os.path.join(output_dir, "output.txt")
    compliance_txt = os.path.join(output_dir, "verapdf-compliance-report.txt")
    clause_csv = os.path.join(output_dir, "verapdf-clause-summary.csv")
    file_summary_csv = os.path.join(output_dir, "verapdf-file-summary.csv")

    steps = parse_output_txt(output_txt)
    compliant_count, non_compliant_count, _, _ = parse_compliance_txt(compliance_txt)
    clause_stats = parse_clause_summary_csv(clause_csv)
    file_summary_file_count = parse_file_summary_csv(file_summary_csv)

    html = generate_phase_html(
        state_name=state,
        phase="report",
        company=company,
                steps=steps,
        compliant_count=compliant_count,
        non_compliant_count=non_compliant_count,
        clause_stats=clause_stats,
        file_summary_file_count=file_summary_file_count,
    )

    os.makedirs(output_dir, exist_ok=True)
    state_slug = state.lower().replace(" ", "-")
    out_name = f"{state_slug}-report.html"
    out_path = os.path.join(output_dir, out_name)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate 'report' report artifacts (CSV/TXT/HTML) from veraPDF XML reports."
    )
    parser.add_argument("--xml-dir", required=True, help="Directory containing veraPDF XML reports (*.xml).")
    parser.add_argument("--output-dir", required=True, help="Directory to write report artifacts (csv, txt, html).")
    parser.add_argument("--state", default="customer", help="State/Customer name shown in the report header.")
    parser.add_argument("--company", default="PDFix", help="Company name shown in the report header.")
    parser.add_argument("--threads", type=int, default=4, help="Number of parsing threads (default: 4).")

    args = parser.parse_args()

    xml_dir = os.path.abspath(args.xml_dir)
    out_dir = os.path.abspath(args.output_dir)

    if not os.path.isdir(xml_dir):
        raise SystemExit(f"Error: --xml-dir is not a directory or does not exist: {xml_dir}")

    os.makedirs(out_dir, exist_ok=True)

    t0 = time.perf_counter()
    summary = load_xml_reports(xml_dir, max_threads=max(1, args.threads))
    duration = time.perf_counter() - t0

    num_files = len(summary)

    # Write "report" artifacts (same naming as your pipeline)
    compliance_txt = os.path.join(out_dir, "verapdf-compliance-report.txt")
    clause_csv = os.path.join(out_dir, "verapdf-clause-summary.csv")
    file_csv = os.path.join(out_dir, "verapdf-file-summary.csv")
    out_txt = os.path.join(out_dir, "output.txt")

    write_file_summary_csv(summary, file_csv)
    write_clause_summary_csv(summary, clause_csv)
    write_compliance_report(summary, compliance_txt)
    write_synthetic_before_output_txt(out_txt, num_files=num_files, duration_sec=duration)

    html_path = build_report(out_dir, state=args.state, company=args.company)

    print(f"[OK] Parsed XML reports: {num_files}")
    print(f"[OK] Wrote: {file_csv}")
    print(f"[OK] Wrote: {clause_csv}")
    print(f"[OK] Wrote: {compliance_txt}")
    print(f"[OK] Wrote: {out_txt}")
    print(f"[OK] Generated HTML: {html_path}")

    return 0

if __name__ == "__main__":
    #raise SystemExit(main())
    parser = argparse.ArgumentParser(
        description="Generate 'report' report artifacts (CSV/TXT/HTML) from veraPDF XML reports."
    )
    parser.add_argument("folder", help="Directory containing veraPDF XML reports (*.xml).")
    args = parser.parse_args()

    if args.folder:
        xml_dir = REPORTS_DIR / args.folder
        # check if xml_dir exists
        if not xml_dir.exists():
            raise SystemExit(f"{args.folder} does not exist. Run Validate first.")
        # check if xml_dir contains xml files
        if not any(xml_dir.glob("*.xml")):
            raise SystemExit(f"No XML files found in {args.folder}. Run Validate first.")
        
        out_dir = REPORTS_DIR / args.folder / "summary"
        out_dir.mkdir(exist_ok=True)

        t0 = time.perf_counter()
        summary = load_xml_reports(xml_dir, max_threads=max(1, 4))
        duration = time.perf_counter() - t0

        num_files = len(summary)

        # Write "report" artifacts (same naming as your pipeline)
        compliance_txt = os.path.join(out_dir, "verapdf-compliance-report.txt")
        clause_csv = os.path.join(out_dir, "verapdf-clause-summary.csv")
        file_csv = os.path.join(out_dir, "verapdf-file-summary.csv")
        out_txt = os.path.join(out_dir, "output.txt")

        write_file_summary_csv(summary, file_csv)
        write_clause_summary_csv(summary, clause_csv)
        write_compliance_report(summary, compliance_txt)
        write_synthetic_before_output_txt(out_txt, num_files=num_files, duration_sec=duration)

        html_path = build_report(out_dir, state="California", company="PDFix")

        print(f"[OK] Parsed XML reports: {num_files}")
        print(f"[OK] Wrote: {file_csv}")
        print(f"[OK] Wrote: {clause_csv}")
        print(f"[OK] Wrote: {compliance_txt}")
        print(f"[OK] Wrote: {out_txt}")
        print(f"[OK] Generated HTML: {html_path}")

