"""Entry point that works from any folder: ``python tools/hr-slim/run.py <command>``."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hrbuild.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
