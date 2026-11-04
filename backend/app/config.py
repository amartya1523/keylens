"""Settings loaded from backend/.env (if present) and the environment."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = Path(os.environ.get("KEYLENS_DB") or DATA_DIR / "keylens.db")
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "KEYLENS_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if o.strip()
]

SESSION_DAYS = 14
