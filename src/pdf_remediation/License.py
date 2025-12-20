from .utilities.PDFix import License
import json

if __name__ == "__main__":
    license = License()

    if license['status']['authorized'] == "false":
        print("License is not active.")
    else:
        print("License is active.")

    print()
    print(json.dumps(license, indent=2))