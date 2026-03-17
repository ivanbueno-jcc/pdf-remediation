'''
Check and display license information.
'''
from .utilities.pdfix import license_status
from .utilities.resources import (
    print_console_banner,
    print_console_json,
    print_console_message,
    print_console_section,
)

if __name__ == "__main__":
    pdfix_license = license_status()

    print_console_banner("PDFIX LICENSE")
    if pdfix_license['status']['authorized'] == "false":
        print_console_message("warn", "License is not active.")
    else:
        print_console_message("success", "License is active.")

    print_console_section("LICENSE DETAILS", "info")
    print_console_json(pdfix_license)
