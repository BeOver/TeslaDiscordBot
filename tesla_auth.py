#!/usr/bin/env python3
"""
Einmalige Tesla-Anmeldung – erstellt tesla_cache.json.

Vor dem ersten Bot-Start ausführen:

    pip install pywebview
    python tesla_auth.py

Alternative ohne Browser-Callback:

    python import_refresh_token.py
"""

from __future__ import annotations

import sys

from config import Config
from logging_setup import setup_logging
from tesla_auth_helper import TeslaAuthInputError, perform_tesla_login
from teslapy import Tesla


def main() -> None:
    try:
        config = Config.from_env()
    except ValueError as exc:
        print(f"Konfigurationsfehler: {exc}")
        raise SystemExit(1) from exc

    setup_logging(level=config.log_level, log_file=config.log_file)

    cache_path = config.tesla_cache_file
    print(f"Tesla-Cache-Datei: {cache_path}")

    tesla = Tesla(config.tesla_email, cache_file=str(cache_path))

    if tesla.authorized:
        print("\n✓ Bereits angemeldet – tesla_cache.json ist vorhanden und gültig.")
        print("  Du kannst den Bot starten: python bot.py")
        return

    print("\nNoch nicht angemeldet – starte Login …\n")

    try:
        perform_tesla_login(tesla)
    except TeslaAuthInputError as exc:
        print(f"\n✗ Eingabe ungültig: {exc}")
        print("\nTipp: pip install pywebview  und erneut versuchen,")
        print("      oder Refresh-Token: python import_refresh_token.py")
        raise SystemExit(1) from exc
    except Exception as exc:
        print(f"\n✗ Anmeldung fehlgeschlagen: {exc}")
        print("\nAlternativen:")
        print("  1. pip install pywebview  →  python tesla_auth.py")
        print("  2. Refresh-Token importieren: python import_refresh_token.py")
        raise SystemExit(1) from exc

    if cache_path.exists():
        print(f"\n✓ Erfolg! Cache gespeichert: {cache_path}")
        print("  Jetzt Bot starten: python bot.py")
    else:
        print("\n✗ Cache-Datei wurde nicht erstellt – bitte erneut versuchen.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
