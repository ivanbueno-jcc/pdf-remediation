'''
Deactivate PDFix license.
'''
from .utilities.PDFix import License, LicenseDeactivate

if __name__ == "__main__":
    LICENSE = License()
    if LICENSE["status"]["authorized"] == "false":
        print("License is not active.  Deactivation not required.")
    else:
        if LicenseDeactivate():
            print("License has been successfully deactivated.")
