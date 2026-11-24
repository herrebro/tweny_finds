"""Configuración. En el servidor se rellena con variables de entorno; en local, con un archivo .env."""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", BASE / "data"))
DB_PATH = DATA_DIR / "tweny.db"
PHOTOS_DIR = DATA_DIR / "photos"
MODELS_DIR = DATA_DIR / "models"
CHANNELS_FILE = BASE / "channels.txt"

IMAGE_MODEL = os.getenv("IMAGE_MODEL", "Qdrant/clip-ViT-B-32-vision")
SCRAPE_INTERVAL_MIN = int(os.getenv("SCRAPE_INTERVAL_MIN", "30"))
FIRST_RUN_PAGES = int(os.getenv("FIRST_RUN_PAGES", "30"))  # unas 20 publicaciones por página
UPDATE_PAGES = int(os.getenv("UPDATE_PAGES", "10"))


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Falta la variable {name}")
    return value


def allowed_users() -> set[int]:
    raw = os.getenv("ALLOWED_USERS", "")
    return {int(x) for x in re.split(r"[,\s]+", raw) if x}


def normalize_channel(ref: str) -> str:
    ref = ref.strip()
    m = re.match(r"(?:https?://)?t\.me/(?:s/)?([A-Za-z0-9_]+)", ref)
    return m.group(1) if m else ref.lstrip("@")


def channels() -> list[str]:
    """Canales: variable CHANNELS (separados por comas o espacios) o, si no existe, channels.txt."""
    raw = os.getenv("CHANNELS", "").strip()
    if raw:
        refs = [r for r in re.split(r"[,\s]+", raw) if r]
    else:
        lines = CHANNELS_FILE.read_text(encoding="utf-8").splitlines() if CHANNELS_FILE.exists() else []
        refs = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
    out = [normalize_channel(r) for r in refs]
    if not out:
        raise SystemExit("No hay canales: pon alguno en channels.txt o en la variable CHANNELS")
    return out
