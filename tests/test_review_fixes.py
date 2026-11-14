"""Tests for the 2026-11-12 review fixes: upload pacing/fail-loud, profile
locks, global settings, stored-profile preference, catalog defaults, and
frontend-compatible audio cue fields."""

from __future__ import annotations

import io
import time

import pytest

from battletag.audio import AudioCue
from battletag.hardware import RealHIDTransport
from battletag.modes import MODE_CATALOG, ArenaEngine


class FakeHIDDevice:
    """Minimal hid.device stand-in: ACKs upload steps, optionally dropping first N writes."""

    def __init__(self, gun_id: int = 0x02, ack: bool = True, drop_writes: set[int] | None = None) -> None:
        self.gun_id = gun_id
        self.ack = ack
        self.drop_writes = drop_writes or set()
        self.queue: list[bytes] = []
        self.write_times: list[float] = []
        self.ack_times: list[float] = []
        self.writes: list[bytes] = []

    def set_nonblocking(self, _flag: bool) -> None:
        pass

    def write(self, data: bytes) -> None:
        write_num = len(self.write_times) + 1
        self.write_times.append(time.monotonic())
        self.writes.append(bytes(data))
        if not self.ack or write_num in self.drop_writes:
            return
        payload = bytes(data[1:])  # strip the 0x00 report ID
        cmd = payload[2]
        reply = bytearray(64)
        reply[0] = 0x05
        reply[1] = self.gun_id
        reply[2] = cmd
        if cmd == 0x86:
            reply[3] = payload[3]  # 0x00 unlock / 0x01 lock echo
        elif cmd == 0x83:
            next_offset = int.from_bytes(payload[3:5], "big") + payload[5]
            reply[3:5] = next_offset.to_bytes(2, "big")
        self.queue.append(bytes(reply))

    def read(self, _size: int, timeout_ms: int = 100) -> bytes:
        if self.queue:
            self.ack_times.append(time.monotonic())
            return self.queue.pop(0)
        return b""


def _transport(fake: FakeHIDDevice) -> RealHIDTransport:
    hw = RealHIDTransport(confirm_write=True)
    hw._log_file = io.StringIO()  # keep test logs out of logs/
    hw.dev = fake
    hw.initialized = True
    return hw


def test_upload_pacing_matches_original_250ms_timer() -> None:
    """Step-to-step transitions must match the original's ~500 ms cadence (~440 ms
    post-ACK quiet time), however quickly the gun ACKs arrive."""
    fake = FakeHIDDevice()
    hw = _transport(fake)
    progress: list[tuple[int, int]] = []
    assert hw.upload_mode_bytecode("FreeFrag", [0x02], progress_cb=lambda c, t: progress.append((c, t))) is True
    # FreeFrag: unlock, erase, prepare, 3 chunks, lock = 7 steps / 7 sends.
    assert len(fake.write_times) == 7
    assert progress == [(i, 7) for i in range(1, 8)]
    step_gaps = [b - a for a, b in zip(fake.write_times, fake.write_times[1:])]
    assert all(g >= 0.48 for g in step_gaps), step_gaps
    post_ack_gaps = [send_t - ack_t for ack_t, send_t in zip(fake.ack_times, fake.write_times[1:])]
    assert all(g >= 0.42 for g in post_ack_gaps), post_ack_gaps


def test_upload_same_step_retry_fires_at_250ms() -> None:
    """An unacknowledged step retries at ~250 ms, then waits >=440 ms post-ACK before the next step."""
    # Drop write #2 (0x81 erase first attempt), ACK its retry (write #3).
    fake = FakeHIDDevice(drop_writes={2})
    hw = _transport(fake)
    hw.UPLOAD_INIT_DELAY_S = 0.0
    hw.UPLOAD_POST_LOCK_DELAY_S = 0.0
    assert hw.upload_mode_bytecode("FreeFrag", [0x02]) is True
    assert len(fake.write_times) == 8
    retry_gap = fake.write_times[2] - fake.write_times[1]
    assert 0.23 <= retry_gap < 0.35, f"expected ~250 ms retry gap, got {retry_gap:.3f}s"
    next_step_post_ack = fake.write_times[3] - fake.ack_times[1]
    assert next_step_post_ack >= 0.42, f"expected >=440 ms post-ACK gap, got {next_step_post_ack:.3f}s"


def test_upload_failure_is_reported_and_names_the_gun() -> None:
    """A gun that never ACKs must fail the upload (not report success) and be
    recorded in upload_failed_guns for the UI to name."""
    fake = FakeHIDDevice(ack=False)
    hw = _transport(fake)
    hw.UPLOAD_MAX_RETRIES = 3
    hw.UPLOAD_INIT_DELAY_S = 0.0
    assert hw.upload_mode_bytecode("FreeFrag", [0x02]) is False
    assert hw.upload_failed_guns == [0x02]


def test_sequence_payloads_must_be_64_bytes() -> None:
    hw = _transport(FakeHIDDevice())
    assert hw.run_sequence({"steps": [{"send": "00ff05", "note": "short"}]}) is False


def test_profile_locks_and_team_invalidation() -> None:
    eng = ArenaEngine()
    eng.phase = "PLAYING"
    with pytest.raises(ValueError):
        eng.update_player_profile(1, name="Nope")
    with pytest.raises(ValueError):
        eng.update_player_profile(1, team_id=2)

    eng.phase = "LOBBY"
    eng.bytecode_ready = True
    eng.update_player_profile(1, name="Renamed")
    # Name/avatar are host-side display only: no re-upload needed.
    assert eng.bytecode_ready is True
    eng.update_player_profile(1, team_id=2)
    # Team is baked into the gun's 0x92 config: changing it forces re-upload.
    assert eng.bytecode_ready is False


def test_global_settings_clamp_and_invalidate() -> None:
    eng = ArenaEngine(settings_path=None)
    eng.bytecode_ready = True
    updated = eng.update_global_settings({"beam_power": 9, "music_volume": 99})
    assert updated["beam_power"] == 5
    assert updated["music_volume"] == 8
    # Beam power is baked into the config: invalidates the upload.
    assert eng.bytecode_ready is False

    eng.bytecode_ready = True
    eng.update_global_settings({"music_volume": 3})
    # Audio volumes apply live and do not invalidate the upload.
    assert eng.bytecode_ready is True

    snap = eng.get_snapshot()
    assert snap["global_settings"]["beam_power"] == 5
    assert snap["upload_failed_guns"] == []


def test_global_settings_persists_to_disk(tmp_path) -> None:
    settings_file = tmp_path / "global_settings.json"
    eng1 = ArenaEngine(settings_path=settings_file)
    eng1.update_global_settings({
        "beam_power": 5,
        "gun_volume": 4,
        "music_volume": 1,
        "sfx_volume": 7,
        "announcer_volume": 0,
    })
    assert settings_file.exists()

    eng2 = ArenaEngine(settings_path=settings_file)
    assert eng2.global_settings == {
        "beam_power": 5,
        "gun_volume": 4,
        "music_volume": 1,
        "sfx_volume": 7,
        "announcer_volume": 0,
    }


def test_get_user_data_dir_platforms(monkeypatch, tmp_path) -> None:
    from pathlib import Path
    from battletag.modes import get_user_data_dir

    monkeypatch.setattr("battletag.modes.sys.platform", "darwin")
    assert get_user_data_dir() == Path.home() / "Library" / "Application Support" / "BattlePew"

    monkeypatch.setattr("battletag.modes.sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert get_user_data_dir() == tmp_path / "Roaming" / "BattlePew"

    monkeypatch.setattr("battletag.modes.sys.platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert get_user_data_dir() == tmp_path / "xdg" / "BattlePew"


def test_impatient_rate_limit_and_global_cooldown() -> None:
    eng = ArenaEngine(settings_path=None)
    # Allowed in LOBBY and WAIT_TRIGGER, blocked in PLAYING
    eng.phase = "PLAYING"
    blocked = eng.trigger_impatient(1, now=100.0)
    assert blocked["ok"] is False
    assert blocked["reason"] == "invalid_phase"

    eng.phase = "LOBBY"
    # Global cooldown blocks rapid consecutive taps across players
    ok1 = eng.trigger_impatient(1, now=100.0)
    assert ok1["ok"] is True
    assert ok1["remaining"] == 9
    gc = eng.trigger_impatient(2, now=101.0)
    assert gc["ok"] is False
    assert gc["reason"] == "global_cooldown"

    # Wait past global cooldown: P1 can tap 9 more times (10 total in 120s)
    t = 103.0
    for i in range(9):
        res = eng.trigger_impatient(1, now=t)
        assert res["ok"] is True
        assert res["remaining"] == 8 - i
        t += 3.0

    # 11th tap within 120s is rate-limited
    rl = eng.trigger_impatient(1, now=t)
    assert rl["ok"] is False
    assert rl["reason"] == "rate_limit"
    assert rl["remaining"] == 0

    # Once first tap ages out (>120s from 100.0), P1 can tap again
    res_after = eng.trigger_impatient(1, now=221.0)
    assert res_after["ok"] is True


def test_hardware_sync_prefers_stored_profile_unless_explicit() -> None:
    eng = ArenaEngine()
    stored = [
        {"gun_num": 1, "gun_id": 2, "ref_hex": "0000798E02000020", "name": "finn", "avatar": "Snake"},
        {"gun_num": 2, "gun_id": 3, "ref_hex": "000073BC02000020", "name": "Aaron", "avatar": "Dog"},
    ]
    # Simulator defaults (Finn/Dragon, Player 2/Shark) must not stick over
    # the guns' stored profiles.
    eng.sync_paired_hardware_guns(stored)
    assert eng.players[2].name == "finn"
    assert eng.players[2].avatar == "Snake"
    assert eng.players[3].avatar == "Dog"

    # An explicit user choice survives the next re-pair.
    eng.update_player_profile(1, avatar="Eagle")
    eng.sync_paired_hardware_guns(stored)
    assert eng.players[2].avatar == "Eagle"
    assert eng.players[3].avatar == "Dog"


def test_catalog_defaults_match_original() -> None:
    assert MODE_CATALOG["TeamFrag"]["defaults"]["lifePoints"] == 5
    assert MODE_CATALOG["TeamFrag"]["defaults"]["assist"] == 1
    assert MODE_CATALOG["FreeForAll"]["defaults"]["assist"] == 1
    assert MODE_CATALOG["LastManStanding"]["defaults"]["assist"] == 1
    assert MODE_CATALOG["StarterFrag"]["defaults"]["ammunitions"] == 12
    assert MODE_CATALOG["StarterFrag"]["defaults"]["assist"] == 1
    assert MODE_CATALOG["TagThenShoot"]["defaults"]["numberOfBase"] == 4
    assert MODE_CATALOG["TagThenShoot"]["defaults"]["durationSeconds"] == 60
    assert MODE_CATALOG["TagThenShoot"]["defaults"]["assist"] == 1


def test_audio_cue_frontend_fields() -> None:
    cue = AudioCue(category="announcer", url="/audio/announcer/GLOBAL_13.wav", filename="GLOBAL_13.wav")
    d = cue.to_dict()
    assert d["sound_file"] == "/audio/announcer/GLOBAL_13.wav"
    assert d["channel"] == "announcer"
    assert d["volume"] == 0.9
