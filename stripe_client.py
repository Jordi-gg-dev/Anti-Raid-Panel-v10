"""
stripe_client.py - Integracion con Stripe para las suscripciones Premium de
AstroCube Anti-Raid (una suscripcion por servidor de Discord).

IMPORTANTE: este modulo solo construye las llamadas a la API de Stripe con
tus propias claves (.env). Quien gestiona el dinero real es Stripe y tu
propia cuenta de Stripe, nunca este codigo ni quien lo ejecuta.

Necesitas en tu .env:
- STRIPE_SECRET_KEY   -> clave secreta de tu cuenta de Stripe (sk_live_/sk_test_)
- STRIPE_WEBHOOK_SECRET -> clave del endpoint de webhook (whsec_...)
- STRIPE_PRICE_ID     -> ID del precio recurrente que crees en el Dashboard de Stripe
- PUBLIC_BASE_URL     -> URL publica del panel (ej: https://tu-panel.up.railway.app)
"""

import stripe

import panel_config as config

stripe.api_key = config.STRIPE_SECRET_KEY


class StripeNotConfigured(Exception):
    pass


def _check_configured():
    if not config.STRIPE_SECRET_KEY or not config.STRIPE_PRICE_ID:
        raise StripeNotConfigured(
            "Stripe no esta configurado. Añade STRIPE_SECRET_KEY y STRIPE_PRICE_ID en el .env del panel."
        )


def create_checkout_session(guild_id: int, guild_name: str, existing_customer_id: str = None, buyer_user_id: int | None = None) -> str:
    """Crea una sesion de Stripe Checkout para suscribir un servidor a Premium.
    Devuelve la URL a la que hay que redirigir al usuario.
    buyer_user_id: ID de Discord de quien pulsa "Comprar", para poder mandarle
    despues un MD de bienvenida a Premium cuando el pago se complete."""
    _check_configured()

    metadata = {"guild_id": str(guild_id), "guild_name": guild_name}
    if buyer_user_id:
        metadata["buyer_user_id"] = str(buyer_user_id)

    params = {
        "mode": "subscription",
        "line_items": [{"price": config.STRIPE_PRICE_ID, "quantity": 1}],
        "success_url": f"{config.PUBLIC_BASE_URL}/guild/{guild_id}?tab=premium&checkout=success",
        "cancel_url": f"{config.PUBLIC_BASE_URL}/guild/{guild_id}?tab=premium&checkout=cancel",
        "client_reference_id": str(guild_id),
        "metadata": metadata,
        "subscription_data": {"metadata": metadata},
    }
    if existing_customer_id:
        params["customer"] = existing_customer_id
    # En mode="subscription" Stripe crea el cliente automaticamente si no se
    # pasa "customer" -> NO usar "customer_creation" aqui, ese parametro solo
    # es valido en mode="payment" (usarlo en subscription da un error 400).

    session = stripe.checkout.Session.create(**params)
    return session.url


def create_billing_portal_session(customer_id: str, guild_id: int) -> str:
    """Crea una sesion del portal de facturacion de Stripe (para que el propio
    servidor cancele o gestione su metodo de pago sin que tengas que hacerlo tu)."""
    _check_configured()
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{config.PUBLIC_BASE_URL}/guild/{guild_id}?tab=premium",
    )
    return session.url


def construct_webhook_event(payload: bytes, sig_header: str):
    """Verifica la firma del webhook y devuelve el evento ya parseado.
    Lanza stripe.error.SignatureVerificationError si la firma no es valida."""
    return stripe.Webhook.construct_event(payload, sig_header, config.STRIPE_WEBHOOK_SECRET)
