"""Allow ``python -m gh_ci_truth``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
