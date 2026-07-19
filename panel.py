"""
AstroCube Panel - Panel web para gestionar AstroCube Anti-Raid.

Cualquier cuenta de Discord puede iniciar sesión, pero solo puede gestionar
un servidor concreto si es su propietario o tiene permiso de Administrador /
Gestionar Servidor ahí (comprobado en vivo contra la API de Discord). Los IDs
en OWNER_IDS (.env) son "superadmins": ven todos los servidores del bot y
acceden además a las páginas globales (Global, Código personalizado).

El panel habla directamente con la API de Discord (con el token del bot) y
con la base de datos SQLite que también usa el bot, así que los cambios se
aplican al instante.

Ejecuta con: python3 panel.py
"""

import functools
import time

from flask import Flask, redirect, render_template, request, session, url_for, flash

import panel_config as config
import db
import discord_api as api

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY

CHANNEL_TYPE_TEXT = 0
CHANNEL_TYPE_VOICE = 2
CHANNEL_TYPE_CATEGORY = 4

TABS = [
    ("resumen", "Resumen", "grid"),
    ("reportes", "Reportes", "flag"),
    ("staff", "Staff", "users"),
    ("bot", "CTO", "crown"),
    ("tareas", "Tareas", "clipboard-check"),
    ("sanciones", "Sanciones", "scale"),
    ("mensajes", "Mensajes", "message-square"),
    ("analiticas", "Analíticas", "bar-chart"),
    ("config", "Configuración", "settings"),
    ("backups", "Backups", "file-text"),
]


def login_required(view):
    """Cualquier cuenta de Discord puede iniciar sesión. El acceso concreto a
    cada servidor o a las páginas de propietario se comprueba aparte con
    @owner_required / @guild_access_required."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def _is_owner(user_id: int) -> bool:
    return user_id in config.OWNER_IDS


def owner_required(view):
    """Para páginas globales (todas las webs, blacklist, código personalizado)
    reservadas al propietario del bot, no a cualquier admin de un servidor."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not _is_owner(session.get("user_id")):
            return render_template("denied.html", user=session.get("username"), reason="owner")
        return view(*args, **kwargs)
    return wrapped


def _user_can_manage_guild(user_id: int, guild_id: int) -> bool:
    """True si el usuario es el propietario del bot, o si tiene permiso de
    Administrador/Gestionar Servidor (o es el dueño) en ESE servidor concreto."""
    if _is_owner(user_id):
        return True
    try:
        member = api.get_guild_member(config.BOT_TOKEN, guild_id, user_id)
        if member is None:
            return False
        guild = api.get_guild(config.BOT_TOKEN, guild_id)
        if str(guild.get("owner_id")) == str(user_id):
            return True
        roles = api.get_guild_roles(config.BOT_TOKEN, guild_id)
        admin_role_ids = {r["id"] for r in api.roles_with_admin(roles)}
        return any(rid in admin_role_ids for rid in member.get("roles", []))
    except api.DiscordAPIError:
        return False


def guild_access_required(view):
    """Para rutas /guild/<guild_id>/...: solo entra el propietario del bot o
    alguien con permisos de administrador en ESE servidor."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        guild_id = kwargs.get("guild_id")
        user_id = session.get("user_id")
        if not _user_can_manage_guild(user_id, guild_id):
            flash("No tienes permisos de administrador en ese servidor.", "error")
            return redirect(url_for("dashboard"))
        db.touch_guild_access(guild_id, user_id, session.get("username"))
        return view(*args, **kwargs)
    return wrapped


def _administrable_guilds_for(user_id: int, all_guilds: list[dict]) -> list[dict]:
    if _is_owner(user_id):
        return all_guilds
    return [g for g in all_guilds if _user_can_manage_guild(user_id, int(g["id"]))]


def _tab_label(key: str) -> str:
    for k, label, icon in TABS:
        if k == key:
            return label
    return key.capitalize()


@app.context_processor
def inject_globals():
    custom = db.get_customization()
    return {
        "bot_name": config.BOT_NAME,
        "session_user": session.get("username"),
        "session_avatar": session.get("avatar_url"),
        "tabs": TABS,
        "tab_label": _tab_label,
        "custom_css": custom.get("custom_css", ""),
        "custom_js": custom.get("custom_js", ""),
        "is_owner": _is_owner(session.get("user_id")) if "user_id" in session else False,
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))


@app.route("/login")
def login():
    if not config.DISCORD_CLIENT_ID or not config.DISCORD_CLIENT_SECRET:
        return render_template("login.html", misconfigured=True)
    return render_template("login.html", misconfigured=False)


@app.route("/discord-login")
def discord_login():
    url = api.oauth_authorize_url(config.DISCORD_CLIENT_ID, config.REDIRECT_URI)
    return redirect(url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        flash("No se recibió código de Discord.", "error")
        return redirect(url_for("login"))
    try:
        token_data = api.oauth_exchange_code(config.DISCORD_CLIENT_ID, config.DISCORD_CLIENT_SECRET, config.REDIRECT_URI, code)
        user = api.oauth_get_user(token_data["access_token"])
    except api.DiscordAPIError as exc:
        flash(str(exc), "error")
        return redirect(url_for("login"))

    session["user_id"] = int(user["id"])
    session["username"] = user.get("global_name") or user.get("username")
    session["handle"] = user.get("username")
    avatar_hash = user.get("avatar")
    if avatar_hash:
        session["avatar_url"] = f"https://cdn.discordapp.com/avatars/{user['id']}/{avatar_hash}.png"
    else:
        session["avatar_url"] = "https://cdn.discordapp.com/embed/avatars/0.png"

    db.log_login(session["user_id"], session["username"], session["avatar_url"])

    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    try:
        all_guilds = api.get_bot_guilds(config.BOT_TOKEN)
    except api.DiscordAPIError as exc:
        flash(str(exc), "error")
        all_guilds = []

    guilds = _administrable_guilds_for(session.get("user_id"), all_guilds)

    bot_user = None
    try:
        bot_user = api.get_bot_user(config.BOT_TOKEN)
    except api.DiscordAPIError:
        pass

    guild_stats = {}
    total_open_reports = 0
    total_incidents_24h = 0
    total_pending_tasks = 0
    for g in guilds:
        gid = int(g["id"])
        _, open_reports, _ = db.report_stats(gid)
        _, incidents_24h = db.incidents_stats(gid)
        pending_tasks = db.tasks_pending_count(gid)
        guild_stats[g["id"]] = {
            "open_reports": open_reports,
            "incidents_24h": incidents_24h,
            "pending_tasks": pending_tasks,
        }
        total_open_reports += open_reports
        total_incidents_24h += incidents_24h
        total_pending_tasks += pending_tasks

    return render_template(
        "dashboard.html",
        guilds=guilds,
        bot_user=bot_user,
        guild_stats=guild_stats,
        total_open_reports=total_open_reports,
        total_incidents_24h=total_incidents_24h,
        total_pending_tasks=total_pending_tasks,
        is_filtered_view=not _is_owner(session.get("user_id")),
    )


@app.route("/global", methods=["GET"])
@login_required
@owner_required
def global_page():
    guild_blacklist = db.list_guild_blacklist()
    user_blacklist = db.list_user_blacklist()
    return render_template("global.html", guild_blacklist=guild_blacklist, user_blacklist=user_blacklist)


@app.route("/activity", methods=["GET"])
@login_required
@owner_required
def activity_page():
    try:
        all_guilds = api.get_bot_guilds(config.BOT_TOKEN)
    except api.DiscordAPIError as exc:
        flash(str(exc), "error")
        all_guilds = []

    guild_names = {g["id"]: g["name"] for g in all_guilds}
    access_rows = db.list_all_guild_access()
    by_guild = {}
    for guild_id, user_id, username, first_seen, last_seen, visits in access_rows:
        if user_id in config.OWNER_IDS:
            continue  # no hace falta listarte a ti mismo como "admin externo"
        by_guild.setdefault(str(guild_id), []).append({
            "user_id": user_id, "username": username,
            "first_seen": first_seen, "last_seen": last_seen, "visits": visits,
        })

    logins = db.list_logins(50)

    return render_template(
        "activity.html",
        all_guilds=all_guilds,
        guild_names=guild_names,
        by_guild=by_guild,
        logins=logins,
        owner_ids_for_badge=config.OWNER_IDS,
    )


@app.route("/code", methods=["GET", "POST"])
@login_required
@owner_required
def code_page():
    if request.method == "POST":
        custom_css = request.form.get("custom_css", "")
        custom_js = request.form.get("custom_js", "")
        db.save_customization(custom_css, custom_js)
        flash("Código guardado. Se ha aplicado a todo el panel.", "success")
        return redirect(url_for("code_page"))
    custom = db.get_customization()
    return render_template("code.html", custom_css=custom["custom_css"], custom_js=custom["custom_js"])


@app.route("/global/guild-blacklist/add", methods=["POST"])
@login_required
@owner_required
def global_guild_blacklist_add():
    guild_id = request.form.get("guild_id", "").strip()
    reason = request.form.get("reason", "Sin especificar")
    if guild_id.isdigit():
        db.blacklist_guild_add(int(guild_id), reason)
        try:
            api.leave_guild(config.BOT_TOKEN, int(guild_id))
        except api.DiscordAPIError:
            pass
        flash("Servidor bloqueado.", "success")
    return redirect(url_for("global_page"))


@app.route("/global/guild-blacklist/remove/<int:guild_id>", methods=["POST"])
@login_required
@owner_required
def global_guild_blacklist_remove(guild_id):
    db.blacklist_guild_remove(guild_id)
    flash("Servidor desbloqueado.", "success")
    return redirect(url_for("global_page"))


@app.route("/global/user-blacklist/add", methods=["POST"])
@login_required
@owner_required
def global_user_blacklist_add():
    user_id = request.form.get("user_id", "").strip()
    reason = request.form.get("reason", "Sin especificar")
    if user_id.isdigit():
        db.blacklist_user_add(int(user_id), reason)
        flash("Usuario bloqueado.", "success")
    return redirect(url_for("global_page"))


@app.route("/global/user-blacklist/remove/<int:user_id>", methods=["POST"])
@login_required
@owner_required
def global_user_blacklist_remove(user_id):
    db.blacklist_user_remove(user_id)
    flash("Usuario desbloqueado.", "success")
    return redirect(url_for("global_page"))


# ---------------------------------------------------------------------------
# Contexto común de servidor
# ---------------------------------------------------------------------------
def _guild_context(guild_id: int, with_counts: bool = False) -> dict:
    guild = api.get_guild(config.BOT_TOKEN, guild_id, with_counts=with_counts)
    try:
        channels = api.get_guild_channels(config.BOT_TOKEN, guild_id)
    except api.DiscordAPIError:
        channels = []
    text_channels = [c for c in channels if c.get("type") == CHANNEL_TYPE_TEXT]
    roles = []
    try:
        roles = api.get_guild_roles(config.BOT_TOKEN, guild_id)
    except api.DiscordAPIError:
        pass
    return {"guild": guild, "text_channels": text_channels, "roles": [r for r in roles if r["name"] != "@everyone"]}


def _sidebar_counts(guild_id: int) -> dict:
    total_reports, open_reports, _ = db.report_stats(guild_id)
    return {
        "open_reports": open_reports,
        "pending_tasks": db.tasks_pending_count(guild_id),
    }


@app.route("/guild/<int:guild_id>")
@login_required
@guild_access_required
def guild_detail(guild_id):
    tab = request.args.get("tab", "resumen")
    try:
        ctx = _guild_context(guild_id, with_counts=(tab == "resumen"))
    except api.DiscordAPIError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    counts = _sidebar_counts(guild_id)

    if tab == "resumen":
        antinuke_on = db.get_bool(guild_id, "antinuke_enabled", True)
        antispam_on = db.get_bool(guild_id, "antispam_enabled", True)
        antiraid_on = db.get_bool(guild_id, "antiraid_enabled", True)
        total_incidents, incidents_24h = db.incidents_stats(guild_id)
        total_reports, open_reports, reports_24h = db.report_stats(guild_id)
        return render_template(
            "guild_resumen.html", tab=tab, guild_id=guild_id, counts=counts,
            antinuke_on=antinuke_on, antispam_on=antispam_on, antiraid_on=antiraid_on,
            total_incidents=total_incidents, incidents_24h=incidents_24h,
            total_reports=total_reports, open_reports=open_reports, reports_24h=reports_24h,
            backups_count=db.backups_count(guild_id), **ctx,
        )

    if tab == "reportes":
        status_filter = request.args.get("status", "open")
        search = request.args.get("q", "").strip()
        reports = db.list_reports(guild_id, status_filter, search)
        selected_id = request.args.get("report_id", type=int)
        selected = db.get_report(selected_id, guild_id) if selected_id else None
        total, open_count, last24h = db.report_stats(guild_id)
        return render_template(
            "guild_reports.html", tab=tab, guild_id=guild_id, counts=counts,
            reports=reports, status_filter=status_filter, search=search, selected=selected,
            total=total, open_count=open_count, last24h=last24h, **ctx,
        )

    if tab == "staff":
        admin_roles = api.roles_with_admin(ctx.get("roles") or [])
        whitelist = {
            "antinuke": db.antinuke_whitelist_list(guild_id),
            "antinuke_bots": db.antinuke_trustedbot_list(guild_id),
            "antispam": db.antispam_whitelist_list(guild_id),
            "antiraid": db.antiraid_whitelist_list(guild_id),
        }
        return render_template(
            "guild_staff.html", tab=tab, guild_id=guild_id, counts=counts,
            admin_roles=admin_roles, whitelist=whitelist, owner_ids=config.OWNER_IDS, **ctx,
        )

    if tab == "bot":
        try:
            bot_user = api.get_bot_user(config.BOT_TOKEN)
        except api.DiscordAPIError as exc:
            flash(str(exc), "error")
            bot_user = None
        return render_template("guild_bot.html", tab=tab, guild_id=guild_id, counts=counts, bot_user=bot_user, **ctx)

    if tab == "tareas":
        tasks = db.list_tasks(guild_id)
        return render_template("guild_tasks.html", tab=tab, guild_id=guild_id, counts=counts, tasks=tasks, **ctx)

    if tab == "sanciones":
        incidents = db.get_incidents(guild_id)
        total, last24h = db.incidents_stats(guild_id)
        return render_template(
            "guild_sanciones.html", tab=tab, guild_id=guild_id, counts=counts,
            incidents=incidents, total=total, last24h=last24h, **ctx,
        )

    if tab == "mensajes":
        return render_template("guild_mensajes.html", tab=tab, guild_id=guild_id, counts=counts, **ctx)

    if tab == "analiticas":
        by_module = db.incidents_by_module(guild_id)
        total_incidents, incidents_24h = db.incidents_stats(guild_id)
        total_reports, open_reports, reports_24h = db.report_stats(guild_id)
        max_count = max([c for _, c in by_module], default=1)
        return render_template(
            "guild_analiticas.html", tab=tab, guild_id=guild_id, counts=counts,
            by_module=by_module, max_count=max_count,
            total_incidents=total_incidents, incidents_24h=incidents_24h,
            total_reports=total_reports, open_reports=open_reports, reports_24h=reports_24h,
            backups_count=db.backups_count(guild_id), **ctx,
        )

    if tab == "config":
        cfg = {
            "antinuke_enabled": db.get_bool(guild_id, "antinuke_enabled", True),
            "antinuke_punishment": db.get_config(guild_id, "antinuke_punishment", "strip-roles"),
            "antinuke_log_channel": db.get_config(guild_id, "antinuke_log_channel"),
            "antispam_enabled": db.get_bool(guild_id, "antispam_enabled", True),
            "antispam_punishment": db.get_config(guild_id, "antispam_punishment", "timeout"),
            "antiraid_enabled": db.get_bool(guild_id, "antiraid_enabled", True),
            "antiraid_action": db.get_config(guild_id, "antiraid_action", "lockdown-verification"),
            "antiraid_log_channel": db.get_config(guild_id, "antiraid_log_channel"),
            "antiraid_min_account_age": db.get_config(guild_id, "antiraid_min_account_age", 3),
            "autorole": db.get_config(guild_id, "autorole"),
        }
        thresholds = {
            "channel_delete": db.get_int_pair(guild_id, "antinuke_threshold_channel_delete", (3, 10)),
            "channel_create": db.get_int_pair(guild_id, "antinuke_threshold_channel_create", (5, 10)),
            "role_delete": db.get_int_pair(guild_id, "antinuke_threshold_role_delete", (3, 10)),
            "role_create": db.get_int_pair(guild_id, "antinuke_threshold_role_create", (5, 10)),
            "ban": db.get_int_pair(guild_id, "antinuke_threshold_ban", (3, 10)),
            "webhook_create": db.get_int_pair(guild_id, "antinuke_threshold_webhook_create", (3, 10)),
        }
        message_threshold = db.get_int_pair(guild_id, "antispam_message_threshold", (6, 6))
        mention_threshold = db.get_config(guild_id, "antispam_mention_threshold", 5)
        join_threshold = db.get_int_pair(guild_id, "antiraid_join_threshold", (10, 15))
        return render_template(
            "guild_config.html", tab=tab, guild_id=guild_id, counts=counts, cfg=cfg, thresholds=thresholds,
            message_threshold=message_threshold, mention_threshold=mention_threshold,
            join_threshold=join_threshold, **ctx,
        )

    if tab == "backups":
        backups = db.list_backups(guild_id)
        return render_template("guild_backups.html", tab=tab, guild_id=guild_id, counts=counts, backups=backups, **ctx)

    return redirect(url_for("guild_detail", guild_id=guild_id, tab="resumen"))


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/config", methods=["POST"])
@login_required
@guild_access_required
def guild_config_save(guild_id):
    f = request.form

    db.set_config(guild_id, "antinuke_enabled", "1" if f.get("antinuke_enabled") else "0")
    db.set_config(guild_id, "antinuke_punishment", f.get("antinuke_punishment", "strip-roles"))
    if f.get("antinuke_log_channel"):
        db.set_config(guild_id, "antinuke_log_channel", f.get("antinuke_log_channel"))

    db.set_config(guild_id, "antispam_enabled", "1" if f.get("antispam_enabled") else "0")
    db.set_config(guild_id, "antispam_punishment", f.get("antispam_punishment", "timeout"))

    db.set_config(guild_id, "antiraid_enabled", "1" if f.get("antiraid_enabled") else "0")
    db.set_config(guild_id, "antiraid_action", f.get("antiraid_action", "lockdown-verification"))
    if f.get("antiraid_log_channel"):
        db.set_config(guild_id, "antiraid_log_channel", f.get("antiraid_log_channel"))
    if f.get("antiraid_min_account_age", "").isdigit():
        db.set_config(guild_id, "antiraid_min_account_age", f.get("antiraid_min_account_age"))

    if f.get("autorole"):
        db.set_config(guild_id, "autorole", f.get("autorole"))

    for key in ["channel_delete", "channel_create", "role_delete", "role_create", "ban", "webhook_create"]:
        count = f.get(f"threshold_{key}_count")
        seconds = f.get(f"threshold_{key}_seconds")
        if count and seconds and count.isdigit() and seconds.isdigit():
            db.set_int_pair(guild_id, f"antinuke_threshold_{key}", int(count), int(seconds))

    if f.get("message_threshold_count", "").isdigit() and f.get("message_threshold_seconds", "").isdigit():
        db.set_int_pair(guild_id, "antispam_message_threshold", int(f["message_threshold_count"]), int(f["message_threshold_seconds"]))
    if f.get("mention_threshold", "").isdigit():
        db.set_config(guild_id, "antispam_mention_threshold", int(f["mention_threshold"]))
    if f.get("join_threshold_count", "").isdigit() and f.get("join_threshold_seconds", "").isdigit():
        db.set_int_pair(guild_id, "antiraid_join_threshold", int(f["join_threshold_count"]), int(f["join_threshold_seconds"]))

    flash("Configuración guardada.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="config"))


# ---------------------------------------------------------------------------
# Mensajes (embeds)
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/embed", methods=["POST"])
@login_required
@guild_access_required
def guild_send_embed(guild_id):
    channel_id = request.form.get("channel_id")
    title = request.form.get("title", "")
    description = request.form.get("description", "")
    color = request.form.get("color", "5865F2")
    if not channel_id or not (title or description):
        flash("Falta el canal o el contenido del embed.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="mensajes"))
    try:
        api.send_embed(config.BOT_TOKEN, int(channel_id), title, description.replace("\\n", "\n"), color)
        flash("Mensaje enviado.", "success")
    except api.DiscordAPIError as exc:
        flash(str(exc), "error")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="mensajes"))


# ---------------------------------------------------------------------------
# Sanciones (incidentes)
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/incidents/clear", methods=["POST"])
@login_required
@guild_access_required
def guild_incidents_clear(guild_id):
    db.clear_incidents(guild_id)
    flash("Historial de sanciones borrado.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="sanciones"))


# ---------------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/report/create", methods=["POST"])
@login_required
@guild_access_required
def guild_report_create(guild_id):
    target = request.form.get("target", "").strip()
    reason = request.form.get("reason", "").strip()
    if not target or not reason:
        flash("Falta el usuario o el motivo del reporte.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="reportes"))
    report_id = db.create_report(guild_id, target, reason, session.get("user_id"))
    flash("Reporte creado.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="reportes", report_id=report_id))


@app.route("/guild/<int:guild_id>/report/<int:report_id>/update", methods=["POST"])
@login_required
@guild_access_required
def guild_report_update(guild_id, report_id):
    status = request.form.get("status", "open")
    notes = request.form.get("notes", "")
    db.update_report(report_id, guild_id, status, notes)
    flash("Reporte actualizado.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="reportes", report_id=report_id))


@app.route("/guild/<int:guild_id>/report/<int:report_id>/delete", methods=["POST"])
@login_required
@guild_access_required
def guild_report_delete(guild_id, report_id):
    db.delete_report(report_id, guild_id)
    flash("Reporte eliminado.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="reportes"))


# ---------------------------------------------------------------------------
# Tareas
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/task/add", methods=["POST"])
@login_required
@guild_access_required
def guild_task_add(guild_id):
    text = request.form.get("text", "").strip()
    if text:
        db.add_task(guild_id, text)
        flash("Tarea añadida.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="tareas"))


@app.route("/guild/<int:guild_id>/task/<int:task_id>/toggle", methods=["POST"])
@login_required
@guild_access_required
def guild_task_toggle(guild_id, task_id):
    db.toggle_task(task_id, guild_id)
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="tareas"))


@app.route("/guild/<int:guild_id>/task/<int:task_id>/delete", methods=["POST"])
@login_required
@guild_access_required
def guild_task_delete(guild_id, task_id):
    db.delete_task(task_id, guild_id)
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="tareas"))


# ---------------------------------------------------------------------------
# Staff (whitelist)
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/whitelist/<list_name>/add", methods=["POST"])
@login_required
@guild_access_required
def guild_whitelist_add(guild_id, list_name):
    entity_id = request.form.get("entity_id", "").strip()
    if not entity_id.isdigit():
        flash("ID inválido.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="staff"))
    entity_id = int(entity_id)
    mapping = {
        "antinuke": db.antinuke_whitelist_add,
        "antinuke_bots": db.antinuke_trustedbot_add,
        "antispam": db.antispam_whitelist_add,
        "antiraid": db.antiraid_whitelist_add,
    }
    if list_name in mapping:
        mapping[list_name](guild_id, entity_id)
        flash("Añadido a la whitelist.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="staff"))


@app.route("/guild/<int:guild_id>/whitelist/<list_name>/remove/<int:entity_id>", methods=["POST"])
@login_required
@guild_access_required
def guild_whitelist_remove(guild_id, list_name, entity_id):
    mapping = {
        "antinuke": db.antinuke_whitelist_remove,
        "antinuke_bots": db.antinuke_trustedbot_remove,
        "antispam": db.antispam_whitelist_remove,
        "antiraid": db.antiraid_whitelist_remove,
    }
    if list_name in mapping:
        mapping[list_name](guild_id, entity_id)
        flash("Quitado de la whitelist.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="staff"))


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/backup/create", methods=["POST"])
@login_required
@guild_access_required
def guild_backup_create(guild_id):
    label = request.form.get("label", "Backup desde el panel")
    try:
        channels = api.get_guild_channels(config.BOT_TOKEN, guild_id)
        roles = api.get_guild_roles(config.BOT_TOKEN, guild_id)
    except api.DiscordAPIError as exc:
        flash(str(exc), "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="backups"))

    categories = {c["id"]: c["name"] for c in channels if c.get("type") == CHANNEL_TYPE_CATEGORY}
    data = {
        "roles": [
            {"name": r["name"], "color": r.get("color", 0), "permissions": r.get("permissions", "0"),
             "hoist": r.get("hoist", False), "mentionable": r.get("mentionable", False)}
            for r in roles if r["name"] != "@everyone" and not r.get("managed")
        ],
        "categories": [{"name": name} for name in categories.values()],
        "channels": [
            {"name": c["name"], "type": "voice" if c.get("type") == CHANNEL_TYPE_VOICE else "text",
             "category": categories.get(c.get("parent_id")), "topic": c.get("topic")}
            for c in channels if c.get("type") in (CHANNEL_TYPE_TEXT, CHANNEL_TYPE_VOICE)
        ],
    }
    db.save_backup(guild_id, label, data)
    flash(f"Backup creado: {len(data['roles'])} roles, {len(data['channels'])} canales.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="backups"))


@app.route("/guild/<int:guild_id>/backup/<int:backup_id>/delete", methods=["POST"])
@login_required
@guild_access_required
def guild_backup_delete(guild_id, backup_id):
    db.delete_backup(backup_id, guild_id)
    flash("Backup eliminado.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="backups"))


@app.route("/guild/<int:guild_id>/backup/<int:backup_id>/restore", methods=["POST"])
@login_required
@guild_access_required
def guild_backup_restore(guild_id, backup_id):
    data = db.get_backup(backup_id, guild_id)
    if not data:
        flash("Backup no encontrado.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="backups"))

    try:
        existing_channels = api.get_guild_channels(config.BOT_TOKEN, guild_id)
        existing_roles = api.get_guild_roles(config.BOT_TOKEN, guild_id)
    except api.DiscordAPIError as exc:
        flash(str(exc), "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="backups"))

    existing_role_names = {r["name"] for r in existing_roles}
    created_roles = 0
    for role_data in data.get("roles", []):
        if role_data["name"] in existing_role_names:
            continue
        try:
            api.create_role(config.BOT_TOKEN, guild_id, role_data["name"], role_data.get("color", 0),
                             role_data.get("hoist", False), role_data.get("mentionable", False),
                             str(role_data.get("permissions", "0")))
            created_roles += 1
        except api.DiscordAPIError:
            pass

    category_map = {c["name"]: c["id"] for c in existing_channels if c.get("type") == CHANNEL_TYPE_CATEGORY}
    created_categories = 0
    for cat in data.get("categories", []):
        if cat["name"] in category_map:
            continue
        try:
            new_cat = api.create_channel(config.BOT_TOKEN, guild_id, cat["name"], CHANNEL_TYPE_CATEGORY)
            category_map[cat["name"]] = new_cat["id"]
            created_categories += 1
        except api.DiscordAPIError:
            pass

    existing_channel_names = {c["name"] for c in existing_channels if c.get("type") in (CHANNEL_TYPE_TEXT, CHANNEL_TYPE_VOICE)}
    created_channels = 0
    for ch in data.get("channels", []):
        if ch["name"] in existing_channel_names:
            continue
        parent_id = category_map.get(ch.get("category")) if ch.get("category") else None
        ch_type = CHANNEL_TYPE_VOICE if ch.get("type") == "voice" else CHANNEL_TYPE_TEXT
        try:
            api.create_channel(config.BOT_TOKEN, guild_id, ch["name"], ch_type, parent_id)
            created_channels += 1
        except api.DiscordAPIError:
            pass

    db.log_incident(guild_id, "backup_restore", session.get("user_id"), f"Backup #{backup_id} (panel)",
                     f"{created_roles} roles, {created_categories} categorías, {created_channels} canales recreados")
    flash(f"Restaurado: {created_roles} roles, {created_categories} categorías, {created_channels} canales.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="backups"))


if __name__ == "__main__":
    # Este bloque solo corre cuando ejecutas "python3 panel.py" en tu Mac.
    # En Railway, el Procfile arranca con gunicorn y no pasa por aqui.
    if not config.BOT_TOKEN:
        raise SystemExit("❌ Falta DISCORD_TOKEN en .env")
    if not config.OWNER_IDS:
        print("⚠️  Aviso: OWNER_IDS está vacío. Cualquiera podrá iniciar sesión, pero nadie tendrá el rol de propietario (acceso a Global/Código/Actividad y a todos los servidores a la vez).")
    app.run(host="127.0.0.1", port=config.PANEL_PORT, debug=False)
