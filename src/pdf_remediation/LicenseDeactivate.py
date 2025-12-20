from .utilities.PDFix import License, LicenseDeactivate
import json
import sys

if __name__ == "__main__":
    license = License()
    if license["status"]["authorized"] == "false":
        print("License is not active.  Deactivation not required.")
    else:
        is_deactivated = LicenseDeactivate()
        if is_deactivated:
            print("License has been successfully deactivated.")