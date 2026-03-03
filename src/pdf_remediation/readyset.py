'''
Backward-compatible wrapper for the unified fleet module.
'''

import sys

from .fleet import main as fleet_main


def main() -> int:
    '''
    Delegate to `pdf_remediation.fleet go`.
    '''
    return fleet_main(['go', *sys.argv[1:]])


if __name__ == '__main__':
    raise SystemExit(main())
