"""Mappt Tesla-Rohdaten auf Emoji-Status und Channel-Namen."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class StatusColor(str, Enum):
    """Visueller Status für den Discord-Channel."""

    OFFLINE = "offline"      # ⚫ asleep / offline
    SERVICE = "service"      # 🛠️ Service / Werkstatt
    ONLINE = "online"        # 🟢 online, geparkt
    DRIVING = "driving"      # 🔵 unterwegs
    CHARGING = "charging"    # 🟠 lädt
    SENTRY = "sentry"        # 🔴 Wächtermodus


EMOJI = {
    StatusColor.OFFLINE: "⚫",
    StatusColor.SERVICE: "🛠️",
    StatusColor.ONLINE: "🟢",
    StatusColor.DRIVING: "🔵",
    StatusColor.CHARGING: "🟠",
    StatusColor.SENTRY: "🔴",
}

# Schwellwert in mph (Tesla API liefert speed in mph)
SPEED_THRESHOLD_MPH = 1.0
DRIVING_SHIFT_STATES = frozenset({"D", "R", "N"})


@dataclass(frozen=True)
class VehicleStatus:
    color: StatusColor
    battery_level: int
    tesla_state: str
    shift_state: str | None
    speed: float | None
    # Zusätzliche Felder
    charging_state: str | None = None
    sentry_mode: bool = False
    in_service: bool = False
    service_mode: bool = False
    locked: bool | None = None
    battery_range_miles: float | None = None
    est_battery_range_miles: float | None = None
    charge_limit_soc: int | None = None
    charger_power: float | None = None
    time_to_full_charge: float | None = None
    inside_temp: float | None = None
    outside_temp: float | None = None
    is_climate_on: bool | None = None
    odometer_miles: float | None = None
    is_user_present: bool | None = None
    latitude: float | None = None
    longitude: float | None = None
    battery_from_cache: bool = False

    @property
    def emoji(self) -> str:
        return EMOJI[self.color]

    @property
    def channel_name(self) -> str:
        # Format: Emoji + Akkustand + %  (ohne Leerzeichen)
        return f"{self.emoji}{self.battery_level}%"

    @property
    def battery_range_km(self) -> float | None:
        if self.battery_range_miles is None:
            return None
        return round(self.battery_range_miles * 1.60934, 1)

    @property
    def est_battery_range_km(self) -> float | None:
        if self.est_battery_range_miles is None:
            return None
        return round(self.est_battery_range_miles * 1.60934, 1)

    @property
    def odometer_km(self) -> float | None:
        if self.odometer_miles is None:
            return None
        return round(self.odometer_miles * 1.60934, 1)


def determine_status(
    *,
    state: str,
    battery_level: int,
    shift_state: str | None = None,
    speed: float | None = None,
    charging_state: str | None = None,
    sentry_mode: bool = False,
    in_service: bool = False,
    service_mode: bool = False,
    **extra: Any,
) -> VehicleStatus:
    """
    Bestimmt den Anzeige-Status aus Tesla-Zustandsdaten.

    Priorität:
    1. asleep / offline                → ⚫
    2. in_service oder service_mode   → 🛠️
    3. online + fahrend               → 🔵
    4. online + lädt                  → 🟠
    5. online + Wächtermodus          → 🔴
    6. online + geparkt               → 🟢
    """
    normalized_state = (state or "offline").lower()
    charging = (charging_state or "").lower() == "charging"
    is_service = in_service or service_mode

    if normalized_state in {"asleep", "offline"}:
        color = StatusColor.OFFLINE
    elif is_service:
        color = StatusColor.SERVICE
    elif normalized_state == "online":
        is_driving = (
            (shift_state in DRIVING_SHIFT_STATES)
            or (speed is not None and speed > SPEED_THRESHOLD_MPH)
        )
        if is_driving:
            color = StatusColor.DRIVING
        elif charging:
            color = StatusColor.CHARGING
        elif sentry_mode:
            color = StatusColor.SENTRY
        else:
            color = StatusColor.ONLINE
    else:
        color = StatusColor.OFFLINE

    return VehicleStatus(
        color=color,
        battery_level=battery_level,
        tesla_state=normalized_state,
        shift_state=shift_state,
        speed=speed,
        charging_state=charging_state,
        sentry_mode=sentry_mode,
        in_service=in_service,
        service_mode=service_mode,
        **extra,
    )


def status_message(status: VehicleStatus) -> str:
    """Formatiert eine lesbare Status-Nachricht für /status."""
    state_labels = {
        StatusColor.OFFLINE: "Offline / Sleep",
        StatusColor.SERVICE: "Service / Werkstatt",
        StatusColor.ONLINE: "Online (geparkt)",
        StatusColor.DRIVING: "Unterwegs",
        StatusColor.CHARGING: "Lädt",
        StatusColor.SENTRY: "Wächtermodus",
    }

    lines = [
        f"{status.emoji} **{state_labels[status.color]}**",
        f"Akkustand: **{status.battery_level}%**",
    ]

    if status.battery_from_cache:
        lines.append("_Akkustand: letzter bekannter Wert (Fahrzeug schläft)_")

    if status.battery_range_km is not None:
        lines.append(f"Reichweite: **{status.battery_range_km} km** (rated)")
    if status.est_battery_range_km is not None:
        lines.append(f"Geschätzte Reichweite: **{status.est_battery_range_km} km**")

    if status.charging_state:
        lines.append(f"Ladezustand: `{status.charging_state}`")
    if status.charge_limit_soc is not None:
        lines.append(f"Ladelimit: **{status.charge_limit_soc}%**")
    if status.charger_power is not None and status.charger_power > 0:
        lines.append(f"Ladeleistung: **{status.charger_power:.1f} kW**")
    if status.time_to_full_charge is not None and status.time_to_full_charge > 0:
        hours = int(status.time_to_full_charge)
        minutes = int((status.time_to_full_charge - hours) * 60)
        lines.append(f"Restladezeit: **{hours}h {minutes}m**")

    # Service-Info
    if status.in_service or status.service_mode:
        service_parts = []
        if status.in_service:
            service_parts.append("in_service")
        if status.service_mode:
            service_parts.append("service_mode")
        lines.append(f"Service: **an** (`{' + '.join(service_parts)}`)")
    else:
        lines.append("Service: **aus**")

    lines.append(f"Wächtermodus: **{'an' if status.sentry_mode else 'aus'}**")

    if status.locked is not None:
        lines.append(f"Verriegelt: **{'ja' if status.locked else 'nein'}**")

    if status.is_climate_on is not None:
        climate = "an" if status.is_climate_on else "aus"
        temp_info = ""
        if status.inside_temp is not None:
            temp_info = f" (innen {status.inside_temp:.1f}°C"
            if status.outside_temp is not None:
                temp_info += f" / außen {status.outside_temp:.1f}°C"
            temp_info += ")"
        lines.append(f"Klima: **{climate}**{temp_info}")

    if status.odometer_km is not None:
        lines.append(f"Kilometerstand: **{status.odometer_km:,.0f} km**".replace(",", "."))

    shift = status.shift_state or "—"
    speed = f"{status.speed:.0f} mph" if status.speed is not None else "—"
    lines.append(f"Tesla-Status: `{status.tesla_state}` | Gang: `{shift}` | Speed: `{speed}`")

    if status.is_user_present is not None:
        lines.append(f"Person im Auto: **{'ja' if status.is_user_present else 'nein'}**")

    return "\n".join(lines)