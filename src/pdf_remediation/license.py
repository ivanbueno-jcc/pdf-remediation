'''
Check and display license information.
'''
import json
from .utilities.pdfix import license_status

if __name__ == "__main__":
    pdfix_license = license_status()

    if pdfix_license['status']['authorized'] == "false":
        print("License is not active.")
    else:
        print("License is active.")

    print()
    print(json.dumps(pdfix_license, indent=2))
