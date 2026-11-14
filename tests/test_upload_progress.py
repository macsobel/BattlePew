"""Tests that real-hardware bytecode uploads stream chunk progress over WebSocket."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import battletag.app as app_mod
from battletag.modes import ArenaEngine, PlayerState


class _FakeUploadHW:
    def __init__(self) -> None:
        self.upload_failed_guns: list[int] = []

    def ensure_initialized(self) -> None:
        pass

    def upload_mode_bytecode(self, mode_name: str, gun_ids: list[int], progress_cb: Any = None) -> bool:
        for step in (1, 2, 3, 4, 5):
            if progress_cb:
                progress_cb(step, 5)
            # Yield briefly so the event loop can run the scheduled broadcast task
            # before the next step or return, simulating real RF chunk round-trips.
            time.sleep(0.02)
        return True

    def configure_gun(self, *args: Any, **kwargs: Any) -> bool:
        return True


def test_hardware_upload_broadcasts_intermediate_progress(monkeypatch) -> None:
    eng = ArenaEngine()
    eng.use_simulator = False
    eng.hardware_write_confirmed = True
    eng.mode_name = "FreeFrag"
    eng.players = {
        2: PlayerState(gun_num=1, gun_id=2, ref_hex="a1", name="P1", avatar="Dragon", vest_ok=True, gun_online=True),
        3: PlayerState(gun_num=2, gun_id=3, ref_hex="b2", name="P2", avatar="Shark", vest_ok=True, gun_online=True),
    }
    eng.hw = _FakeUploadHW()  # type: ignore[assignment]

    monkeypatch.setattr(app_mod, "engine", eng)
    monkeypatch.setattr(eng.audio, "play_music", lambda *a, **kw: None)
    monkeypatch.setattr(eng.audio, "play_ui", lambda *a, **kw: None)

    seen_progress: list[tuple[str, dict[str, int]]] = []

    async def fake_broadcast() -> None:
        seen_progress.append((eng.phase, dict(eng.upload_progress)))

    async def fake_refresh_vests() -> None:
        pass

    monkeypatch.setattr(app_mod, "broadcast_state", fake_broadcast)
    monkeypatch.setattr(app_mod, "_refresh_vest_states", fake_refresh_vests)

    ok = asyncio.run(app_mod._execute_bytecode_upload())
    assert ok is True

    uploading_steps = [prog["current"] for phase, prog in seen_progress if phase == "UPLOADING"]
    assert 0 in uploading_steps
    assert 1 in uploading_steps
    assert 2 in uploading_steps
    assert 3 in uploading_steps
    assert 4 in uploading_steps
    assert 5 in uploading_steps

