'''
Deactivate PDFix license.
'''
from .utilities.PDFix import license_status, license_deactivate

if __name__ == "__main__":
    LICENSE = license_status()
    if LICENSE["status"]["authorized"] == "false":
        print("License is not active.  Deactivation not required.")
    else:
        if license_deactivate():
            print("License has been successfully deactivated.")
