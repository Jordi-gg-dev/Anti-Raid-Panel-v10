# 🛰️ AstroCube Panel

Panel web privado para configurar **AstroCube Anti-Raid** sin usar comandos de Discord: servidores, anti-nuke, anti-spam, anti-raid, envío de embeds, whitelist, backups e incidentes.

**Acceso restringido**: solo puede entrar quien inicie sesión con la cuenta de Discord que pongas en `OWNER_IDS`. Nadie más puede ni siquiera ver el panel, aunque conozca la URL.

No necesita que el bot esté corriendo a la vez: habla directamente con la API de Discord (con el token del bot) y con el mismo archivo de base de datos (`data/antiraid.db`) que usa el bot, así que los cambios se aplican en cuanto el bot procese el siguiente evento o arranque.

---

## 1. Coloca esta carpeta junto a la del bot

```
tu-carpeta/
├── AstroCube-AntiRaid-Bot/
└── AstroCube-AntiRaid-Panel/   ← esta carpeta
```

Si no las tienes una junto a la otra, no pasa nada: solo tendrás que poner la ruta completa a `data/antiraid.db` en la variable `DB_PATH` del `.env` del panel.

## 2. Registrar el login de Discord (OAuth2)

En el mismo Developer Portal donde creaste el bot (https://discord.com/developers/applications → AstroCube Anti-Raid):

1. Ve a la pestaña **OAuth2**.
2. En **Redirects**, añade exactamente: `http://localhost:5000/callback` → Guardar.
3. Copia el **Client ID** (arriba, en "General Information") y el **Client Secret** (en esta misma pestaña OAuth2, botón "Reset Secret" si no lo tenías ya).

## 3. Instalar y configurar

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Rellena `.env`:
- `DISCORD_TOKEN` — el mismo token del bot.
- `DISCORD_CLIENT_ID` y `DISCORD_CLIENT_SECRET` — del paso 2.
- `OWNER_IDS` — tu ID de usuario de Discord (clic derecho sobre tu perfil con el Modo Desarrollador activo → Copiar ID). Sin esto, nadie puede entrar.
- `FLASK_SECRET_KEY` — cualquier cadena larga aleatoria.

## 4. Arrancar

```bash
python3 panel.py
```

**Nota macOS:** si te sale error de certificados SSL (como con el bot), usa:
```bash
export SSL_CERT_FILE=$(python3 -c "import certifi; print(certifi.where())")
python3 panel.py
```
(si `certifi` no está instalado en este venv: `pip install certifi` primero)

Abre **http://localhost:5000** en el navegador, pulsa "Iniciar sesión con Discord", autoriza con tu cuenta, y listo.

## 5. Puedes tener el bot y el panel corriendo a la vez

Son dos procesos independientes (dos pestañas de terminal, dos `venv` separados). Los cambios que hagas en el panel (activar/desactivar módulos, cambiar umbrales, canal de logs, whitelist...) los recoge el bot automáticamente porque ambos leen la misma base de datos.

---

## Qué puedes hacer desde el panel

- **Servidores** — lista de todos los servidores donde está el bot.
- **Configuración** (por servidor) — activar/desactivar anti-nuke/anti-spam/anti-raid, castigo automático, umbrales, canal de logs, rol automático (autorole).
- **Logs** — historial de incidentes detectados, con opción de borrarlo.
- **Embeds** — enviar un embed con tu marca a cualquier canal del servidor.
- **Whitelist** — usuarios y bots de confianza excluidos de los castigos automáticos.
- **Backups** — crear, restaurar y borrar copias de seguridad de canales/roles.
- **Global** — bloquear servidores o usuarios a nivel de todo el bot.

### Funciones Premium (de pago, vía Stripe)

- **Premium** — activar la suscripción del servidor (Stripe Checkout) y gestionarla (portal de facturación, cancelar, ver estado).
- **Comandos personalizados** — crear disparadores de texto con respuesta automática (solo servidores premium).
- **Perfil de bot** — apodo personalizado del bot en ese servidor (solo servidores premium).
- **Niveles** — XP por mensaje, subida de nivel automática, anuncio de subida de nivel, tabla de clasificación.
- **Sorteos** — crear sorteos con botón de participación, cierre automático o manual, elección de ganadores.
- **Tickets** — panel con botón para abrir tickets de soporte en canales privados, con cierre por staff.
- **Roles de reacción** — paneles con botones para que los miembros se autoasignen roles.

El dashboard principal muestra ahora una insignia 💎 junto a cada servidor con Premium activo, además del contador de tickets abiertos.

## Seguridad

- No expongas este panel a internet tal cual (está pensado para `localhost`). Si algún día quieres acceder desde fuera de tu casa, hazlo por una VPN o un túnel con autenticación adicional (ej. Cloudflare Tunnel con Access) — no lo publiques abierto en un puerto de tu router.
- `.env` nunca debe compartirse ni subirse a un repositorio.
- Si sospechas que tu `DISCORD_CLIENT_SECRET` se ha filtrado, resetéalo en el Developer Portal (OAuth2 → Reset Secret) y actualiza el `.env`.
