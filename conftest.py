"""Make the `netcopilot` package importable from the repo root.

Works regardless of whether the project is pip-installed (uv tool pytest,
bare CI runners, hermes verify) — no environment assumptions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
