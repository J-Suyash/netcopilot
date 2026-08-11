"""Make the `netcopilot` package importable from the repo root.

Works regardless of whether the project is pip-installed (uv tool pytest,
bare CI runners, hermes verify) — no environment assumptions.
"""

import os
import sys
from pathlib import Path

# C16: pin the hub instead of inheriting the default. The N29 confinement rule
# assumes os_ken.lib.hub does not monkey-patch sockets under pytest, which is
# only true for the native hub — make that explicit rather than ambient.
os.environ.setdefault("OSKEN_HUB_TYPE", "native")

sys.path.insert(0, str(Path(__file__).resolve().parent))
