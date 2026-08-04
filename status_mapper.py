"""Mappt Tesla-Rohdaten auf Emoji-Status und Channel-Namen."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StatusColor(str, Enum):
    """Visueller Status für den Discord-Channel."""

    OFFLINE = "offline"  # ⚫ asleep / offline
    ONLINE = "online"  # 🟢 online, geparkt
    DRIVING = "driving"  # 🔵 unterwegs


EMOJI = {
    StatusColor.OFFLINE: "⚫",
    StatusColor.ONLINE: "🟢",
    StatusColor.DRIVING: "🔵",
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

    @property
    def emoji(self) -> str:
        return EMOJI[self.color]

    @property
    def channel_name(self) -> str:
        return f"{self.emoji} {self.battery_level}%"


def determine_status(
    *,
    state: str,
    battery_level: int,
    shift_state: str | None = None,
    speed: float | None = None,
) -> VehicleStatus:
    """
    Bestimmt den Anzeige-Status aus Tesla-Zustandsdaten.

    Regeln:
    - asleep / offline → schwarz
    - online + (shift P/None und speed ≈ 0) → grün
    - online + (shift D/R/N oder speed > 0) → blau
    """
    normalized_state = (state or "offline").lower()

    if normalized_state in {"asleep", "offline"}:
        color = StatusColor.OFFLINE
    elif normalized_state == "online":
        is_driving = (
            (shift_state in DRIVING_SHIFT_STATES)
            or (speed is not None and speed > SPEED_THRESHOLD_MPH)
        )
        color = StatusColor.DRIVING if is_driving else StatusColor.ONLINE
    else:
        # Unbekannte Zustände vorsichtig als offline behandeln
        color = StatusColor.OFFLINE

    return VehicleStatus(
        color=color,
        battery_level=battery_level,
        tesla_state=normalized_state,
        shift_state=shift_state,
        speed=speed,
    )


def status_message(status: VehicleStatus) -> str:
    """Formatiert eine lesbare Status-Nachricht für /status."""
    state_labels = {
        StatusColor.OFFLINE: "Offline / Sleep",
        StatusColor.ONLINE: "Online (geparkt)",
        StatusColor.DRIVING: "Unterwegs",
    }
    shift = status.shift_state or "—"
    speed = f"{status.speed:.0f} mph" if status.speed is not None else "—"
    return (
        f"{status.emoji} **{state_labels[status.color]}**\n"
        f"Akkustand: **{status.battery_level}%**\n"
        f"Tesla-Status: `{status.tesla_state}` | Gang: `{shift}` | Speed: `{speed}`"
    )
