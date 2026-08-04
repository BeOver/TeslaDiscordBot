#!/usr/bin/env python3
"""
Refresh-Token aus .env importieren – ohne Browser-Login.

So bekommst du einen Refresh-Token:
  • iOS/Android: „Auth app for Tesla“ (Owners API v3)
  • Desktop: https://github.com/adriankumpf/tesla_auth/releases

In .env eintragen:
  TESLA_REFRESH_TOKEN=dein_refresh_token

Dann:
  python import_refresh_token.py
"""

from __future__ import annotations

import sys

from config import Config
from logging_setup import setup_logging
from teslapy import Tesla


def main() -> None:
    try:
        config = Config.from_env()
    except ValueError as exc:
        print(f"Konfigurationsfehler: {exc}")
        raise SystemExit(1) from exc

    if not config.tesla_refresh_token:
        print("TESLA_REFRESH_TOKEN fehlt in .env")
        print()
        print("Refresh-Token beschaffen:")
        print("  • App „Auth app for Tesla“ (App Store)")
        print("  • oder tesla_auth CLI: https://github.com/adriankumpf/tesla_auth")
        raise SystemExit(1)

    setup_logging(level=config.log_level, log_file=config.log_file)
    cache_path = config.tesla_cache_file

    print(f"Importiere Refresh-Token nach {cache_path} …")

    tesla = Tesla(config.tesla_email, cache_file=str(cache_path))
    tesla.token = {"refresh_token": config.tesla_refresh_token}

    try:
        tesla.refresh_token(reload=True)
    except Exception as exc:
        print(f"\n✗ Token-Refresh fehlgeschlagen: {exc}")
        print("  Token ungültig oder abgelaufen – neuen Token beschaffen.")
        raise SystemExit(1) from exc

    if not tesla.authorized:
        print("\n✗ Kein gültiger Access-Token erhalten.")
        raise SystemExit(1)

    if cache_path.exists():
        print(f"\n✓ Erfolg! {cache_path} wurde erstellt/aktualisiert.")
        print("  Jetzt Bot starten: python bot.py")
    else:
        print("\n✗ Cache-Datei fehlt trotz erfolgreichem Refresh.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
