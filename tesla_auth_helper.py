"""Hilfsfunktionen für die TeslaPy-Browser-Anmeldung."""

from __future__ import annotations

import logging
import re
import webbrowser
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

_CODE_IN_URL = re.compile(r"code=([A-Za-z0-9._~-]+)")
_CODE_ONLY = re.compile(r"^[A-Za-z0-9._~-]{10,}$")


class TeslaAuthInputError(ValueError):
    """Nutzer-Eingabe für OAuth-Callback ungültig."""


def extract_auth_code(value: str) -> str | None:
    """Extrahiert den OAuth-Code aus URL oder Rohtext."""
    value = value.strip()
    if not value:
        return None

    match = _CODE_IN_URL.search(value)
    if match:
        return match.group(1)

    if _CODE_ONLY.match(value):
        return value

    return None


def normalize_authorization_response(raw: str, state: str | None) -> str:
    """
    Wandelt verschiedene Eingabeformate in eine gültige authorization_response um.

    Akzeptiert z. B.:
    - tesla://auth/callback?code=...&state=...
    - https://auth.tesla.com/void/callback?code=...&state=...
    - nur den Code (state wird aus der laufenden Session ergänzt)
    """
    raw = raw.strip()
    code = extract_auth_code(raw)
    if not code:
        raise TeslaAuthInputError(
            "Kein gültiger Auth-Code gefunden. Die Eingabe muss 'code=...' "
            "enthalten oder nur den Code selbst sein."
        )

    parsed = urlparse(raw if "://" in raw else f"dummy://callback?{raw}")
    query = parse_qs(parsed.query)
    response_state = (query.get("state") or [None])[0] or state

    if not response_state:
        raise TeslaAuthInputError(
            "Kein 'state'-Parameter gefunden. Bitte die komplette Callback-URL "
            "einfügen (nicht nur die Login-Seite oder „Verified Successfully“)."
        )

    return f"tesla://auth/callback?code={code}&state={response_state}"


def _capture_with_pywebview(auth_url: str) -> str | None:
    """Eingebetteter Browser fängt die Callback-URL automatisch ab."""
    try:
        import webview
    except ImportError:
        logger.debug("pywebview nicht installiert – Fallback auf System-Browser.")
        return None

    result: list[str] = []

    def on_loaded() -> None:
        try:
            current_url = window.get_current_url()
        except Exception as exc:
            logger.debug("WebView URL konnte nicht gelesen werden: %s", exc)
            return

        if current_url and extract_auth_code(current_url):
            result.append(current_url)
            window.destroy()

    window = webview.create_window(
        "Tesla Login",
        auth_url,
        width=520,
        height=760,
        resizable=True,
    )
    window.events.loaded += on_loaded

    print("  → Eingebetteter Login-Dialog öffnet sich …")
    print("    Nach dem Login schließt sich das Fenster automatisch.")
    webview.start(gui="edgechromium")

    return result[0] if result else None


def _prompt_manual_auth(auth_url: str) -> str:
    """Fallback: System-Browser + manuelle Eingabe."""
    print()
    print("=" * 62)
    print("  TESLA-ANMELDUNG (manuell)")
    print("=" * 62)
    print()
    print("  Hinweis: Nach „Verified Successfully“ steht in der Adresszeile")
    print("  oft KEINE URL mit code= – dann schlägt das Einfügen fehl.")
    print()
    print("  Besser:  pip install pywebview  → erneut python tesla_auth.py")
    print("  Oder:    Refresh-Token per  python import_refresh_token.py")
    print()
    print("  Falls du trotzdem manuell einfügst:")
    print("  • URL muss  code=...  enthalten")
    print("  • Oder nur den Code einfügen (langer alphanumerischer String)")
    print()
    print("  Login-URL:")
    print(f"  {auth_url}")
    print()
    print("=" * 62)
    print()

    if webbrowser.open(auth_url):
        logger.debug("Browser geöffnet: %s", auth_url)
    else:
        print("Browser konnte nicht geöffnet werden – URL oben manuell öffnen.")

    while True:
        user_input = input(
            "Callback-URL oder nur den Auth-Code einfügen: "
        ).strip()
        if user_input:
            return user_input
        print("Keine Eingabe – bitte erneut versuchen.")


def perform_tesla_login(tesla) -> None:
    """
    Führt den kompletten OAuth-Flow aus und speichert den Token-Cache.

    Bevorzugt pywebview (automatische URL-Erfassung), sonst manueller Fallback.
    """
    if tesla.authorized:
        logger.info("Tesla bereits authentifiziert.")
        return

    auth_url = tesla.authorization_url()
    if not auth_url:
        raise TeslaAuthInputError("Konnte keine Tesla-Login-URL erzeugen.")

    oauth_state = getattr(tesla, "_state", None)

    redirect_url = _capture_with_pywebview(auth_url)
    if not redirect_url:
        raw_input = _prompt_manual_auth(auth_url)
        redirect_url = normalize_authorization_response(raw_input, oauth_state)
    else:
        redirect_url = normalize_authorization_response(redirect_url, oauth_state)

    tesla.fetch_token(authorization_response=redirect_url)


def authenticate_via_browser(url: str) -> str:
    """
    TeslaPy-kompatibler Authenticator (Legacy-Schnittstelle).

    Wird von fetch_token() aufgerufen, wenn kein authorization_response übergeben wurde.
    """
    redirect_url = _capture_with_pywebview(url)
    if redirect_url:
        return redirect_url
    return _prompt_manual_auth(url)
