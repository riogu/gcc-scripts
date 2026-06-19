#!/usr/bin/env python3
import sys
from pathlib import Path

# make sure the package next to this script is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.__main__ import main
main()
