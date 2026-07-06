# Ensures the repo root is on sys.path so `import streamcurves` / `import views`
# work from pytest regardless of invocation directory (same pattern as DEEP).

# Render plots on the non-interactive Agg backend (matches app.py) so the test
# process never touches an interactive backend's GUI/font-cache cold start.
import matplotlib

matplotlib.use("Agg")
