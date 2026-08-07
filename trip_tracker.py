"""Einfaches Fahrtenbuch – erkennt Fahrtende und postet Zusammenfassung."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord

from status_mapper import StatusColor, VehicleStatus

logger = logging.getLogger(__name__)


@dataclass
class ActiveTrip:
    started_at: str          # ISO UTC
    start_odometer_miles: float
    start_battery: int
    start_lat: float | None = None
    start_lon: float | None = None

    @classmethod
    def from_status(cls, status: VehicleStatus, lat: float | None = None, lon: float | None = None) -> ActiveTrip | None:
        if status.odometer_miles is None:
            return None
        return cls(
            started_at=datetime.now(timezone.utc).isoformat(),
            start_odometer_miles=status.odometer_miles,
            start_battery=status.battery_level,
            start_lat=lat,
            start_lon=lon,
        )


class TripTracker:
    def __init__(self, state_file: Path, enabled: bool = True) -> None:
        self._state_file = state_file
        self.enabled = enabled
        self._active: ActiveTrip | None = None
        self._last_color: StatusColor | None = None
        self._load()

    def _load(self) -> None:
        if not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            if data.get("active"):
                self._active = ActiveTrip(**data["active"])
                logger.info("Aktiven Trip aus Datei geladen (Start: %s)", self._active.started_at)
        except Exception as exc:
            logger.warning("Trip-State konnte nicht geladen werden: %s", exc)

    def _save(self) -> None:
        try:
            payload = {"active": asdict(self._active) if self._active else None}
            self._state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Trip-State konnte nicht gespeichert werden: %s", exc)

    def process(
        self,
        status: VehicleStatus,
        *,
        lat: float | None = None,
        lon: float | None = None,
    ) -> discord.Embed | None:
        """
        Wird bei jedem Status-Update aufgerufen.
        Gibt ein Embed zurück, wenn eine Fahrt gerade beendet wurde, sonst None.
        """
        if not self.enabled:
            return None

        current = status.color
        previous = self._last_color
        self._last_color = current

        # Fahrt startet
        if current == StatusColor.DRIVING and previous != StatusColor.DRIVING:
            trip = ActiveTrip.from_status(status, lat=lat, lon=lon)
            if trip:
                self._active = trip
                self._save()
                logger.info("Neue Fahrt gestartet (Akku %s%%, Odo %.1f mi)", trip.start_battery, trip.start_odometer_miles)
            return None

        # Fahrt endet
        if (
            previous == StatusColor.DRIVING
            and current != StatusColor.DRIVING
            and self._active is not None
            and status.odometer_miles is not None
        ):
            embed = self._build_embed(self._active, status, end_lat=lat, end_lon=lon)
            self._active = None
            self._save()
            logger.info("Fahrt beendet – Fahrtenbuch-Eintrag erzeugt")
            return embed

        return None

    def _build_embed(
        self,
        trip: ActiveTrip,
        end_status: VehicleStatus,
        *,
        end_lat: float | None = None,
        end_lon: float | None = None,
    ) -> discord.Embed:
        start_dt = datetime.fromisoformat(trip.started_at)
        end_dt = datetime.now(timezone.utc)
        duration = end_dt - start_dt

        hours, rem = divmod(int(duration.total_seconds()), 3600)
        minutes = rem // 60
        duration_str = f"{hours} h {minutes} min" if hours else f"{minutes} min"

        distance_miles = max(0.0, end_status.odometer_miles - trip.start_odometer_miles)
        distance_km = distance_miles * 1.60934

        battery_used = trip.start_battery - end_status.battery_level
        # grobe Schätzung: ~0.2 kWh pro %-Punkt (sehr vereinfacht, je nach Modell unterschiedlich)
        approx_kwh = battery_used * 0.2 if battery_used > 0 else 0
        consumption = (approx_kwh / distance_km * 100) if distance_km > 1 else None

        embed = discord.Embed(
            title="🚗 Fahrt beendet",
            color=0x3498DB,
            timestamp=end_dt,
        )
        embed.add_field(name="Dauer", value=duration_str, inline=True)
        embed.add_field(name="Distanz", value=f"{distance_km:.1f} km", inline=True)
        embed.add_field(
            name="Akku",
            value=f"{trip.start_battery}% → {end_status.battery_level}% ({battery_used:+d}%)",
            inline=True,
        )

        if consumption is not None and consumption > 0:
            embed.add_field(name="≈ Verbrauch", value=f"{consumption:.1f} kWh/100 km", inline=True)

        embed.add_field(
            name="Zeitraum",
            value=f"<t:{int(start_dt.timestamp())}:t> – <t:{int(end_dt.timestamp())}:t>",
            inline=False,
        )

        # Optional Maps-Links
        if trip.start_lat is not None and trip.start_lon is not None:
            start_link = f"[Start](https://maps.google.com/?q={trip.start_lat},{trip.start_lon})"
            end_link = ""
            if end_lat is not None and end_lon is not None:
                end_link = f" · [Ende](https://maps.google.com/?q={end_lat},{end_lon})"
            embed.add_field(name="Position", value=start_link + end_link, inline=False)

        embed.set_footer(text="Tesla Fahrtenbuch")
        return embed