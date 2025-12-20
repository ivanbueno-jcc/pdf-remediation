from .utilities.PDFix import License, LicenseActivate
import json
import sys

if __name__ == "__main__":
    licenseKey = ''
    if len(sys.argv) > 1:
        licenseKey = sys.argv[1]
    else:
        print("Missing argument. Please provide a license key.")
        exit()

    if licenseKey:
        is_license_activated = LicenseActivate(licenseKey)
        if is_license_activated:            
            print("License activated successfully.")
        else:
            print("License activation failed.")