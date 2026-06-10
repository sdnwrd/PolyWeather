"""Pytest config: ensure the repo root is importable so tests can
`import journal`, `import signals`, etc. (modules live at the repo root,
not under a package)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
