"""
AstroCube Panel - Pequeño wrapper sobre la API REST de Discord.

El panel NO necesita que el bot esté corriendo: habla directamente con la
API de Discord usando el mismo token del bot (para leer servidores/canales
y enviar mensajes) y con la API OAuth2 (para el login del propietario).
"""

import requests

API_BASE = "https://discord.com/api/v10"


class DiscordAPIError(Exception):
    pass


def _headers(token: str) -> dict:
    return {"Authorization": f"Bot {token}", "Content-Type": "application/json"}


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """Envuelve requests.* para que cualquier fallo de red (no solo de la API)
    se convierta en DiscordAPIError, y así las rutas de Flask puedan mostrar
    un mensaje amable en vez de una página de error 500."""
    try:
        return requests.request(method, url, timeout=kwargs.pop("timeout", 10), **kwargs)
    except requests.RequestException as exc:
        raise DiscordAPIError(f"No se pudo contactar con Discord: {exc}") from exc


def get_bot_guilds(token: str) -> list[dict]:
    resp = _request("GET", f"{API_BASE}/users/@me/guilds", headers=_headers(token))
    if resp.status_code != 200:
        raise DiscordAPIError(f"No se pudo obtener la lista de servidores ({resp.status_code})")
    return resp.json()


def get_bot_user(token: str) -> dict:
    """Info del propio bot (para la pestaña 'Bot')."""
    resp = _request("GET", f"{API_BASE}/users/@me", headers=_headers(token))
    if resp.status_code != 200:
        raise DiscordAPIError(f"No se pudo obtener la información del bot ({resp.status_code})")
    return resp.json()


def get_guild(token: str, guild_id: int, with_counts: bool = False) -> dict:
    url = f"{API_BASE}/guilds/{guild_id}"
    if with_counts:
        url += "?with_counts=true"
    resp = _request("GET", url, headers=_headers(token))
    if resp.status_code != 200:
        raise DiscordAPIError(f"No se pudo obtener el servidor {guild_id} ({resp.status_code})")
    return resp.json()


def get_guild_channels(token: str, guild_id: int) -> list[dict]:
    resp = _request("GET", f"{API_BASE}/guilds/{guild_id}/channels", headers=_headers(token))
    if resp.status_code != 200:
        raise DiscordAPIError(f"No se pudieron obtener los canales ({resp.status_code})")
    return resp.json()


def get_guild_roles(token: str, guild_id: int) -> list[dict]:
    resp = _request("GET", f"{API_BASE}/guilds/{guild_id}/roles", headers=_headers(token))
    if resp.status_code != 200:
        raise DiscordAPIError(f"No se pudieron obtener los roles ({resp.status_code})")
    return resp.json()


def get_guild_member(token: str, guild_id: int, user_id: int) -> dict | None:
    """Miembro de un servidor concreto. None si esa persona no está en el servidor
    (404), en vez de lanzar un error — es un caso válido, no un fallo de red."""
    resp = _request("GET", f"{API_BASE}/guilds/{guild_id}/members/{user_id}", headers=_headers(token))
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise DiscordAPIError(f"No se pudo comprobar la membresía ({resp.status_code})")
    return resp.json()


ADMINISTRATOR_BIT = 0x8
MANAGE_GUILD_BIT = 0x20


def roles_with_admin(roles: list[dict]) -> list[dict]:
    """Filtra los roles que tienen permiso de Administrador o Gestionar Servidor."""
    result = []
    for r in roles:
        try:
            perms = int(r.get("permissions", "0"))
        except ValueError:
            perms = 0
        if perms & ADMINISTRATOR_BIT or perms & MANAGE_GUILD_BIT:
            result.append(r)
    return result


def send_embed(token: str, channel_id: int, title: str, description: str, color_hex: str = "5865F2") -> dict:
    try:
        color_int = int(color_hex.lstrip("#"), 16)
    except ValueError:
        color_int = 0x5865F2
    payload = {"embeds": [{"title": title, "description": description, "color": color_int}]}
    resp = _request("POST", f"{API_BASE}/channels/{channel_id}/messages", headers=_headers(token), json=payload)
    if resp.status_code not in (200, 201):
        raise DiscordAPIError(f"No se pudo enviar el mensaje ({resp.status_code}: {resp.text[:200]})")
    return resp.json()


def create_channel(token: str, guild_id: int, name: str, channel_type: int = 0, parent_id: int | None = None) -> dict:
    payload = {"name": name, "type": channel_type}
    if parent_id:
        payload["parent_id"] = parent_id
    resp = _request("POST", f"{API_BASE}/guilds/{guild_id}/channels", headers=_headers(token), json=payload)
    if resp.status_code not in (200, 201):
        raise DiscordAPIError(f"No se pudo crear el canal ({resp.status_code})")
    return resp.json()


def create_role(token: str, guild_id: int, name: str, color: int = 0, hoist: bool = False, mentionable: bool = False, permissions: str = "0") -> dict:
    payload = {"name": name, "color": color, "hoist": hoist, "mentionable": mentionable, "permissions": permissions}
    resp = _request("POST", f"{API_BASE}/guilds/{guild_id}/roles", headers=_headers(token), json=payload)
    if resp.status_code not in (200, 201):
        raise DiscordAPIError(f"No se pudo crear el rol ({resp.status_code})")
    return resp.json()


def set_bot_nickname(token: str, guild_id: int, nickname: str) -> dict:
    """Cambia el apodo del bot en ESE servidor concreto (PATCH @me).
    Nota: Discord no permite que un mismo bot tenga un avatar distinto por
    servidor, solo el apodo/nickname se puede personalizar por servidor."""
    payload = {"nick": nickname or None}
    resp = _request("PATCH", f"{API_BASE}/guilds/{guild_id}/members/@me", headers=_headers(token), json=payload)
    if resp.status_code not in (200, 204):
        raise DiscordAPIError(f"No se pudo cambiar el apodo del bot ({resp.status_code}: {resp.text[:200]})")
    return resp.json() if resp.text else {}


def send_message_with_buttons(token: str, channel_id: int, title: str, description: str, buttons: list, color_hex: str = "5865F2") -> dict:
    """Publica un embed con botones (roles de reaccion, etc.) usando la API
    REST directamente. 'buttons' es una lista de dicts: {"label", "custom_id", "emoji" (opcional)}.
    Se agrupan en filas de hasta 5 botones (limite de Discord por fila)."""
    try:
        color_int = int(color_hex.lstrip("#"), 16)
    except ValueError:
        color_int = 0x5865F2

    rows = []
    for i in range(0, len(buttons), 5):
        chunk = buttons[i:i + 5]
        components = []
        for b in chunk:
            comp = {"type": 2, "style": 1, "label": b["label"][:80], "custom_id": b["custom_id"]}
            if b.get("emoji"):
                comp["emoji"] = {"name": b["emoji"]}
            components.append(comp)
        rows.append({"type": 1, "components": components})

    payload = {
        "embeds": [{"title": title, "description": description, "color": color_int}],
        "components": rows,
    }
    resp = _request("POST", f"{API_BASE}/channels/{channel_id}/messages", headers=_headers(token), json=payload)
    if resp.status_code not in (200, 201):
        raise DiscordAPIError(f"No se pudo publicar el panel ({resp.status_code}: {resp.text[:200]})")
    return resp.json()


def leave_guild(token: str, guild_id: int) -> None:
    resp = _request("DELETE", f"{API_BASE}/users/@me/guilds/{guild_id}", headers=_headers(token))
    if resp.status_code not in (200, 204):
        raise DiscordAPIError(f"No se pudo abandonar el servidor ({resp.status_code})")


# ---------------------------------------------------------------------------
# OAuth2 (login del propietario del panel)
# ---------------------------------------------------------------------------
def oauth_authorize_url(client_id: str, redirect_uri: str) -> str:
    from urllib.parse import urlencode
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "prompt": "consent",
    }
    return f"https://discord.com/oauth2/authorize?{urlencode(params)}"


def oauth_exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    resp = _request("POST", f"{API_BASE}/oauth2/token", data=data, headers=headers)
    if resp.status_code != 200:
        raise DiscordAPIError(f"No se pudo intercambiar el código OAuth2 ({resp.status_code}: {resp.text[:200]})")
    return resp.json()


def oauth_get_user(access_token: str) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = _request("GET", f"{API_BASE}/users/@me", headers=headers)
    if resp.status_code != 200:
        raise DiscordAPIError(f"No se pudo obtener el usuario autenticado ({resp.status_code})")
    return resp.json()
