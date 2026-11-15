"""Lets `python -m rfq_parser parse ...` run the command line."""

import sys

from .cli import main

sys.exit(main())
