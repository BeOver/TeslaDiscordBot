"""
Tesla-API-Zugriff über TeslaPy (Owner API).

Strategie für minimalen Aufweck-Aufwand:
1. Zuerst nur Vehicle-Summary abrufen (weckt das Fahrzeug nicht).
2. Bei asleep/offline: gecachten Akkustand verwenden.
3. Nur bei online: optional drive_state/charge_state aus Summary oder vehicle_data.

Die Klasse ist so strukturiert, dass später ein Fleet-API-Backend
(z. B. MyTeslaMate / Teslemetry) leicht eingehängt werden kann –
siehe `FleetApiClient`-Stub am Dateiende.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import requests
from teslapy import Tesla

from status_mapper import VehicleStatus, determine_status
from tesla_auth_helper import TeslaAuthInputError, perform_tesla_login

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 2.0


class TeslaDataError(Exception):
    """Fehler beim Abrufen oder Interpretieren von Tesla-Daten."""


class BatteryCache:
    """Persistiert den zuletzt bekannten Akkustand (für asleep/offline)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._level: int | None = None
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            level = data.get("battery_level")
            if isinstance(level, int) and 0 <= level <= 100:
                self._level = level
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("Battery-Cache konnte nicht geladen werden: %s", exc)

    def get(self) -> int | None:
        return self._level

    def set(self, level: int) -> None:
        if not 0 <= level <= 100:
            return
        self._level = level
        try:
            self._path.write_text(
                json.dumps({"battery_level": level}, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Battery-Cache konnte nicht gespeichert werden: %s", exc)


class TeslaClientBase(ABC):
    """Abstrakte Schnittstelle – erleichtert späteren Wechsel auf Fleet API."""

    @abstractmethod
    def get_status(self) -> VehicleStatus:
        raise NotImplementedError


class TeslaPyClient(TeslaClientBase):
    """Owner-API-Implementierung mit TeslaPy."""

    def __init__(
        self,
        email: str,
        cache_file: Path,
        battery_cache_file: Path,
        vin: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        self._email = email
        self._vin = vin
        self._battery_cache = BatteryCache(battery_cache_file)
        self._vehicle = None

        self._tesla = Tesla(email, cache_file=str(cache_file))
        if refresh_token:
            self._seed_refresh_token(refresh_token)

    def ensure_authenticated(self) -> None:
        """Stellt sicher, dass ein gültiger Tesla-Token vorhanden ist."""
        if self._tesla.authorized:
            logger.info("Tesla-Token aus Cache geladen (%s).", self._tesla.cache_file)
            return

        if self._tesla.token.get("refresh_token"):
            try:
                self._tesla.refresh_token(reload=True)
                if self._tesla.authorized:
                    logger.info("Tesla-Token via Refresh-Token erneuert.")
                    return
            except Exception as exc:
                logger.warning("Refresh aus Cache fehlgeschlagen: %s", exc)

        logger.info(
            "Kein Tesla-Token – starte Login. "
            "Tipp: `pip install pywebview` dann `python tesla_auth.py`"
        )
        try:
            perform_tesla_login(self._tesla)
        except TeslaAuthInputError as exc:
            raise TeslaDataError(
                f"Tesla-Anmeldung: {exc} "
                "Tipp: pywebview installieren oder import_refresh_token.py nutzen."
            ) from exc
        except Exception as exc:
            raise TeslaDataError(
                "Tesla-Anmeldung fehlgeschlagen. "
                "Führe `python tesla_auth.py` oder `python import_refresh_token.py` aus."
            ) from exc

        if not self._tesla.authorized:
            raise TeslaDataError(
                "Tesla-Anmeldung unvollständig – tesla_cache.json fehlt."
            )
        logger.info("Tesla-Anmeldung erfolgreich – Token gespeichert.")

    def _seed_refresh_token(self, refresh_token: str) -> None:
        """Optional: Refresh-Token aus .env laden und Cache aufbauen."""
        try:
            self._tesla.token = {"refresh_token": refresh_token}
            self._tesla.refresh_token(reload=True)
            logger.info("Tesla Refresh-Token aus .env importiert.")
        except Exception as exc:
            logger.warning("Refresh-Token aus .env konnte nicht geladen werden: %s", exc)

    def _retry(self, func, *args, **kwargs) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)
            except requests.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response else None
                if status in {401, 403}:
                    logger.info("Tesla-Token abgelaufen – versuche Refresh …")
                    try:
                        self._tesla.refresh_token(reload=True)
                        continue
                    except Exception as refresh_exc:
                        raise TeslaDataError(
                            "Tesla-Authentifizierung fehlgeschlagen."
                        ) from refresh_exc
                if status == 429 or (status and status >= 500):
                    delay = RETRY_BASE_DELAY_SECONDS * attempt
                    logger.warning(
                        "Tesla HTTP %s – Retry %s/%s in %.1fs",
                        status,
                        attempt,
                        MAX_RETRIES,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise TeslaDataError(f"Tesla HTTP-Fehler: {exc}") from exc
            except requests.RequestException as exc:
                last_error = exc
                delay = RETRY_BASE_DELAY_SECONDS * attempt
                logger.warning(
                    "Tesla-Netzwerkfehler – Retry %s/%s in %.1fs: %s",
                    attempt,
                    MAX_RETRIES,
                    delay,
                    exc,
                )
                time.sleep(delay)
            except Exception as exc:
                raise TeslaDataError(f"Unerwarteter Tesla-Fehler: {exc}") from exc

        raise TeslaDataError(
            f"Tesla-Anfrage nach {MAX_RETRIES} Versuchen fehlgeschlagen."
        ) from last_error

    def _get_vehicle(self):
        if self._vehicle is not None:
            return self._vehicle

        vehicles = self._retry(self._tesla.vehicle_list)
        if not vehicles:
            raise TeslaDataError("Keine Tesla-Fahrzeuge im Account gefunden.")

        if self._vin:
            for vehicle in vehicles:
                if vehicle.get("vin") == self._vin:
                    self._vehicle = vehicle
                    logger.info("Fahrzeug per VIN gefunden: %s", self._vin)
                    return vehicle
            raise TeslaDataError(f"Kein Fahrzeug mit VIN {self._vin} gefunden.")

        self._vehicle = vehicles[0]
        logger.info(
            "Erstes Fahrzeug verwendet (%s). TESLA_VIN setzen für gezielte Auswahl.",
            self._vehicle.get("display_name", self._vehicle.get("vin", "?")),
        )
        return self._vehicle

    @staticmethod
    def _extract_nested(data: dict[str, Any], key: str) -> dict[str, Any]:
        value = data.get(key)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _unwrap_response(data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            inner = data.get("response")
            return inner if isinstance(inner, dict) else data
        raise TeslaDataError("Ungültige Tesla-API-Antwort.")

    def _fetch_summary(self, vehicle) -> dict[str, Any]:
        """Leichtgewichtiger Abruf – weckt das Fahrzeug in der Regel nicht."""
        raw = self._retry(vehicle.get_vehicle_summary)
        return self._unwrap_response(raw)

    def _fetch_vehicle_data(self, vehicle) -> dict[str, Any]:
        """Voller Datenabruf – weckt online/asleep Fahrzeuge. Nur wenn nötig."""
        raw = self._retry(vehicle.get_vehicle_data)
        return self._unwrap_response(raw)

    def _resolve_battery(
        self,
        summary: dict[str, Any],
        *,
        allow_vehicle_data: bool,
        vehicle: dict[str, Any],
    ) -> int:
        charge = self._extract_nested(summary, "charge_state")
        level = charge.get("battery_level")

        if isinstance(level, (int, float)):
            battery = int(level)
            self._battery_cache.set(battery)
            return battery

        if allow_vehicle_data:
            logger.debug("Akkustand fehlt in Summary – vehicle_data wird abgefragt.")
            data = self._fetch_vehicle_data(vehicle)
            charge = self._extract_nested(data, "charge_state")
            level = charge.get("battery_level")
            if isinstance(level, (int, float)):
                battery = int(level)
                self._battery_cache.set(battery)
                return battery

        cached = self._battery_cache.get()
        if cached is not None:
            logger.debug("Verwende gecachten Akkustand: %s%%", cached)
            return cached

        raise TeslaDataError("Kein Akkustand verfügbar und kein Cache vorhanden.")

    def get_status(self) -> VehicleStatus:
        vehicle = self._get_vehicle()
        summary = self._fetch_summary(vehicle)

        state = str(summary.get("state", vehicle.get("state", "offline"))).lower()
        drive = self._extract_nested(summary, "drive_state")
        shift_state = drive.get("shift_state")
        speed_raw = drive.get("speed")
        speed = float(speed_raw) if speed_raw is not None else None

        # Bei online fehlen oft Details → vehicle_data nachladen
        needs_details = state == "online" and (
            shift_state is None
            or "charge_state" not in summary
            or "vehicle_state" not in summary
        )

        if needs_details:
            logger.debug("Fahrzeug online – hole vehicle_data für fehlende Felder.")
            data = self._fetch_vehicle_data(vehicle)
            drive = self._extract_nested(data, "drive_state")
            shift_state = drive.get("shift_state", shift_state)
            speed_raw = drive.get("speed", speed_raw)
            speed = float(speed_raw) if speed_raw is not None else speed
            summary = {**summary, **data}

        allow_wake = state == "online"
        battery = self._resolve_battery(
            summary,
            allow_vehicle_data=allow_wake,
            vehicle=vehicle,
        )

        # Zusätzliche Felder extrahieren
        charge = self._extract_nested(summary, "charge_state")
        vehicle_state = self._extract_nested(summary, "vehicle_state")
        climate = self._extract_nested(summary, "climate_state")

        charging_state = charge.get("charging_state")
        sentry_mode = bool(vehicle_state.get("sentry_mode", False))

        battery_range = charge.get("battery_range")
        est_battery_range = charge.get("est_battery_range")
        charge_limit_soc = charge.get("charge_limit_soc")
        charger_power = charge.get("charger_power")
        time_to_full = charge.get("time_to_full_charge")

        locked = vehicle_state.get("locked")
        odometer = vehicle_state.get("odometer")
        is_user_present = vehicle_state.get("is_user_present")

        inside_temp = climate.get("inside_temp")
        outside_temp = climate.get("outside_temp")
        is_climate_on = climate.get("is_climate_on")

        return determine_status(
            state=state,
            battery_level=battery,
            shift_state=shift_state,
            speed=speed,
            charging_state=charging_state,
            sentry_mode=sentry_mode,
            locked=locked,
            battery_range_miles=float(battery_range) if battery_range is not None else None,
            est_battery_range_miles=float(est_battery_range) if est_battery_range is not None else None,
            charge_limit_soc=int(charge_limit_soc) if charge_limit_soc is not None else None,
            charger_power=float(charger_power) if charger_power is not None else None,
            time_to_full_charge=float(time_to_full) if time_to_full is not None else None,
            inside_temp=float(inside_temp) if inside_temp is not None else None,
            outside_temp=float(outside_temp) if outside_temp is not None else None,
            is_climate_on=bool(is_climate_on) if is_climate_on is not None else None,
            odometer_miles=float(odometer) if odometer is not None else None,
            is_user_present=bool(is_user_present) if is_user_present is not None else None,
        )


class FleetApiClient(TeslaClientBase):
    """
    Platzhalter für spätere Fleet-API-Integration (MyTeslaMate, Teslemetry, …).

    Erwartet einen Proxy-Endpunkt, der bereits authentifizierte Fahrzeugdaten liefert.
    """

    def __init__(self, base_url: str, access_token: str, vin: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._vin = vin

    def get_status(self) -> VehicleStatus:
        raise NotImplementedError(
            "FleetApiClient ist ein Stub – Owner API über TeslaPyClient verwenden."
        )
