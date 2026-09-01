"""Fahrtenbuch – erkennt Fahrten, auch kurze, und postet Zusammenfassungen."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord

from status_mapper import StatusColor, VehicleStatus

logger = logging.getLogger(__name__)

# Aktive Fahrt älter als 12h ohne Ende → verwerfen (Bot war offline)
MAX_ACTIVE_TRIP_AGE = timedelta(hours=12)


@dataclass
class ActiveTrip:
    started_at: str          # ISO UTC
    start_odometer_miles: float
    start_battery: int
    start_lat: float | None = None
    start_lon: float | None = None

    @classmethod
    def from_status(cls, status: VehicleStatus) -> ActiveTrip | None:
        if status.odometer_miles is None:
            logger.warning("Fahrt kann nicht gestartet werden – Kilometerstand fehlt.")
            return None
        return cls(
            started_at=datetime.now(timezone.utc).isoformat(),
            start_odometer_miles=status.odometer_miles,
            start_battery=status.battery_level,
            start_lat=status.latitude,
            start_lon=status.longitude,
        )


class TripTracker:
    def __init__(
        self,
        state_file: Path,
        *,
        enabled: bool = True,
        min_distance_km: float = 0.3,
    ) -> None:
        self._state_file = state_file
        self.enabled = enabled
        self._min_distance_km = min_distance_km
        self._active: ActiveTrip | None = None
        self._last_color: StatusColor | None = None
        self._last_odometer_miles: float | None = None
        self._last_poll_at: str | None = None
        self._load()

    def _load(self) -> None:
        if not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))

            last_color_raw = data.get("last_color")
            if last_color_raw:
                try:
                    self._last_color = StatusColor(last_color_raw)
                except ValueError:
                    self._last_color = None

            odo = data.get("last_odometer_miles")
            if isinstance(odo, (int, float)):
                self._last_odometer_miles = float(odo)

            self._last_poll_at = data.get("last_poll_at")

            if data.get("active"):
                trip = ActiveTrip(**data["active"])
                if self._is_trip_stale(trip):
                    logger.warning(
                        "Veraltete aktive Fahrt verworfen (Start: %s).",
                        trip.started_at,
                    )
                else:
                    self._active = trip
                    # Bot-Neustart mitten in der Fahrt: weiter als DRIVING behandeln
                    self._last_color = StatusColor.DRIVING
                    logger.info(
                        "Aktive Fahrt wiederhergestellt (Start: %s, Akku %s%%).",
                        trip.started_at,
                        trip.start_battery,
                    )
        except Exception as exc:
            logger.warning("Trip-State konnte nicht geladen werden: %s", exc)

    @staticmethod
    def _is_trip_stale(trip: ActiveTrip) -> bool:
        try:
            started = datetime.fromisoformat(trip.started_at)
        except ValueError:
            return True
        return datetime.now(timezone.utc) - started > MAX_ACTIVE_TRIP_AGE

    def _save(self) -> None:
        try:
            payload = {
                "active": asdict(self._active) if self._active else None,
                "last_color": self._last_color.value if self._last_color else None,
                "last_odometer_miles": self._last_odometer_miles,
                "last_poll_at": self._last_poll_at,
            }
            self._state_file.write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Trip-State konnte nicht gespeichert werden: %s", exc)

    def process(self, status: VehicleStatus) -> discord.Embed | None:
        """
        Wird bei jedem Tesla-Poll aufgerufen.
        Gibt ein Embed zurück, wenn eine Fahrt gerade beendet wurde.
        """
        if not self.enabled:
            return None

        now_iso = datetime.now(timezone.utc).isoformat()
        current = status.color
        previous = self._last_color

        embed: discord.Embed | None = None

        # Kilometer-basierte Erkennung verpasster Fahrten (zwischen zwei Polls)
        embed = embed or self._detect_missed_trip(status, previous, current)

        # Fahrt startet
        if current == StatusColor.DRIVING and previous != StatusColor.DRIVING:
            if self._active is None:
                trip = ActiveTrip.from_status(status)
                if trip:
                    self._active = trip
                    logger.info(
                        "Neue Fahrt gestartet (Akku %s%%, Odo %.1f mi).",
                        trip.start_battery,
                        trip.start_odometer_miles,
                    )

        # Fahrt endet
        if (
            previous == StatusColor.DRIVING
            and current != StatusColor.DRIVING
            and self._active is not None
            and status.odometer_miles is not None
        ):
            distance_km = max(
                0.0,
                (status.odometer_miles - self._active.start_odometer_miles) * 1.60934,
            )
            if distance_km >= self._min_distance_km:
                embed = self._build_embed(self._active, status)
                logger.info(
                    "Fahrt beendet – %.1f km, Akku %s%% → %s%%.",
                    distance_km,
                    self._active.start_battery,
                    status.battery_level,
                )
            else:
                logger.info(
                    "Fahrt verworfen – nur %.2f km (Minimum %.1f km).",
                    distance_km,
                    self._min_distance_km,
                )
            self._active = None

        self._last_color = current
        if status.odometer_miles is not None:
            self._last_odometer_miles = status.odometer_miles
        self._last_poll_at = now_iso
        self._save()

        return embed

    def _detect_missed_trip(
        self,
        status: VehicleStatus,
        previous: StatusColor | None,
        current: StatusColor,
    ) -> discord.Embed | None:
        """
        Erkennt Fahrten, die zwischen zwei Polls stattfanden (Start+Ende verpasst).

        Indiz: Kilometerstand stieg deutlich, aktuell nicht mehr unterwegs,
        keine aktive Fahrt, vorheriger Poll war auch nicht DRIVING.
        """
        if self._active is not None:
            return None
        if current == StatusColor.DRIVING:
            return None
        if previous == StatusColor.DRIVING:
            return None
        if self._last_odometer_miles is None or status.odometer_miles is None:
            return None
        if self._last_poll_at is None:
            return None

        delta_km = (status.odometer_miles - self._last_odometer_miles) * 1.60934
        if delta_km < self._min_distance_km:
            return None

        logger.info(
            "Verpasste Fahrt erkannt (+%.1f km seit letztem Poll) – schätze Fahrtenbuch-Eintrag.",
            delta_km,
        )

        try:
            start_dt = datetime.fromisoformat(self._last_poll_at)
        except ValueError:
            start_dt = datetime.now(timezone.utc) - timedelta(minutes=5)

        synthetic_trip = ActiveTrip(
            started_at=start_dt.isoformat(),
            start_odometer_miles=self._last_odometer_miles,
            start_battery=status.battery_level,  # ungenau – Endwert als Schätzung
            start_lat=None,
            start_lon=None,
        )
        embed = self._build_embed(synthetic_trip, status, estimated=True)
        return embed

    def _build_embed(
        self,
        trip: ActiveTrip,
        end_status: VehicleStatus,
        *,
        estimated: bool = False,
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
        approx_kwh = battery_used * 0.2 if battery_used > 0 else 0
        consumption = (approx_kwh / distance_km * 100) if distance_km > 1 else None

        title = "🚗 Fahrt beendet (geschätzt)" if estimated else "🚗 Fahrt beendet"
        embed = discord.Embed(
            title=title,
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

        if trip.start_lat is not None and trip.start_lon is not None:
            start_link = f"[Start](https://maps.google.com/?q={trip.start_lat},{trip.start_lon})"
            end_link = ""
            if end_status.latitude is not None and end_status.longitude is not None:
                end_link = (
                    f" · [Ende](https://maps.google.com/?q="
                    f"{end_status.latitude},{end_status.longitude})"
                )
            embed.add_field(name="Position", value=start_link + end_link, inline=False)

        footer = "Tesla Fahrtenbuch"
        if estimated:
            footer += " · Kurze Fahrt zwischen zwei Abfragen (geschätzt)"
        embed.set_footer(text=footer)
        return embed
