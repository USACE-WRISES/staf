import pathlib
import sys

# Ensure the repo root (containing the `sfari` package + `data/`) is importable.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
