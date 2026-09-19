from __future__ import annotations

from pathlib import Path
import sys


SRC = Path(__file__).parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
