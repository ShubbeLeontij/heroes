import sys

from ..cli import parse_args
from .app import run_app

sys.exit(run_app(parse_args(sys.argv[1:])))
