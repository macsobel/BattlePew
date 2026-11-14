"""Background queue for gun profile writes.

A name/avatar save in either interface (Arena Master roster editor or
phone HUD) writes the new profile into the gun's flash. Saves from up
to 8 players can arrive at once, but the UbiConnect is one half-duplex
HID handle and a flash write is a multi-step sequence — so every save
becomes a job in this queue and a single background worker runs them
one at a time, in the background, with no queue UI for the user to
manage. A newer save for the same gun supersedes a still-waiting older
one, so rapid re-edits cost one flash cycle, not one per keystroke.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from battletag.modes import ArenaEngine


@dataclass
class WriteJob:
    """One queued profile write: burn name/avatar into one gun's flash."""

    gun_num: int
    name: str
    avatar: str
    enqueued_at: float = field(default_factory=time.time)


class ProfileWriteQueue:
    """FIFO job queue with per-gun coalescing.

    `submit` drops any still-waiting job for the same gun before
    appending the new one (the newest save wins). The running job is
    never interrupted — a flash sequence must run to completion.
    """

    def __init__(self) -> None:
        self._pending: deque[WriteJob] = deque()
        self._event = asyncio.Event()

    def submit(self, job: WriteJob) -> WriteJob:
        self._pending = deque(j for j in self._pending if j.gun_num != job.gun_num)
        self._pending.append(job)
        self._event.set()
        return job

    async def take(self) -> WriteJob:
        while True:
            if self._pending:
                job = self._pending.popleft()
                if not self._pending:
                    self._event.clear()
                return job
            self._event.clear()
            await self._event.wait()

    def __len__(self) -> int:
        return len(self._pending)

    def pending_jobs(self) -> list[WriteJob]:
        return list(self._pending)


def execute_profile_write(engine: ArenaEngine, job: WriteJob) -> tuple[bool, str]:
    """Run one profile write to completion (blocking; call in a thread).

    Re-checks the pairing-page gate at start (a job queued in setup must
    not run after a match starts), writes via the simulator or the real
    transport, and only then updates the displayed profile — the display
    follows the gun, never the other way round. Sets the terminal
    per-gun status (saved/failed) on the engine; the caller broadcasts.
    """
    from battletag.hardware import RealHIDTransport

    def fail(detail: str) -> tuple[bool, str]:
        engine.profile_write_status[job.gun_num] = {"state": "failed", "detail": detail}
        return False, detail

    player = next((p for p in engine.players.values() if p.gun_num == job.gun_num), None)
    if player is None:
        return fail(f"Player {job.gun_num} is no longer paired.")
    if not engine.profiles_editable():
        return fail(engine.profiles_locked_reason())
    if engine.use_simulator:
        ok, detail = engine.sim.write_gun_profile(player.gun_id, job.name, job.avatar)
    else:
        if not engine.hardware_write_confirmed:
            return fail("Hardware writes are not authorized.")
        if engine.hw is None:
            try:
                engine.hw = RealHIDTransport(confirm_write=True)
                engine.hw.open()
                engine.hw.ensure_initialized()
            except Exception as exc:  # noqa: BLE001 - report, don't crash the worker
                return fail(f"Could not open the UbiConnect: {exc}")
        ok, detail = engine.hw.write_gun_profile(player.gun_id, job.name, job.avatar)
    if not ok:
        return fail(detail)
    # The gun now stores the new profile: show it, drop the host-side
    # override (a future re-pair reads it back from the gun itself).
    player.name = job.name.strip()
    player.avatar = job.avatar
    if player.gun_id in engine.sim.guns:
        sg = engine.sim.guns[player.gun_id]
        sg.name = player.name
        sg.avatar = player.avatar
    engine.mark_profile_stored(job.gun_num)
    engine.add_event(
        "system",
        f"Player {job.gun_num}'s T-Blaster now stores \"{player.display_name}\" ({player.avatar}).",
        actor=player,
    )
    engine.audio.play_ui("Validation.wav")
    engine.profile_write_status[job.gun_num] = {"state": "saved", "detail": "verified"}
    return True, "verified"
