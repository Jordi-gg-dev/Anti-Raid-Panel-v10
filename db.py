"""
AstroCube Panel - Acceso a la MISMA base de datos SQLite que usa el bot
(data/antiraid.db). El panel escribe ahí y el bot lo lee al instante en su
siguiente evento/comando - no hace falta que el bot este corriendo para usar
el panel.

Tambien guarda aqui datos exclusivos del panel (reportes, tareas, actividad)
que el bot en si no usa, pero conviven en el mismo archivo por simplicidad.
"""

import json
import os
import time
import sqlite3
import threading

import panel_config as config

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    # Si la carpeta de destino todavia no existe, sqlite3 lanza
    # "unable to open database file" en vez de crearla sola. La creamos
    # nosotros para que el panel arranque siempre.
    db_dir = os.path.dirname(os.path.abspath(config.DB_PATH))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


_conn = _connect()
_conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS guild_config (
        guild_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT,
        PRIMARY KEY (guild_id, key)
    );
    CREATE TABLE IF NOT EXISTS antinuke_whitelist (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY (guild_id, user_id));
    CREATE TABLE IF NOT EXISTS antinuke_trusted_bots (guild_id INTEGER NOT NULL, bot_id INTEGER NOT NULL, PRIMARY KEY (guild_id, bot_id));
    CREATE TABLE IF NOT EXISTS antispam_whitelist (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY (guild_id, user_id));
    CREATE TABLE IF NOT EXISTS antiraid_whitelist (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY (guild_id, user_id));
    CREATE TABLE IF NOT EXISTS incidents (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, module TEXT NOT NULL,
        executor_id INTEGER, detail TEXT, action_taken TEXT, created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS backups (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, label TEXT,
        data TEXT NOT NULL, created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS guild_blacklist (guild_id INTEGER PRIMARY KEY, reason TEXT, added_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS user_blacklist (user_id INTEGER PRIMARY KEY, reason TEXT, added_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
        target TEXT NOT NULL, reason TEXT NOT NULL, reporter_id INTEGER,
        status TEXT NOT NULL DEFAULT 'open', notes TEXT,
        created_at INTEGER NOT NULL, resolved_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
        text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS panel_customization (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        custom_css TEXT NOT NULL DEFAULT '',
        custom_js TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS login_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        avatar_url TEXT,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS guild_access_log (
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        username TEXT,
        first_seen INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        visits INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (guild_id, user_id)
    );
    """
)
_conn.commit()


def _execute(query: str, params: tuple = ()):
    with _lock:
        cur = _conn.execute(query, params)
        _conn.commit()
        return cur


def _fetchall(query: str, params: tuple = ()):
    with _lock:
        cur = _conn.execute(query, params)
        return cur.fetchall()


def _fetchone(query: str, params: tuple = ()):
    with _lock:
        cur = _conn.execute(query, params)
        return cur.fetchone()


# --- Config generico ---
def set_config(guild_id: int, key: str, value) -> None:
    _execute(
        "INSERT INTO guild_config (guild_id, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id, key) DO UPDATE SET value=excluded.value",
        (guild_id, key, str(value)),
    )


def get_config(guild_id: int, key: str, default=None):
    row = _fetchone("SELECT value FROM guild_config WHERE guild_id=? AND key=?", (guild_id, key))
    return row[0] if row else default


def get_bool(guild_id: int, key: str, default: bool = False) -> bool:
    return get_config(guild_id, key, "1" if default else "0") == "1"


def get_int_pair(guild_id: int, key: str, default: tuple[int, int]) -> tuple[int, int]:
    value = get_config(guild_id, key)
    if not value:
        return default
    try:
        c, s = value.split(":")
        return int(c), int(s)
    except (ValueError, AttributeError):
        return default


def set_int_pair(guild_id: int, key: str, count: int, seconds: int):
    set_config(guild_id, key, f"{count}:{seconds}")


# --- Whitelists ---
def _wl_add(table, guild_id, entity_id, col):
    _execute(f"INSERT OR IGNORE INTO {table} (guild_id, {col}) VALUES (?, ?)", (guild_id, entity_id))


def _wl_remove(table, guild_id, entity_id, col):
    _execute(f"DELETE FROM {table} WHERE guild_id=? AND {col}=?", (guild_id, entity_id))


def _wl_list(table, guild_id, col):
    return [r[0] for r in _fetchall(f"SELECT {col} FROM {table} WHERE guild_id=?", (guild_id,))]


def antinuke_whitelist_add(g, u): _wl_add("antinuke_whitelist", g, u, "user_id")
def antinuke_whitelist_remove(g, u): _wl_remove("antinuke_whitelist", g, u, "user_id")
def antinuke_whitelist_list(g): return _wl_list("antinuke_whitelist", g, "user_id")

def antinuke_trustedbot_add(g, b): _wl_add("antinuke_trusted_bots", g, b, "bot_id")
def antinuke_trustedbot_remove(g, b): _wl_remove("antinuke_trusted_bots", g, b, "bot_id")
def antinuke_trustedbot_list(g): return _wl_list("antinuke_trusted_bots", g, "bot_id")

def antispam_whitelist_add(g, u): _wl_add("antispam_whitelist", g, u, "user_id")
def antispam_whitelist_remove(g, u): _wl_remove("antispam_whitelist", g, u, "user_id")
def antispam_whitelist_list(g): return _wl_list("antispam_whitelist", g, "user_id")

def antiraid_whitelist_add(g, u): _wl_add("antiraid_whitelist", g, u, "user_id")
def antiraid_whitelist_remove(g, u): _wl_remove("antiraid_whitelist", g, u, "user_id")
def antiraid_whitelist_list(g): return _wl_list("antiraid_whitelist", g, "user_id")


# --- Incidentes ("Sanciones") ---
def get_incidents(guild_id: int, limit: int = 30):
    return _fetchall(
        "SELECT module, executor_id, detail, action_taken, created_at FROM incidents "
        "WHERE guild_id=? ORDER BY created_at DESC LIMIT ?",
        (guild_id, limit),
    )


def clear_incidents(guild_id: int):
    _execute("DELETE FROM incidents WHERE guild_id=?", (guild_id,))


def log_incident(guild_id: int, module: str, executor_id, detail: str, action_taken: str):
    _execute(
        "INSERT INTO incidents (guild_id, module, executor_id, detail, action_taken, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (guild_id, module, executor_id, detail, action_taken, int(time.time())),
    )


def incidents_stats(guild_id: int):
    total = _fetchone("SELECT COUNT(*) FROM incidents WHERE guild_id=?", (guild_id,))[0]
    last24h = _fetchone("SELECT COUNT(*) FROM incidents WHERE guild_id=? AND created_at > ?", (guild_id, int(time.time()) - 86400))[0]
    return total, last24h


def incidents_by_module(guild_id: int):
    return _fetchall(
        "SELECT module, COUNT(*) as c FROM incidents WHERE guild_id=? GROUP BY module ORDER BY c DESC", (guild_id,)
    )


# --- Backups ---
def save_backup(guild_id: int, label: str, data: dict) -> int:
    cur = _execute(
        "INSERT INTO backups (guild_id, label, data, created_at) VALUES (?, ?, ?, ?)",
        (guild_id, label, json.dumps(data), int(time.time())),
    )
    return cur.lastrowid


def list_backups(guild_id: int):
    return _fetchall("SELECT id, label, created_at FROM backups WHERE guild_id=? ORDER BY created_at DESC", (guild_id,))


def get_backup(backup_id: int, guild_id: int):
    row = _fetchone("SELECT data FROM backups WHERE id=? AND guild_id=?", (backup_id, guild_id))
    return json.loads(row[0]) if row else None


def delete_backup(backup_id: int, guild_id: int) -> bool:
    cur = _execute("DELETE FROM backups WHERE id=? AND guild_id=?", (backup_id, guild_id))
    return cur.rowcount > 0


def backups_count(guild_id: int) -> int:
    return _fetchone("SELECT COUNT(*) FROM backups WHERE guild_id=?", (guild_id,))[0]


# --- Blacklists globales ---
def blacklist_guild_add(guild_id: int, reason: str):
    _execute("INSERT OR REPLACE INTO guild_blacklist (guild_id, reason, added_at) VALUES (?, ?, ?)", (guild_id, reason, int(time.time())))


def blacklist_guild_remove(guild_id: int):
    _execute("DELETE FROM guild_blacklist WHERE guild_id=?", (guild_id,))


def list_guild_blacklist():
    return _fetchall("SELECT guild_id, reason, added_at FROM guild_blacklist ORDER BY added_at DESC")


def blacklist_user_add(user_id: int, reason: str):
    _execute("INSERT OR REPLACE INTO user_blacklist (user_id, reason, added_at) VALUES (?, ?, ?)", (user_id, reason, int(time.time())))


def blacklist_user_remove(user_id: int):
    _execute("DELETE FROM user_blacklist WHERE user_id=?", (user_id,))


def list_user_blacklist():
    return _fetchall("SELECT user_id, reason, added_at FROM user_blacklist ORDER BY added_at DESC")


# --- Reportes ---
def create_report(guild_id: int, target: str, reason: str, reporter_id: int) -> int:
    cur = _execute(
        "INSERT INTO reports (guild_id, target, reason, reporter_id, status, created_at) VALUES (?, ?, ?, ?, 'open', ?)",
        (guild_id, target, reason, reporter_id, int(time.time())),
    )
    return cur.lastrowid


def list_reports(guild_id: int, status_filter: str = "open", search: str = ""):
    query = "SELECT id, target, reason, reporter_id, status, notes, created_at, resolved_at FROM reports WHERE guild_id=?"
    params: list = [guild_id]
    if status_filter and status_filter != "all":
        query += " AND status=?"
        params.append(status_filter)
    if search:
        query += " AND (target LIKE ? OR reason LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    query += " ORDER BY created_at DESC"
    return _fetchall(query, tuple(params))


def get_report(report_id: int, guild_id: int):
    return _fetchone(
        "SELECT id, target, reason, reporter_id, status, notes, created_at, resolved_at FROM reports WHERE id=? AND guild_id=?",
        (report_id, guild_id),
    )


def update_report(report_id: int, guild_id: int, status: str, notes: str = ""):
    resolved_at = int(time.time()) if status == "closed" else None
    _execute(
        "UPDATE reports SET status=?, notes=?, resolved_at=? WHERE id=? AND guild_id=?",
        (status, notes, resolved_at, report_id, guild_id),
    )


def delete_report(report_id: int, guild_id: int):
    _execute("DELETE FROM reports WHERE id=? AND guild_id=?", (report_id, guild_id))


def report_stats(guild_id: int):
    total = _fetchone("SELECT COUNT(*) FROM reports WHERE guild_id=?", (guild_id,))[0]
    open_count = _fetchone("SELECT COUNT(*) FROM reports WHERE guild_id=? AND status='open'", (guild_id,))[0]
    last24h = _fetchone("SELECT COUNT(*) FROM reports WHERE guild_id=? AND created_at > ?", (guild_id, int(time.time()) - 86400))[0]
    return total, open_count, last24h


# --- Tareas ---
def add_task(guild_id: int, text: str) -> int:
    cur = _execute("INSERT INTO tasks (guild_id, text, done, created_at) VALUES (?, ?, 0, ?)", (guild_id, text, int(time.time())))
    return cur.lastrowid


def list_tasks(guild_id: int):
    return _fetchall("SELECT id, text, done, created_at FROM tasks WHERE guild_id=? ORDER BY done ASC, created_at DESC", (guild_id,))


def toggle_task(task_id: int, guild_id: int):
    row = _fetchone("SELECT done FROM tasks WHERE id=? AND guild_id=?", (task_id, guild_id))
    if row:
        _execute("UPDATE tasks SET done=? WHERE id=? AND guild_id=?", (0 if row[0] else 1, task_id, guild_id))


def delete_task(task_id: int, guild_id: int):
    _execute("DELETE FROM tasks WHERE id=? AND guild_id=?", (task_id, guild_id))


def tasks_pending_count(guild_id: int) -> int:
    return _fetchone("SELECT COUNT(*) FROM tasks WHERE guild_id=? AND done=0", (guild_id,))[0]


# --- Personalizacion del panel (CSS/JS propios) ---
def get_customization() -> dict:
    row = _fetchone("SELECT custom_css, custom_js FROM panel_customization WHERE id=1")
    if not row:
        return {"custom_css": "", "custom_js": ""}
    return {"custom_css": row[0] or "", "custom_js": row[1] or ""}


def save_customization(custom_css: str, custom_js: str):
    _execute(
        "INSERT INTO panel_customization (id, custom_css, custom_js) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET custom_css=excluded.custom_css, custom_js=excluded.custom_js",
        (custom_css, custom_js),
    )


# --- Registro de actividad (logins + accesos por servidor) ---
def log_login(user_id: int, username: str, avatar_url: str):
    _execute(
        "INSERT INTO login_log (user_id, username, avatar_url, created_at) VALUES (?, ?, ?, ?)",
        (user_id, username, avatar_url, int(time.time())),
    )


def list_logins(limit: int = 50):
    return _fetchall(
        "SELECT user_id, username, avatar_url, created_at FROM login_log ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )


def touch_guild_access(guild_id: int, user_id: int, username: str):
    now = int(time.time())
    row = _fetchone("SELECT visits FROM guild_access_log WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    if row:
        _execute(
            "UPDATE guild_access_log SET username=?, last_seen=?, visits=visits+1 WHERE guild_id=? AND user_id=?",
            (username, now, guild_id, user_id),
        )
    else:
        _execute(
            "INSERT INTO guild_access_log (guild_id, user_id, username, first_seen, last_seen, visits) VALUES (?, ?, ?, ?, ?, 1)",
            (guild_id, user_id, username, now, now),
        )


def list_guild_access(guild_id: int):
    return _fetchall(
        "SELECT user_id, username, first_seen, last_seen, visits FROM guild_access_log WHERE guild_id=? ORDER BY last_seen DESC",
        (guild_id,),
    )


def list_all_guild_access():
    return _fetchall(
        "SELECT guild_id, user_id, username, first_seen, last_seen, visits FROM guild_access_log ORDER BY last_seen DESC"
    )
