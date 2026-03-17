'''
Activate PDFix license using provided license key.
'''

import sys
from .utilities.pdfix import license_activate
from .utilities.resources import print_console_banner, print_console_message

if __name__ == "__main__":
    LICENSE_KEY = ""
    print_console_banner("ACTIVATE PDFIX LICENSE")
    if len(sys.argv) > 1:
        LICENSE_KEY = sys.argv[1]
    else:
        print_console_message("error", "Missing argument. Please provide a license key.")
        sys.exit()

    if LICENSE_KEY:
        if license_activate(LICENSE_KEY):
            print_console_message("success", "License activated successfully.")
        else:
            print_console_message("error", "License activation failed.")
