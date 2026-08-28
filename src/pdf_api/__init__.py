'''
Single-PDF remediation pipeline extracted from the pdf_remediation batch tooling.

The batch pipeline expresses its decisions through a project/workspace folder
tree: a file's fate is recorded by which directory it ends up in. This package
runs the same sequence for one PDF and returns the outcome directly, so no
folder structure is required and nothing has to be inferred from the filesystem
afterwards.
'''

APP_NAME = "PDF Remediation API"
APP_VERSION = "0.1.0"
