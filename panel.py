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
import stripe_client

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY

CHANNEL_TYPE_TEXT = 0
FREE_REACTION_PANEL_LIMIT = 1
FREE_INCIDENT_RETENTION_DAYS = 7
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
    ("premium", "Premium", "diamond"),
    ("comandos", "Comandos", "code"),
    ("perfil", "Perfil de Bot", "user-circle"),
    ("niveles", "Niveles", "bar-chart"),
    ("sorteos", "Sorteos", "activity"),
    ("tickets", "Tickets", "message-square"),
    ("roles-reaccion", "Roles Reacción", "users"),
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
        "support_server_invite": config.SUPPORT_SERVER_INVITE,
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
    total_premium_guilds = 0
    for g in guilds:
        gid = int(g["id"])
        _, open_reports, _ = db.report_stats(gid)
        _, incidents_24h = db.incidents_stats(gid)
        pending_tasks = db.tasks_pending_count(gid)
        is_prem = db.is_premium(gid)
        guild_stats[g["id"]] = {
            "open_reports": open_reports,
            "incidents_24h": incidents_24h,
            "pending_tasks": pending_tasks,
            "is_premium": is_prem,
            "open_tickets": db.tickets_open_count(gid),
        }
        total_open_reports += open_reports
        total_incidents_24h += incidents_24h
        total_pending_tasks += pending_tasks
        if is_prem:
            total_premium_guilds += 1

    return render_template(
        "dashboard.html",
        guilds=guilds,
        bot_user=bot_user,
        guild_stats=guild_stats,
        total_open_reports=total_open_reports,
        total_incidents_24h=total_incidents_24h,
        total_pending_tasks=total_pending_tasks,
        total_premium_guilds=total_premium_guilds,
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
            incidents=incidents, total=total, last24h=last24h,
            is_premium=db.is_premium(guild_id), FREE_INCIDENT_RETENTION_DAYS=FREE_INCIDENT_RETENTION_DAYS, **ctx,
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

    if tab == "premium":
        premium_row = db.get_premium(guild_id)
        checkout_result = request.args.get("checkout")
        return render_template(
            "guild_premium.html", tab=tab, guild_id=guild_id, counts=counts,
            premium=premium_row, is_premium=db.is_premium(guild_id),
            stripe_configured=bool(config.STRIPE_SECRET_KEY and config.STRIPE_PRICE_ID),
            checkout_result=checkout_result, **ctx,
        )

    if tab == "comandos":
        commands_list = db.list_custom_commands(guild_id)
        return render_template(
            "guild_comandos.html", tab=tab, guild_id=guild_id, counts=counts,
            commands_list=commands_list, is_premium=db.is_premium(guild_id), **ctx,
        )

    if tab == "perfil":
        profile = db.get_bot_profile(guild_id)
        return render_template(
            "guild_perfil.html", tab=tab, guild_id=guild_id, counts=counts,
            profile=profile, is_premium=db.is_premium(guild_id), **ctx,
        )

    if tab == "niveles":
        niveles_cfg = {
            "xp_enabled": db.get_bool(guild_id, "xp_enabled", True),
            "xp_min": db.get_config(guild_id, "xp_min", 15),
            "xp_max": db.get_config(guild_id, "xp_max", 25),
            "xp_cooldown": db.get_config(guild_id, "xp_cooldown", 60),
            "xp_levelup_channel": db.get_config(guild_id, "xp_levelup_channel"),
            "xp_multiplier": db.get_config(guild_id, "xp_multiplier", "1.0"),
            "rank_color": db.get_config(guild_id, "rank_color", ""),
        }
        top = db.leaderboard(guild_id, 10)
        role_rewards = db.list_level_role_rewards(guild_id)
        return render_template(
            "guild_niveles.html", tab=tab, guild_id=guild_id, counts=counts,
            niveles_cfg=niveles_cfg, top=top, role_rewards=role_rewards,
            is_premium=db.is_premium(guild_id), **ctx,
        )

    if tab == "sorteos":
        giveaways = db.list_giveaways(guild_id)
        giveaway_entry_counts = {g[0]: db.count_giveaway_entries(g[0]) for g in giveaways}
        return render_template(
            "guild_sorteos.html", tab=tab, guild_id=guild_id, counts=counts,
            giveaways=giveaways, giveaway_entry_counts=giveaway_entry_counts, **ctx,
        )

    if tab == "tickets":
        try:
            all_channels = api.get_guild_channels(config.BOT_TOKEN, guild_id)
        except api.DiscordAPIError:
            all_channels = []
        categories = [c for c in all_channels if c.get("type") == CHANNEL_TYPE_CATEGORY]
        tickets_cfg = {
            "category_id": db.get_config(guild_id, "tickets_category_id"),
            "staff_role_id": db.get_config(guild_id, "tickets_staff_role_id"),
            "transcript_channel_id": db.get_config(guild_id, "tickets_transcript_channel"),
        }
        tickets_list = db.list_tickets(guild_id)
        return render_template(
            "guild_tickets.html", tab=tab, guild_id=guild_id, counts=counts,
            categories=categories, tickets_cfg=tickets_cfg, tickets_list=tickets_list,
            is_premium=db.is_premium(guild_id), **ctx,
        )

    if tab == "roles-reaccion":
        panels = db.list_reaction_panels(guild_id)
        panels_with_options = [(p, db.list_reaction_options(p[0])) for p in panels]
        return render_template(
            "guild_roles_reaccion.html", tab=tab, guild_id=guild_id, counts=counts,
            panels_with_options=panels_with_options, is_premium=db.is_premium(guild_id),
            FREE_REACTION_PANEL_LIMIT=FREE_REACTION_PANEL_LIMIT, **ctx,
        )

    return redirect(url_for("guild_detail", guild_id=guild_id, tab="resumen"))


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/tickets/config", methods=["POST"])
@login_required
@guild_access_required
def tickets_config_save(guild_id):
    db.set_config(guild_id, "tickets_category_id", request.form.get("category_id", "").strip())
    db.set_config(guild_id, "tickets_staff_role_id", request.form.get("staff_role_id", "").strip())
    if db.is_premium(guild_id):
        db.set_config(guild_id, "tickets_transcript_channel", request.form.get("transcript_channel_id", "").strip())
    flash("Configuración de tickets guardada.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="tickets"))


@app.route("/guild/<int:guild_id>/tickets/publicar", methods=["POST"])
@login_required
@guild_access_required
def tickets_publicar(guild_id):
    channel_id = request.form.get("channel_id", "").strip()
    if not channel_id:
        flash("Elige un canal donde publicar el panel de tickets.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="tickets"))
    try:
        api.send_message_with_buttons(
            config.BOT_TOKEN, int(channel_id),
            "🎫 Soporte",
            "¿Necesitas ayuda? Pulsa el botón de abajo para abrir un ticket privado con el staff.",
            [{"label": "Abrir ticket", "custom_id": "astrocube:ticket:abrir", "emoji": "🎫"}],
        )
    except api.DiscordAPIError as exc:
        flash(f"No se pudo publicar el panel: {exc}", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="tickets"))
    flash("Panel de tickets publicado.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="tickets"))


# ---------------------------------------------------------------------------
# Roles de reaccion / boton
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/roles-reaccion/crear", methods=["POST"])
@login_required
@guild_access_required
def roles_reaccion_crear(guild_id):
    channel_id = request.form.get("channel_id", "").strip()
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    if not channel_id or not title:
        flash("Rellena al menos el canal y el título del panel.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="roles-reaccion"))
    if not db.is_premium(guild_id) and len(db.list_reaction_panels(guild_id)) >= FREE_REACTION_PANEL_LIMIT:
        flash(
            f"Los servidores gratuitos solo pueden tener {FREE_REACTION_PANEL_LIMIT} panel de roles de reacción. "
            "Hazte Premium para crear paneles ilimitados.",
            "error",
        )
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="roles-reaccion"))
    db.create_reaction_panel(guild_id, int(channel_id), title, description)
    flash("Panel de roles creado. Ahora añade opciones y publícalo.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="roles-reaccion"))


@app.route("/guild/<int:guild_id>/roles-reaccion/<int:panel_id>/opcion", methods=["POST"])
@login_required
@guild_access_required
def roles_reaccion_opcion(guild_id, panel_id):
    role_id = request.form.get("role_id", "").strip()
    label = request.form.get("label", "").strip()
    emoji = request.form.get("emoji", "").strip()
    if not role_id or not label:
        flash("Rellena el rol y la etiqueta de la opción.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="roles-reaccion"))
    db.add_reaction_option(panel_id, int(role_id), label, emoji)
    flash("Opción añadida.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="roles-reaccion"))


@app.route("/guild/<int:guild_id>/roles-reaccion/<int:panel_id>/opcion/<int:option_id>/eliminar", methods=["POST"])
@login_required
@guild_access_required
def roles_reaccion_opcion_eliminar(guild_id, panel_id, option_id):
    db.delete_reaction_option(option_id)
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="roles-reaccion"))


@app.route("/guild/<int:guild_id>/roles-reaccion/<int:panel_id>/publicar", methods=["POST"])
@login_required
@guild_access_required
def roles_reaccion_publicar(guild_id, panel_id):
    panel_row = db.get_reaction_panel(panel_id)
    if panel_row is None or panel_row[1] != guild_id:
        flash("Ese panel no existe.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="roles-reaccion"))

    options = db.list_reaction_options(panel_id)
    if not options:
        flash("Añade al menos una opción antes de publicar.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="roles-reaccion"))

    buttons = [
        {"label": label, "custom_id": f"astrocube:rrole:{panel_id}:{opt_id}:{role_id}", "emoji": emoji}
        for opt_id, _panel_id, role_id, label, emoji in options
    ]

    try:
        result = api.send_message_with_buttons(
            config.BOT_TOKEN, panel_row[2], panel_row[4], panel_row[5] or "Elige tus roles pulsando un botón.", buttons
        )
    except api.DiscordAPIError as exc:
        flash(f"No se pudo publicar el panel: {exc}", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="roles-reaccion"))

    db.set_reaction_panel_message(panel_id, result.get("id"))
    flash("Panel de roles publicado. El bot tiene que estar en marcha para que los botones respondan.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="roles-reaccion"))


@app.route("/guild/<int:guild_id>/roles-reaccion/<int:panel_id>/eliminar", methods=["POST"])
@login_required
@guild_access_required
def roles_reaccion_eliminar(guild_id, panel_id):
    db.delete_reaction_panel(panel_id, guild_id)
    flash("Panel eliminado de la base de datos (borra también el mensaje en Discord manualmente si hace falta).", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="roles-reaccion"))


# ---------------------------------------------------------------------------
# Niveles (XP)
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/niveles/save", methods=["POST"])
@login_required
@guild_access_required
def niveles_save(guild_id):
    db.set_config(guild_id, "xp_enabled", "1" if request.form.get("xp_enabled") == "on" else "0")
    db.set_config(guild_id, "xp_min", request.form.get("xp_min", "15"))
    db.set_config(guild_id, "xp_max", request.form.get("xp_max", "25"))
    db.set_config(guild_id, "xp_cooldown", request.form.get("xp_cooldown", "60"))
    channel_id = request.form.get("xp_levelup_channel", "").strip()
    db.set_config(guild_id, "xp_levelup_channel", channel_id or "")
    if db.is_premium(guild_id):
        try:
            multiplier = float(request.form.get("xp_multiplier", "1.0").replace(",", "."))
        except ValueError:
            multiplier = 1.0
        db.set_config(guild_id, "xp_multiplier", max(0.1, min(10.0, multiplier)))
        rank_color = request.form.get("rank_color", "").strip().lstrip("#")
        db.set_config(guild_id, "rank_color", rank_color)
    flash("Configuración de niveles guardada.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="niveles"))


@app.route("/guild/<int:guild_id>/niveles/rol-nivel/add", methods=["POST"])
@login_required
@guild_access_required
def niveles_rol_nivel_add(guild_id):
    if not db.is_premium(guild_id):
        flash("Los roles de recompensa por nivel son una función Premium.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="niveles"))
    try:
        level = int(request.form.get("level", "").strip())
    except ValueError:
        flash("Indica un nivel válido.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="niveles"))
    role_id = request.form.get("role_id", "").strip()
    if not role_id:
        flash("Elige un rol.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="niveles"))
    db.set_level_role_reward(guild_id, level, int(role_id))
    flash(f"Recompensa del nivel {level} guardada.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="niveles"))


@app.route("/guild/<int:guild_id>/niveles/rol-nivel/<int:level>/eliminar", methods=["POST"])
@login_required
@guild_access_required
def niveles_rol_nivel_eliminar(guild_id, level):
    db.remove_level_role_reward(guild_id, level)
    flash("Recompensa eliminada.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="niveles"))


# ---------------------------------------------------------------------------
# Sorteos (giveaways) - el panel puede finalizar uno antes de tiempo usando
# solo la API REST del bot (no necesita que el bot este conectado por gateway).
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/sorteos/<int:giveaway_id>/terminar", methods=["POST"])
@login_required
@guild_access_required
def sorteos_terminar(guild_id, giveaway_id):
    import random

    row = db.get_giveaway(giveaway_id)
    if row is None or row[1] != guild_id:
        flash("Ese sorteo no existe en este servidor.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="sorteos"))
    if row[8]:
        flash("Ese sorteo ya había terminado.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="sorteos"))

    _, _, channel_id, message_id, prize, winners_count, host_id, ends_at, ended, winners, created_at = row
    entries = db.list_giveaway_entries(giveaway_id)
    ganadores = random.sample(entries, min(winners_count, len(entries))) if entries else []
    db.finish_giveaway(giveaway_id, ganadores)

    try:
        if ganadores:
            texto = f"🎉 ¡Felicidades {', '.join(f'<@{w}>' for w in ganadores)}! Habéis ganado **{prize}**. (Finalizado desde el panel)"
        else:
            texto = f"El sorteo de **{prize}** ha terminado sin participantes. (Finalizado desde el panel)"
        api.send_embed(config.BOT_TOKEN, channel_id, "🎉 Sorteo finalizado", texto)
    except api.DiscordAPIError as exc:
        flash(f"El sorteo se marcó como terminado, pero no se pudo avisar en Discord: {exc}", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="sorteos"))

    flash("Sorteo finalizado y ganadores anunciados.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="sorteos"))


# ---------------------------------------------------------------------------
# Premium (Stripe)
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/premium/checkout", methods=["POST"])
@login_required
@guild_access_required
def premium_checkout(guild_id):
    try:
        guild = api.get_guild(config.BOT_TOKEN, guild_id)
        guild_name = guild.get("name", str(guild_id))
    except api.DiscordAPIError:
        guild_name = str(guild_id)

    existing = db.get_premium(guild_id)
    existing_customer_id = existing[1] if existing else None

    try:
        checkout_url = stripe_client.create_checkout_session(guild_id, guild_name, existing_customer_id)
    except stripe_client.StripeNotConfigured as exc:
        flash(str(exc), "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="premium"))
    except Exception as exc:
        flash(f"Error creando el checkout de Stripe: {exc}", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="premium"))

    return redirect(checkout_url)


@app.route("/guild/<int:guild_id>/premium/portal", methods=["POST"])
@login_required
@guild_access_required
def premium_portal(guild_id):
    existing = db.get_premium(guild_id)
    if not existing or not existing[1]:
        flash("Este servidor todavía no tiene una suscripción de Stripe asociada.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="premium"))

    try:
        portal_url = stripe_client.create_billing_portal_session(existing[1], guild_id)
    except Exception as exc:
        flash(f"Error abriendo el portal de facturación: {exc}", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="premium"))

    return redirect(portal_url)


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    """Endpoint publico que llama Stripe directamente (sin sesion de usuario).
    Verifica la firma con STRIPE_WEBHOOK_SECRET antes de fiarse de nada."""
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe_client.construct_webhook_event(payload, sig_header)
    except Exception as exc:
        return {"error": str(exc)}, 400

    event_type = event["type"]
    data_object = event["data"]["object"]

    if event_type == "checkout.session.completed":
        guild_id = data_object.get("client_reference_id") or (data_object.get("metadata") or {}).get("guild_id")
        customer_id = data_object.get("customer")
        subscription_id = data_object.get("subscription")
        if guild_id:
            db.upsert_premium(int(guild_id), "active", stripe_customer_id=customer_id, stripe_subscription_id=subscription_id)

    elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
        subscription_id = data_object.get("id")
        customer_id = data_object.get("customer")
        status = data_object.get("status")  # active, past_due, canceled, unpaid, trialing...
        period_end = data_object.get("current_period_end")
        guild_id = (data_object.get("metadata") or {}).get("guild_id")
        existing = db.get_premium_by_subscription(subscription_id) or (db.get_premium_by_customer(customer_id) if customer_id else None)
        target_guild_id = int(guild_id) if guild_id else (existing[0] if existing else None)
        if target_guild_id:
            db.upsert_premium(
                target_guild_id,
                "active" if status in ("active", "trialing") else status,
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id,
                current_period_end=period_end,
            )

    elif event_type == "customer.subscription.deleted":
        subscription_id = data_object.get("id")
        existing = db.get_premium_by_subscription(subscription_id)
        if existing:
            db.upsert_premium(existing[0], "canceled", stripe_subscription_id=subscription_id)

    return {"received": True}, 200


# ---------------------------------------------------------------------------
# Comandos personalizados (Premium)
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/comandos/add", methods=["POST"])
@login_required
@guild_access_required
def comandos_add(guild_id):
    if not db.is_premium(guild_id):
        flash("Los comandos personalizados son una función Premium. Suscribe este servidor primero.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="premium"))

    trigger_text = request.form.get("trigger", "").strip()
    response = request.form.get("response", "").strip()
    if not trigger_text or not response:
        flash("Rellena el disparador y la respuesta del comando.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="comandos"))

    db.add_custom_command(guild_id, trigger_text, response)
    flash(f"Comando \"!{trigger_text.lower()}\" creado.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="comandos"))


@app.route("/guild/<int:guild_id>/comandos/<int:command_id>/toggle", methods=["POST"])
@login_required
@guild_access_required
def comandos_toggle(guild_id, command_id):
    db.toggle_custom_command(command_id, guild_id)
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="comandos"))


@app.route("/guild/<int:guild_id>/comandos/<int:command_id>/delete", methods=["POST"])
@login_required
@guild_access_required
def comandos_delete(guild_id, command_id):
    db.delete_custom_command(command_id, guild_id)
    flash("Comando eliminado.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="comandos"))


# ---------------------------------------------------------------------------
# Perfil personalizado del bot (Premium)
# ---------------------------------------------------------------------------
@app.route("/guild/<int:guild_id>/perfil/save", methods=["POST"])
@login_required
@guild_access_required
def perfil_save(guild_id):
    if not db.is_premium(guild_id):
        flash("Personalizar el perfil del bot es una función Premium. Suscribe este servidor primero.", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="premium"))

    nickname = request.form.get("nickname", "").strip()[:32]
    try:
        api.set_bot_nickname(config.BOT_TOKEN, guild_id, nickname)
    except api.DiscordAPIError as exc:
        flash(f"No se pudo cambiar el apodo en Discord: {exc}", "error")
        return redirect(url_for("guild_detail", guild_id=guild_id, tab="perfil"))

    db.set_bot_profile_nickname(guild_id, nickname)
    flash("Apodo del bot actualizado en este servidor.", "success")
    return redirect(url_for("guild_detail", guild_id=guild_id, tab="perfil"))


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
