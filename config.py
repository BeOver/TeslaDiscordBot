"""Konfiguration aus Umgebungsvariablen (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Config:
    discord_token: str
    channel_id: int
    tesla_email: str
    tesla_vin: str | None
    tesla_refresh_token: str | None
    tesla_cache_file: Path
    battery_cache_file: Path
    tesla_poll_interval_seconds: float
    channel_edit_cooldown_minutes: float
    battery_cache_max_age_seconds: float
    trip_min_distance_km: float
    log_file: Path | None
    log_level: str

    @classmethod
    def from_env(cls) -> Config:
        token = os.getenv("DISCORD_TOKEN", "").strip()
        channel_raw = os.getenv("CHANNEL_ID", "").strip()
        email = os.getenv("TESLA_EMAIL", "").strip()

        missing = [
            name
            for name, value in [
                ("DISCORD_TOKEN", token),
                ("CHANNEL_ID", channel_raw),
                ("TESLA_EMAIL", email),
            ]
            if not value
        ]
        if missing:
            raise ValueError(
                f"Fehlende Pflicht-Umgebungsvariablen: {', '.join(missing)}"
            )

        log_file_raw = os.getenv("LOG_FILE", "").strip()
        log_file = Path(log_file_raw) if log_file_raw else None

        # Abwärtskompatibel: UPDATE_INTERVAL_MINUTES → Sekunden (wird ignoriert wenn
        # TESLA_POLL_INTERVAL_SECONDS gesetzt ist)
        poll_seconds_raw = os.getenv("TESLA_POLL_INTERVAL_SECONDS", "").strip()
        if poll_seconds_raw:
            poll_interval = float(poll_seconds_raw)
        else:
            legacy_minutes = os.getenv("UPDATE_INTERVAL_MINUTES", "").strip()
            poll_interval = float(legacy_minutes) * 60 if legacy_minutes else 60.0

        return cls(
            discord_token=token,
            channel_id=int(channel_raw),
            tesla_email=email,
            tesla_vin=os.getenv("TESLA_VIN", "").strip() or None,
            tesla_refresh_token=os.getenv("TESLA_REFRESH_TOKEN", "").strip() or None,
            tesla_cache_file=BASE_DIR / "tesla_cache.json",
            battery_cache_file=BASE_DIR / "battery_cache.json",
            tesla_poll_interval_seconds=max(30.0, poll_interval),
            channel_edit_cooldown_minutes=float(
                os.getenv("CHANNEL_EDIT_COOLDOWN_MINUTES", "6")
            ),
            battery_cache_max_age_seconds=float(
                os.getenv("BATTERY_CACHE_MAX_AGE_SECONDS", "86400")
            ),
            trip_min_distance_km=float(os.getenv("TRIP_MIN_DISTANCE_KM", "0.3")),
            log_file=log_file,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
