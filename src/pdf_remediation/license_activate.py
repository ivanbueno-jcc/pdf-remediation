'''
Activate PDFix license using provided license key.
'''

import sys
from .utilities.PDFix import license_activate

if __name__ == "__main__":
    LICENSE_KEY = ""
    if len(sys.argv) > 1:
        LICENSE_KEY = sys.argv[1]
    else:
        print("Missing argument. Please provide a license key.")
        sys.exit()

    if LICENSE_KEY:
        if license_activate(LICENSE_KEY):
            print("License activated successfully.")
        else:
            print("License activation failed.")
