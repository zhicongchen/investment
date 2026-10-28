import sys
from pathlib import Path

# The backend runs from backend/ (`uvicorn app.main:app`), so make the repo-root
# `shared` package importable.
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)
