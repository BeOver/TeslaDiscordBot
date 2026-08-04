"""
Tesla Discord Status Bot

Aktualisiert regelmäßig den Namen eines Discord-Channels basierend
auf dem Tesla-Fahrzeugstatus (Offline / Online / Unterwegs + Akkustand).
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import tasks

from channel_manager import ChannelManager
from config import Config
from logging_setup import setup_logging
from status_mapper import VehicleStatus, status_message
from tesla_client import TeslaDataError, TeslaPyClient

logger = logging.getLogger(__name__)


class TeslaDiscordBot(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(intents=intents)

        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.tesla = TeslaPyClient(
            email=config.tesla_email,
            cache_file=config.tesla_cache_file,
            battery_cache_file=config.battery_cache_file,
            vin=config.tesla_vin,
            refresh_token=config.tesla_refresh_token,
        )
        self.channel_manager = ChannelManager(
            channel_id=config.channel_id,
            cooldown_minutes=config.channel_edit_cooldown_minutes,
        )
        self._latest_status: VehicleStatus | None = None

        self._register_commands()

    def _register_commands(self) -> None:
        @self.tree.command(
            name="status",
            description="Zeigt den aktuellen Tesla-Status (ohne Channel-Umbenennung).",
        )
        async def status_command(interaction: discord.Interaction) -> None:
            await interaction.response.defer(thinking=True)
            try:
                vehicle_status = await self._fetch_tesla_status()
                self._latest_status = vehicle_status
                await interaction.followup.send(status_message(vehicle_status))
            except TeslaDataError as exc:
                await interaction.followup.send(
                    f"⚠️ Tesla-Status konnte nicht abgerufen werden:\n```{exc}```"
                )
            except Exception as exc:
                logger.exception("Fehler bei /status")
                await interaction.followup.send(
                    f"⚠️ Unerwarteter Fehler:\n```{exc}```"
                )

    async def setup_hook(self) -> None:
        await self.tree.sync()
        logger.info("Slash-Commands synchronisiert.")

        self.update_channel_task.change_interval(
            minutes=self.config.update_interval_minutes
        )
        if not self.update_channel_task.is_running():
            self.update_channel_task.start()

    async def on_ready(self) -> None:
        logger.info("Eingeloggt als %s (ID: %s)", self.user, self.user.id)

        channel = self.get_channel(self.config.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(self.config.channel_id)
            except discord.HTTPException:
                channel = None

        if isinstance(channel, discord.abc.GuildChannel):
            self.channel_manager.sync_name_from_channel(channel.name)
        else:
            logger.warning(
                "Channel %s nicht gefunden – prüfe CHANNEL_ID und Bot-Berechtigungen.",
                self.config.channel_id,
            )

        # Optional: direkt beim Start aktualisieren (Cooldown beachtet intern)
        await self._run_update_cycle()

    async def _fetch_tesla_status(self) -> VehicleStatus:
        """Blockierende TeslaPy-Aufrufe in einen Worker-Thread auslagern."""
        return await asyncio.to_thread(self.tesla.get_status)

    async def _run_update_cycle(self) -> None:
        try:
            vehicle_status = await self._fetch_tesla_status()
            self._latest_status = vehicle_status
            logger.info(
                "Tesla-Status: %s | %s%% | state=%s",
                vehicle_status.emoji,
                vehicle_status.battery_level,
                vehicle_status.tesla_state,
            )
            await self.channel_manager.update_channel_name(self, vehicle_status)
        except TeslaDataError as exc:
            logger.error("Tesla-Fehler im Update-Zyklus: %s", exc)
        except Exception:
            logger.exception("Unerwarteter Fehler im Update-Zyklus")

    @tasks.loop(minutes=7)
    async def update_channel_task(self) -> None:
        await self._run_update_cycle()

    @update_channel_task.before_loop
    async def before_update_channel_task(self) -> None:
        await self.wait_until_ready()


def main() -> None:
    try:
        config = Config.from_env()
    except ValueError as exc:
        print(f"Konfigurationsfehler: {exc}")
        raise SystemExit(1) from exc

    setup_logging(level=config.log_level, log_file=config.log_file)

    bot = TeslaDiscordBot(config)

    # Tesla-Login im Hauptthread, BEVOR Discord startet –
    # sonst wartet input() unsichtbar in einem Hintergrund-Thread.
    print("Prüfe Tesla-Anmeldung …")
    bot.tesla.ensure_authenticated()

    bot.run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
