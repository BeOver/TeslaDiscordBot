"""Discord-Channel-Umbenennung mit Cooldown und Rate-Limit-Schutz."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord

from status_mapper import VehicleStatus

logger = logging.getLogger(__name__)


class ChannelManager:
    """
    Verwaltet Channel-Namen-Updates unter Berücksichtigung von:
    - Discord Rate-Limit (~2 Renames / 10 Min. pro Channel)
    - interner Cooldown zwischen echten Edits
    - Änderung nur bei tatsächlichem Status-/Akkustandswechsel
    """

    def __init__(self, channel_id: int, cooldown_minutes: float) -> None:
        self._channel_id = channel_id
        self._cooldown = timedelta(minutes=cooldown_minutes)
        self._last_edit_at: datetime | None = None
        self._last_channel_name: str | None = None

    @property
    def last_channel_name(self) -> str | None:
        return self._last_channel_name

    def _cooldown_remaining(self) -> timedelta | None:
        if self._last_edit_at is None:
            return None
        elapsed = datetime.now(timezone.utc) - self._last_edit_at
        remaining = self._cooldown - elapsed
        return remaining if remaining.total_seconds() > 0 else None

    def should_update(self, status: VehicleStatus) -> bool:
        """Prüft, ob ein Rename sinnvoll und erlaubt ist."""
        new_name = status.channel_name

        if self._last_channel_name == new_name:
            logger.debug("Channel-Name unverändert (%s) – kein Update.", new_name)
            return False

        remaining = self._cooldown_remaining()
        if remaining is not None:
            logger.info(
                "Cooldown aktiv – %s noch %.0fs bis zum nächsten Rename.",
                new_name,
                remaining.total_seconds(),
            )
            return False

        return True

    async def update_channel_name(
        self,
        bot: discord.Client,
        status: VehicleStatus,
    ) -> bool:
        """
        Benennt den Channel um, wenn nötig. Gibt True zurück bei erfolgreichem Edit.
        """
        new_name = status.channel_name

        if not self.should_update(status):
            return False

        channel = bot.get_channel(self._channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(self._channel_id)
            except discord.HTTPException as exc:
                logger.error("Channel %s nicht abrufbar: %s", self._channel_id, exc)
                return False

        if not isinstance(channel, discord.abc.GuildChannel):
            logger.error("CHANNEL_ID %s ist kein Guild-Channel.", self._channel_id)
            return False

        # Discord Channel-Namen: max. 100 Zeichen, hier sind es nur wenige
        for attempt in range(1, 4):
            try:
                await channel.edit(name=new_name, reason="Tesla-Status-Update")
                self._last_edit_at = datetime.now(timezone.utc)
                self._last_channel_name = new_name
                logger.info("Channel umbenannt → %s", new_name)
                return True
            except discord.HTTPException as exc:
                if exc.status == 429:
                    retry_after = getattr(exc, "retry_after", 30)
                    logger.warning(
                        "Discord Rate-Limit – warte %.1fs (Versuch %s/3).",
                        retry_after,
                        attempt,
                    )
                    await asyncio.sleep(float(retry_after) + 1)
                    continue
                logger.error("Channel-Rename fehlgeschlagen: %s", exc)
                return False

        logger.error("Channel-Rename nach 3 Versuchen abgebrochen.")
        return False

    def sync_name_from_channel(self, current_name: str) -> None:
        """Übernimmt den bestehenden Channel-Namen beim Start (kein sofortiger Edit)."""
        self._last_channel_name = current_name
        logger.debug("Bestehender Channel-Name übernommen: %s", current_name)
