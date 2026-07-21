"""Kompakte Live-Statusanzeige fuer die Windows-Konsole."""

import asyncio
import datetime
import os
import sys
from collections import deque

import psutil


class Dashboard:
    """Aktualisiert den Konsolenstatus, ohne den Discord-Eventloop zu blockieren."""

    def __init__(self, bot):
        self.bot = bot
        self.errors: deque[str] = deque(maxlen=3)
        self._task: asyncio.Task | None = None

    def record_error(self, message: str) -> None:
        self.errors.appendleft(message.replace("\n", " ")[:120])

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="console-dashboard")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        process = psutil.Process(os.getpid())
        while not self.bot.is_closed():
            try:
                self._draw(process)
            except Exception as exc:  # Das Dashboard darf den Bot nie stoeren.
                self.record_error(f"Dashboard: {exc}")
            await asyncio.sleep(10)

    def _draw(self, process: psutil.Process) -> None:
        tickets = self.bot.get_cog("Tickets")
        open_count = tickets.open_ticket_count if tickets else 0
        average = tickets.rating_average if tickets else 0.0
        users = sum(guild.member_count or 0 for guild in self.bot.guilds)
        uptime = datetime.datetime.now(datetime.timezone.utc) - self.bot.started_at
        errors = " | ".join(self.errors) if self.errors else "Keine"
        cogs = ", ".join(name.removeprefix("cogs.") for name in self.bot.extensions) or "Keine"
        status = "ONLINE" if self.bot.is_ready() else "VERBINDUNG..."
        ping = round(self.bot.latency * 1000) if self.bot.is_ready() else 0

        lines = [
            "=" * 68,
            "                         AVOKE BOT DASHBOARD",
            "=" * 68,
            f"Status: {status:<15} Ping: {ping:>4} ms",
            f"CPU: {psutil.cpu_percent():>5.1f}%             RAM: {process.memory_info().rss / 1024 / 1024:>6.1f} MB",
            f"Server: {len(self.bot.guilds):>3}                User: {users:>6}",
            f"Offene Tickets: {open_count:>3}        Bewertung: {average:.2f}/5",
            f"Uptime: {str(uptime).split('.')[0]}",
            f"Letzte Fehler: {errors}",
            f"Geladene Cogs: {cogs}",
            "=" * 68,
        ]
        os.system("cls" if os.name == "nt" else "clear")
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()
