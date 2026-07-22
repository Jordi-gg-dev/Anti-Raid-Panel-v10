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
    CREATE TABLE IF NOT EXISTS premium_guilds (
        guild_id INTEGER PRIMARY KEY,
        stripe_customer_id TEXT,
        stripe_subscription_id TEXT,
        status TEXT NOT NULL DEFAULT 'inactive',
        current_period_end INTEGER,
        updated_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS custom_commands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        trigger_text TEXT NOT NULL,
        response TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS bot_profile (
        guild_id INTEGER PRIMARY KEY,
        nickname TEXT,
        updated_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_levels (
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        username TEXT,
        xp INTEGER NOT NULL DEFAULT 0,
        level INTEGER NOT NULL DEFAULT 0,
        last_message_at INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS giveaways (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        message_id INTEGER,
        prize TEXT NOT NULL,
        winners_count INTEGER NOT NULL DEFAULT 1,
        host_id INTEGER,
        ends_at INTEGER NOT NULL,
        ended INTEGER NOT NULL DEFAULT 0,
        winners TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS giveaway_entries (
        giveaway_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (giveaway_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER,
        user_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        created_at INTEGER NOT NULL,
        closed_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS reaction_role_panels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        message_id INTEGER,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS reaction_role_options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        panel_id INTEGER NOT NULL,
        role_id INTEGER NOT NULL,
        label TEXT NOT NULL,
        emoji TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS level_role_rewards (
        guild_id INTEGER NOT NULL,
        level INTEGER NOT NULL,
        role_id INTEGER NOT NULL,
        PRIMARY KEY (guild_id, level)
    );
    CREATE TABLE IF NOT EXISTS command_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER,
        guild_name TEXT,
        user_id INTEGER NOT NULL,
        username TEXT,
        command_name TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS staff_members (
        user_id INTEGER PRIMARY KEY,
        rank TEXT NOT NULL,
        username TEXT,
        avatar_url TEXT,
        added_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS panel_moderators (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        avatar_url TEXT,
        added_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS dm_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        username TEXT,
        avatar_url TEXT,
        direction TEXT NOT NULL,
        content TEXT NOT NULL,
        responded_by INTEGER,
        responded_by_username TEXT,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS dm_read_state (
        user_id INTEGER PRIMARY KEY,
        last_read_at INTEGER NOT NULL
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


# ---------------------------------------------------------------------------
# Premium (Stripe) por servidor
# ---------------------------------------------------------------------------
def get_premium(guild_id: int):
    return _fetchone(
        "SELECT guild_id, stripe_customer_id, stripe_subscription_id, status, current_period_end, updated_at "
        "FROM premium_guilds WHERE guild_id=?",
        (guild_id,),
    )


def is_premium(guild_id: int) -> bool:
    row = get_premium(guild_id)
    return bool(row and row[3] == "active")


def upsert_premium(guild_id: int, status: str, stripe_customer_id: str = None,
                    stripe_subscription_id: str = None, current_period_end: int = None):
    now = int(time.time())
    existing = get_premium(guild_id)
    if existing:
        _execute(
            "UPDATE premium_guilds SET "
            "status=?, "
            "stripe_customer_id=COALESCE(?, stripe_customer_id), "
            "stripe_subscription_id=COALESCE(?, stripe_subscription_id), "
            "current_period_end=COALESCE(?, current_period_end), "
            "updated_at=? WHERE guild_id=?",
            (status, stripe_customer_id, stripe_subscription_id, current_period_end, now, guild_id),
        )
    else:
        _execute(
            "INSERT INTO premium_guilds (guild_id, stripe_customer_id, stripe_subscription_id, status, current_period_end, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, stripe_customer_id, stripe_subscription_id, status, current_period_end, now),
        )


def get_premium_by_customer(stripe_customer_id: str):
    return _fetchone(
        "SELECT guild_id, stripe_customer_id, stripe_subscription_id, status, current_period_end, updated_at "
        "FROM premium_guilds WHERE stripe_customer_id=?",
        (stripe_customer_id,),
    )


def get_premium_by_subscription(stripe_subscription_id: str):
    return _fetchone(
        "SELECT guild_id, stripe_customer_id, stripe_subscription_id, status, current_period_end, updated_at "
        "FROM premium_guilds WHERE stripe_subscription_id=?",
        (stripe_subscription_id,),
    )


def list_premium_guilds():
    return _fetchall(
        "SELECT guild_id, stripe_customer_id, stripe_subscription_id, status, current_period_end, updated_at "
        "FROM premium_guilds WHERE status='active'"
    )


# ---------------------------------------------------------------------------
# Registro global de comandos usados (solo panel, propietario)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Staff (equipo mostrado en el botón "Staff" del panel)
# ---------------------------------------------------------------------------
def add_staff_member(user_id: int, rank: str, username: str | None = None, avatar_url: str | None = None) -> None:
    _execute(
        "INSERT INTO staff_members (user_id, rank, username, avatar_url, added_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET rank=excluded.rank, "
        "username=COALESCE(excluded.username, staff_members.username), "
        "avatar_url=COALESCE(excluded.avatar_url, staff_members.avatar_url)",
        (user_id, rank, username, avatar_url, int(time.time())),
    )


def remove_staff_member(user_id: int) -> None:
    _execute("DELETE FROM staff_members WHERE user_id = ?", (user_id,))


def list_staff_members():
    return _fetchall(
        "SELECT user_id, rank, username, avatar_url, added_at FROM staff_members ORDER BY added_at ASC"
    )


# ---------------------------------------------------------------------------
# Moderadores del panel (acceso concedido por el dueño a funciones concretas,
# empezando por la bandeja de mensajes privados).
# ---------------------------------------------------------------------------
def add_panel_moderator(user_id: int, username: str | None = None, avatar_url: str | None = None) -> None:
    _execute(
        "INSERT INTO panel_moderators (user_id, username, avatar_url, added_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "username=COALESCE(excluded.username, panel_moderators.username), "
        "avatar_url=COALESCE(excluded.avatar_url, panel_moderators.avatar_url)",
        (user_id, username, avatar_url, int(time.time())),
    )


def remove_panel_moderator(user_id: int) -> None:
    _execute("DELETE FROM panel_moderators WHERE user_id = ?", (user_id,))


def is_panel_moderator(user_id: int) -> bool:
    row = _fetchone("SELECT 1 FROM panel_moderators WHERE user_id = ?", (user_id,))
    return row is not None


def list_panel_moderators():
    return _fetchall(
        "SELECT user_id, username, avatar_url, added_at FROM panel_moderators ORDER BY added_at ASC"
    )


# ---------------------------------------------------------------------------
# Mensajes privados (modmail)
# ---------------------------------------------------------------------------
def log_dm_message(
    user_id: int, username: str | None, avatar_url: str | None, direction: str, content: str,
    responded_by: int | None = None, responded_by_username: str | None = None,
) -> None:
    _execute(
        "INSERT INTO dm_messages (user_id, username, avatar_url, direction, content, responded_by, "
        "responded_by_username, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, username, avatar_url, direction, content, responded_by, responded_by_username, int(time.time())),
    )


def list_dm_threads():
    """Un hilo por usuario (el último mensaje, entrante o saliente), con
    contador de mensajes entrantes no leídos, ordenado por actividad reciente."""
    rows = _fetchall(
        "SELECT dm.user_id, dm.username, dm.avatar_url, dm.content, dm.direction, dm.created_at "
        "FROM dm_messages dm "
        "INNER JOIN (SELECT user_id, MAX(id) AS max_id FROM dm_messages GROUP BY user_id) latest "
        "ON dm.user_id = latest.user_id AND dm.id = latest.max_id "
        "ORDER BY dm.created_at DESC"
    )
    threads = []
    for user_id, username, avatar_url, content, direction, created_at in rows:
        threads.append({
            "user_id": user_id, "username": username, "avatar_url": avatar_url,
            "last_content": content, "last_direction": direction, "last_created_at": created_at,
            "unread": _count_unread_in_thread(user_id),
        })
    return threads


def _count_unread_in_thread(user_id: int) -> int:
    row = _fetchone(
        "SELECT COUNT(*) FROM dm_messages "
        "WHERE user_id = ? AND direction = 'in' AND created_at > COALESCE("
        "  (SELECT last_read_at FROM dm_read_state WHERE user_id = ?), 0)",
        (user_id, user_id),
    )
    return row[0] if row else 0


def count_unread_dm_threads() -> int:
    row = _fetchone(
        "SELECT COUNT(*) FROM ("
        "  SELECT dm.user_id FROM dm_messages dm "
        "  LEFT JOIN dm_read_state rs ON rs.user_id = dm.user_id "
        "  WHERE dm.direction = 'in' AND dm.created_at > COALESCE(rs.last_read_at, 0) "
        "  GROUP BY dm.user_id"
        ")"
    )
    return row[0] if row else 0


def list_dm_messages(user_id: int, limit: int = 300):
    return _fetchall(
        "SELECT id, user_id, username, avatar_url, direction, content, responded_by, "
        "responded_by_username, created_at FROM dm_messages WHERE user_id = ? "
        "ORDER BY created_at ASC LIMIT ?",
        (user_id, limit),
    )


def mark_dm_thread_read(user_id: int) -> None:
    _execute(
        "INSERT INTO dm_read_state (user_id, last_read_at) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET last_read_at=excluded.last_read_at",
        (user_id, int(time.time())),
    )


def list_command_log(limit: int = 200, guild_id=None, user_id=None, command_name=None):
    query = (
        "SELECT id, guild_id, guild_name, user_id, username, command_name, created_at "
        "FROM command_log WHERE 1=1"
    )
    params: list = []
    if guild_id:
        query += " AND guild_id = ?"
        params.append(int(guild_id))
    if user_id:
        query += " AND user_id = ?"
        params.append(int(user_id))
    if command_name:
        query += " AND command_name LIKE ?"
        params.append(f"%{command_name}%")
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    return _fetchall(query, tuple(params))


# ---------------------------------------------------------------------------
# Comandos personalizados (funcion Premium)
# ---------------------------------------------------------------------------
def add_custom_command(guild_id: int, trigger_text: str, response: str) -> int:
    cur = _execute(
        "INSERT INTO custom_commands (guild_id, trigger_text, response, enabled, created_at) VALUES (?, ?, ?, 1, ?)",
        (guild_id, trigger_text.strip().lower(), response, int(time.time())),
    )
    return cur.lastrowid


def list_custom_commands(guild_id: int):
    return _fetchall(
        "SELECT id, trigger_text, response, enabled, created_at FROM custom_commands "
        "WHERE guild_id=? ORDER BY created_at DESC",
        (guild_id,),
    )


def count_custom_commands(guild_id: int) -> int:
    row = _fetchone("SELECT COUNT(*) FROM custom_commands WHERE guild_id=?", (guild_id,))
    return row[0] if row else 0


def get_custom_command(command_id: int, guild_id: int):
    return _fetchone(
        "SELECT id, trigger_text, response, enabled, created_at FROM custom_commands WHERE id=? AND guild_id=?",
        (command_id, guild_id),
    )


def toggle_custom_command(command_id: int, guild_id: int):
    row = get_custom_command(command_id, guild_id)
    if row is None:
        return
    new_value = 0 if row[3] else 1
    _execute("UPDATE custom_commands SET enabled=? WHERE id=? AND guild_id=?", (new_value, command_id, guild_id))


def delete_custom_command(command_id: int, guild_id: int):
    _execute("DELETE FROM custom_commands WHERE id=? AND guild_id=?", (command_id, guild_id))


# ---------------------------------------------------------------------------
# Perfil personalizado del bot por servidor (funcion Premium)
# ---------------------------------------------------------------------------
def get_bot_profile(guild_id: int):
    return _fetchone("SELECT guild_id, nickname, updated_at FROM bot_profile WHERE guild_id=?", (guild_id,))


def set_bot_profile_nickname(guild_id: int, nickname: str):
    now = int(time.time())
    existing = get_bot_profile(guild_id)
    if existing:
        _execute("UPDATE bot_profile SET nickname=?, updated_at=? WHERE guild_id=?", (nickname, now, guild_id))
    else:
        _execute(
            "INSERT INTO bot_profile (guild_id, nickname, updated_at) VALUES (?, ?, ?)",
            (guild_id, nickname, now),
        )


# ---------------------------------------------------------------------------
# Niveles (XP) por servidor
# ---------------------------------------------------------------------------
def get_user_level(guild_id: int, user_id: int):
    return _fetchone(
        "SELECT guild_id, user_id, username, xp, level, last_message_at FROM user_levels WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )


def leaderboard(guild_id: int, limit: int = 10):
    return _fetchall(
        "SELECT user_id, username, xp, level FROM user_levels WHERE guild_id=? ORDER BY xp DESC LIMIT ?",
        (guild_id, limit),
    )


def leaderboard_rank(guild_id: int, user_id: int) -> int:
    row = _fetchone(
        "SELECT COUNT(*) + 1 FROM user_levels WHERE guild_id=? AND xp > "
        "(SELECT xp FROM user_levels WHERE guild_id=? AND user_id=?)",
        (guild_id, guild_id, user_id),
    )
    return row[0] if row else 1


# ---------------------------------------------------------------------------
# Roles de recompensa por nivel (funcion Premium)
# ---------------------------------------------------------------------------
def set_level_role_reward(guild_id: int, level: int, role_id: int) -> None:
    _execute(
        "INSERT INTO level_role_rewards (guild_id, level, role_id) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id, level) DO UPDATE SET role_id=excluded.role_id",
        (guild_id, level, role_id),
    )


def remove_level_role_reward(guild_id: int, level: int) -> None:
    _execute("DELETE FROM level_role_rewards WHERE guild_id=? AND level=?", (guild_id, level))


def list_level_role_rewards(guild_id: int):
    return _fetchall(
        "SELECT level, role_id FROM level_role_rewards WHERE guild_id=? ORDER BY level ASC", (guild_id,)
    )


# ---------------------------------------------------------------------------
# Sorteos (giveaways)
# ---------------------------------------------------------------------------
def create_giveaway(guild_id: int, channel_id: int, prize: str, winners_count: int, host_id: int, ends_at: int) -> int:
    cur = _execute(
        "INSERT INTO giveaways (guild_id, channel_id, prize, winners_count, host_id, ends_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (guild_id, channel_id, prize, winners_count, host_id, ends_at, int(time.time())),
    )
    return cur.lastrowid


def set_giveaway_message_id(giveaway_id: int, message_id: int):
    _execute("UPDATE giveaways SET message_id=? WHERE id=?", (message_id, giveaway_id))


def get_giveaway(giveaway_id: int):
    return _fetchone(
        "SELECT id, guild_id, channel_id, message_id, prize, winners_count, host_id, ends_at, ended, winners, created_at "
        "FROM giveaways WHERE id=?",
        (giveaway_id,),
    )


def list_giveaways(guild_id: int):
    return _fetchall(
        "SELECT id, guild_id, channel_id, message_id, prize, winners_count, host_id, ends_at, ended, winners, created_at "
        "FROM giveaways WHERE guild_id=? ORDER BY created_at DESC",
        (guild_id,),
    )


def list_due_giveaways():
    now = int(time.time())
    return _fetchall(
        "SELECT id, guild_id, channel_id, message_id, prize, winners_count, host_id, ends_at, ended, winners, created_at "
        "FROM giveaways WHERE ended=0 AND ends_at<=?",
        (now,),
    )


def finish_giveaway(giveaway_id: int, winners_ids: list):
    _execute(
        "UPDATE giveaways SET ended=1, winners=? WHERE id=?",
        (",".join(str(w) for w in winners_ids), giveaway_id),
    )


def add_giveaway_entry(giveaway_id: int, user_id: int):
    _execute(
        "INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id, created_at) VALUES (?, ?, ?)",
        (giveaway_id, user_id, int(time.time())),
    )


def has_giveaway_entry(giveaway_id: int, user_id: int) -> bool:
    row = _fetchone("SELECT 1 FROM giveaway_entries WHERE giveaway_id=? AND user_id=?", (giveaway_id, user_id))
    return row is not None


def list_giveaway_entries(giveaway_id: int):
    return [r[0] for r in _fetchall("SELECT user_id FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))]


def count_giveaway_entries(giveaway_id: int) -> int:
    row = _fetchone("SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,))
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------
def create_ticket(guild_id: int, user_id: int) -> int:
    cur = _execute(
        "INSERT INTO tickets (guild_id, user_id, status, created_at) VALUES (?, ?, 'open', ?)",
        (guild_id, user_id, int(time.time())),
    )
    return cur.lastrowid


def set_ticket_channel(ticket_id: int, channel_id: int):
    _execute("UPDATE tickets SET channel_id=? WHERE id=?", (channel_id, ticket_id))


def get_ticket(ticket_id: int):
    return _fetchone(
        "SELECT id, guild_id, channel_id, user_id, status, created_at, closed_at FROM tickets WHERE id=?",
        (ticket_id,),
    )


def get_open_ticket_for_user(guild_id: int, user_id: int):
    return _fetchone(
        "SELECT id, guild_id, channel_id, user_id, status, created_at, closed_at FROM tickets "
        "WHERE guild_id=? AND user_id=? AND status='open'",
        (guild_id, user_id),
    )


def close_ticket(ticket_id: int):
    _execute("UPDATE tickets SET status='closed', closed_at=? WHERE id=?", (int(time.time()), ticket_id))


def list_tickets(guild_id: int, status: str = None):
    if status:
        return _fetchall(
            "SELECT id, guild_id, channel_id, user_id, status, created_at, closed_at FROM tickets "
            "WHERE guild_id=? AND status=? ORDER BY created_at DESC",
            (guild_id, status),
        )
    return _fetchall(
        "SELECT id, guild_id, channel_id, user_id, status, created_at, closed_at FROM tickets "
        "WHERE guild_id=? ORDER BY created_at DESC",
        (guild_id,),
    )


def tickets_open_count(guild_id: int) -> int:
    row = _fetchone("SELECT COUNT(*) FROM tickets WHERE guild_id=? AND status='open'", (guild_id,))
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# Roles de reaccion / boton
# ---------------------------------------------------------------------------
def create_reaction_panel(guild_id: int, channel_id: int, title: str, description: str) -> int:
    cur = _execute(
        "INSERT INTO reaction_role_panels (guild_id, channel_id, title, description, created_at) VALUES (?, ?, ?, ?, ?)",
        (guild_id, channel_id, title, description, int(time.time())),
    )
    return cur.lastrowid


def set_reaction_panel_message(panel_id: int, message_id: int):
    _execute("UPDATE reaction_role_panels SET message_id=? WHERE id=?", (message_id, panel_id))


def get_reaction_panel(panel_id: int):
    return _fetchone(
        "SELECT id, guild_id, channel_id, message_id, title, description, created_at FROM reaction_role_panels WHERE id=?",
        (panel_id,),
    )


def list_reaction_panels(guild_id: int):
    return _fetchall(
        "SELECT id, guild_id, channel_id, message_id, title, description, created_at FROM reaction_role_panels "
        "WHERE guild_id=? ORDER BY created_at DESC",
        (guild_id,),
    )


def delete_reaction_panel(panel_id: int, guild_id: int):
    _execute("DELETE FROM reaction_role_options WHERE panel_id=?", (panel_id,))
    _execute("DELETE FROM reaction_role_panels WHERE id=? AND guild_id=?", (panel_id, guild_id))


def add_reaction_option(panel_id: int, role_id: int, label: str, emoji: str = "") -> int:
    cur = _execute(
        "INSERT INTO reaction_role_options (panel_id, role_id, label, emoji) VALUES (?, ?, ?, ?)",
        (panel_id, role_id, label, emoji),
    )
    return cur.lastrowid


def list_reaction_options(panel_id: int):
    return _fetchall(
        "SELECT id, panel_id, role_id, label, emoji FROM reaction_role_options WHERE panel_id=?",
        (panel_id,),
    )


def delete_reaction_option(option_id: int):
    _execute("DELETE FROM reaction_role_options WHERE id=?", (option_id,))
