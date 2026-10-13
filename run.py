"""Start Trivela and the football analysis engine.

    python run.py                 -> http://127.0.0.1:8000
    PORT=9000 python run.py       -> another port
    HOST=0.0.0.0 python run.py    -> reachable from other devices on your network
"""
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
APP_DIR = ROOT_DIR / "app"


def load_dotenv(path):
    """Read KEY=VALUE lines from a local .env file into the environment (without overriding real variables)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv(ROOT_DIR / ".env")
sys.path.insert(0, str(APP_DIR))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        log_level="info",
    )
