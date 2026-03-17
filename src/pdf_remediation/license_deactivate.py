'''
Deactivate PDFix license.
'''
from .utilities.pdfix import license_status, license_deactivate
from .utilities.resources import print_console_banner, print_console_message

if __name__ == "__main__":
    print_console_banner("DEACTIVATE PDFIX LICENSE")
    LICENSE = license_status()
    if LICENSE["status"]["authorized"] == "false":
        print_console_message("warn", "License is not active. Deactivation is not required.")
    else:
        if license_deactivate():
            print_console_message("success", "License has been successfully deactivated.")
