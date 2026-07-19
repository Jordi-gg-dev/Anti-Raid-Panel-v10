"""AstroCube Panel - Configuración (lee .env)."""

import os
from dotenv import load_dotenv

load_dotenv()


def _parse_id_list(raw: str) -> list[int]:
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


BOT_TOKEN: str = os.getenv("DISCORD_TOKEN", "")

DISCORD_CLIENT_ID: str = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET: str = os.getenv("DISCORD_CLIENT_SECRET", "")
REDIRECT_URI: str = os.getenv("REDIRECT_URI", "http://localhost:5000/callback")

OWNER_IDS: list[int] = _parse_id_list(os.getenv("OWNER_IDS", ""))

FLASK_SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "cambia-esto-por-algo-aleatorio")

# Ruta al archivo de base de datos SQLite. En Railway, apunta esto a la
# carpeta donde montes el Volume persistente (ej: /data/antiraid.db) para que
# no se borre en cada redeploy. En local, por defecto usa data/antiraid.db
# junto al bot si la carpeta del panel está al lado de AstroCube-AntiRaid-Bot.
DB_PATH: str = os.getenv("DB_PATH") or os.path.join(
    os.path.dirname(__file__), "..", "AstroCube-AntiRaid-Bot", "data", "antiraid.db"
)

PANEL_PORT: int = int(os.getenv("PORT") or os.getenv("PANEL_PORT", "5000"))

BOT_NAME = "AstroCube Anti-Raid"
