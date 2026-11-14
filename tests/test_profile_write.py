"""Tests for gun profile writing (UTGame.Ui.Associate write path).

Covers the PRFL/DATA flash block builder (byte-exact against the Lua
spec), the simulator's stored-profile model, the unicast 250 ms
resend-until-ACK sequence on a fake HID device (including read-back
verification through the real 0x84 decoder), and the engine's
explicit-override handling after a successful write.
"""

from __future__ import annotations

import asyncio
import io
import time

import pytest

from battletag.hardware import RealHIDTransport, SimulatorTransport
from battletag.modes import ArenaEngine
from battletag.profile_writes import ProfileWriteQueue, WriteJob, execute_profile_write
from battletag.protocol import build_profile_block


# --- Block builder -----------------------------------------------------------


def test_block_bytes_match_lua_spec_for_aaron_dog() -> None:
    """Byte-exact block for name 'Aaron', icon 'dog.tga' (N = 31)."""
    block = build_profile_block("Aaron", "Dog")
    assert block.hex().upper() == (
        "5052464C"  # PRFL
        "01"  # number
        "00000001"  # revision
        "44415441"  # DATA
        "1000"  # offset 0x0010
        "0A00"  # data length 10 LE
        "05"  # name length
        "4161726F6E"  # Aaron
        "03"  # icon stem length (dog, without .tga)
        "646F672E746761"  # dog.tga (full filename stored)
    )
    assert len(block) == 31
    # The record starts at offset 17 (0x11): exactly the 0x84 read address.
    assert block[17] == 5
    assert block[18:23] == b"Aaron"


def test_block_bytes_for_finn_snake() -> None:
    block = build_profile_block("finn", "Snake")
    assert len(block) == 17 + 2 + 4 + 9
    assert block[15:17] == bytes([11, 0])  # data length 11 LE
    assert block[17] == 4
    assert block[18:22] == b"finn"
    assert block[22] == 5  # 'snake' stem length
    assert block[23:] == b"snake.tga"


def test_block_builder_validates_inputs() -> None:
    with pytest.raises(ValueError):
        build_profile_block("", "Dog")
    with pytest.raises(ValueError):
        build_profile_block("   ", "Dog")
    with pytest.raises(ValueError):
        build_profile_block("A" * 15, "Dog")
    with pytest.raises(ValueError):
        build_profile_block("Zoë", "Dog")
    with pytest.raises(ValueError):
        build_profile_block("Finn", "Unicorn")
    # Avatar matching is case-insensitive; the stored filename is lowercase.
    assert build_profile_block("Finn", "DRAGON").endswith(b"dragon.tga")


# --- Simulator stored profiles -------------------------------------------------


def test_sim_write_updates_stored_profile_only() -> None:
    sim = SimulatorTransport(num_guns=2)
    assert sim.read_gun_profile(0x02) == {"name": "Finn", "avatar": "Dragon"}
    ok, detail = sim.write_gun_profile(0x02, "Milo", "Lion")
    assert ok and detail == "verified"
    assert sim.read_gun_profile(0x02) == {"name": "Milo", "avatar": "Lion"}
    # The display name is untouched by the write itself (the engine mirrors it).
    assert sim.guns[0x02].name == "Finn"
    assert sim.guns[0x02].stored_name == "Milo"


def test_sim_write_rejects_bad_input_and_unknown_gun() -> None:
    sim = SimulatorTransport(num_guns=2)
    ok, _ = sim.write_gun_profile(0x02, "", "Dog")
    assert not ok
    ok, _ = sim.write_gun_profile(0x02, "Finn", "Unicorn")
    assert not ok
    ok, _ = sim.write_gun_profile(0x09, "Finn", "Dog")
    assert not ok
    assert sim.read_gun_profile(0x09) is None


# --- Real transport sequence (fake HID) ----------------------------------------


class ProfileFakeHIDDevice:
    """Fake gun: echoes flash commands, and answers 0x84 reads from the
    profile block it last received in an 0x83 chunk (a real round-trip
    through the block format and the 0x84 decoder)."""

    def __init__(self, gun_id: int = 0x03, ack: bool = True) -> None:
        self.gun_id = gun_id
        self.ack = ack
        self.queue: list[bytes] = []
        self.writes: list[bytes] = []
        self.write_times: list[float] = []
        self.stored_block: bytes | None = None
        # When set, 0x84 replies report this block instead of what the
        # last chunk wrote (lets tests force a verification mismatch).
        self.reply_block: bytes | None = None

    def set_nonblocking(self, _flag: bool) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.write_times.append(time.monotonic())
        self.writes.append(bytes(data))
        if not self.ack:
            return
        payload = bytes(data[1:])
        cmd = payload[2]
        if cmd == 0x83:
            length = payload[5]
            self.stored_block = bytes(payload[6 : 6 + length])
        if cmd == 0x84:
            # Echo first (the real gun echoes the request), then the reply.
            echo = bytearray(64)
            echo[0] = 0x05
            echo[1] = self.gun_id
            echo[2] = 0x84
            echo[6] = 0x11
            echo[7] = 0x30
            self.queue.append(bytes(echo))
            reply = bytearray(64)
            reply[0] = 0x35
            reply[1] = self.gun_id
            reply[2] = 0x84
            reply[6] = 0x11
            reply[7] = 0x30
            if self.stored_block is not None:
                record = (self.reply_block or self.stored_block)[17:]
                end = 8 + len(record)
                reply[8:end] = record
                if end < len(reply):
                    reply[end] = 0x59
                for idx in range(end + 1, len(reply)):
                    reply[idx] = 0xFF
            self.queue.append(bytes(reply))
            return
        reply = bytearray(64)
        reply[0] = 0x05
        reply[1] = self.gun_id
        reply[2] = cmd
        self.queue.append(bytes(reply))

    def read(self, _size: int, timeout_ms: int = 100) -> bytes:
        if self.queue:
            return self.queue.pop(0)
        return b""


def _profile_transport(fake: ProfileFakeHIDDevice) -> RealHIDTransport:
    hw = RealHIDTransport(confirm_write=True)
    hw._log_file = io.StringIO()
    hw.dev = fake
    hw.initialized = True
    return hw


def test_write_sequence_bytes_match_lua() -> None:
    fake = ProfileFakeHIDDevice(gun_id=0x03)
    hw = _profile_transport(fake)
    hw.paired_by_ref["REF03"] = {
        "gun_num": 2,
        "gun_id": 0x03,
        "ref_hex": "REF03",
        "name": "OldName",
        "avatar": "Bull",
    }
    ok, detail = hw.write_gun_profile(0x03, "Aaron", "Dog")
    assert ok, detail
    assert hw.paired_by_ref["REF03"]["name"] == "Aaron"
    assert hw.paired_by_ref["REF03"]["avatar"] == "Dog"
    # 5 flash steps + 1 verification read request.
    assert len(fake.writes) == 6
    payloads = [bytes(w[1:]) for w in fake.writes]
    assert payloads[0][:4] == bytes([0x01, 0x03, 0x86, 0x00])  # unlock
    assert payloads[1][:7] == bytes([0x04, 0x03, 0x81, 0, 0, 0, 0])  # erase @0
    assert payloads[2][:9] == bytes([0x06, 0x03, 0x82, 0, 0, 0, 0, 0, 31])  # init
    assert payloads[3][0] == 3 + 31
    assert payloads[3][1:6] == bytes([0x03, 0x83, 0x00, 0x00, 31])
    assert payloads[3][6 : 6 + 31] == build_profile_block("Aaron", "Dog")
    assert payloads[4][:4] == bytes([0x01, 0x03, 0x86, 0x01])  # lock
    assert payloads[5][:8] == bytes([0x05, 0x03, 0x84, 0, 0, 0, 0x11, 0x30])
    # The 250 ms pacing discipline applies to the flash steps too.
    gaps = [b - a for a, b in zip(fake.write_times, fake.write_times[1:5])]
    assert all(g >= 0.24 for g in gaps), gaps


def test_write_fails_loudly_without_acks_and_leaves_upload_state_clean() -> None:
    fake = ProfileFakeHIDDevice(gun_id=0x03, ack=False)
    hw = _profile_transport(fake)
    ok, detail = hw.write_gun_profile(0x03, "Aaron", "Dog")
    assert not ok
    assert "unlock" in detail
    assert hw.upload_failed_guns == []


def test_write_requires_permission() -> None:
    hw = RealHIDTransport(confirm_write=False)
    hw._log_file = io.StringIO()
    with pytest.raises(PermissionError):
        hw.write_gun_profile(0x03, "Aaron", "Dog")


def test_verification_mismatch_fails() -> None:
    """A gun that reports a different profile than requested must fail."""
    fake = ProfileFakeHIDDevice(gun_id=0x03)
    fake.reply_block = build_profile_block("Stale", "Cat")
    hw = _profile_transport(fake)
    ok, detail = hw.write_gun_profile(0x03, "Aaron", "Dog")
    assert not ok
    assert "mismatch" in detail
    assert "Stale" in detail


# --- Engine explicit-override handling -----------------------------------------


def test_mark_profile_stored_drops_explicit_override() -> None:
    engine = ArenaEngine()
    engine.sim.setup_number_of_guns(2)
    engine.sync_paired_hardware_guns(
        [
            {"gun_num": 1, "gun_id": 2, "ref_hex": "aa", "name": "Finn", "avatar": "Dragon"},
        ]
    )
    engine.update_player_profile(gun_num=1, name="Milo")
    assert 1 in engine._explicit_profiles
    # A re-pair with the gun's old stored name must NOT clobber the override.
    engine.sync_paired_hardware_guns(
        [{"gun_num": 1, "gun_id": 2, "ref_hex": "aa", "name": "Finn", "avatar": "Dragon"}]
    )
    assert engine.players[2].name == "Milo"
    # After a successful gun write, the override drops: the gun itself
    # holds the new profile, so re-pairing reads it back.
    engine.mark_profile_stored(1)
    assert 1 not in engine._explicit_profiles
    engine.sync_paired_hardware_guns(
        [{"gun_num": 1, "gun_id": 2, "ref_hex": "aa", "name": "Milo", "avatar": "Dragon"}]
    )
    assert engine.players[2].name == "Milo"


# --- Pairing-page gate ---------------------------------------------------------


def test_profiles_editable_gate() -> None:
    """Names/avatars editable only in setup phases AND on wizard step 1."""
    engine = ArenaEngine()
    assert engine.profiles_editable()  # LOBBY, step 1
    engine.set_wizard_step(2)
    assert not engine.profiles_editable()
    assert "pairing screen" in engine.profiles_locked_reason()
    engine.set_wizard_step(1)
    engine.phase = "PLAYING"
    assert not engine.profiles_editable()
    assert "match is running" in engine.profiles_locked_reason()
    engine.phase = "GAME_OVER"
    assert engine.profiles_editable()
    assert engine.set_wizard_step(9) == 2  # clamped
    assert engine.set_wizard_step(0) == 1


# --- Write queue ---------------------------------------------------------------


def test_queue_coalesces_pending_jobs_for_same_gun() -> None:
    queue = ProfileWriteQueue()
    queue.submit(WriteJob(gun_num=1, name="Milo", avatar="Lion"))
    queue.submit(WriteJob(gun_num=2, name="Rae", avatar="Eagle"))
    queue.submit(WriteJob(gun_num=1, name="Milo2", avatar="Bear"))
    pending = queue.pending_jobs()
    assert len(pending) == 2
    assert [j.gun_num for j in pending] == [2, 1]  # newer save for gun 1 wins
    assert pending[1].name == "Milo2"
    assert len(queue) == 2


def test_queue_take_returns_fifo_and_waits() -> None:
    async def drain() -> list[WriteJob]:
        queue = ProfileWriteQueue()
        queue.submit(WriteJob(gun_num=1, name="A", avatar="Dog"))
        queue.submit(WriteJob(gun_num=2, name="B", avatar="Cat"))
        return [await queue.take(), await queue.take()]

    jobs = asyncio.run(drain())
    assert [j.gun_num for j in jobs] == [1, 2]


# --- Queued job execution (simulator) ------------------------------------------


def _paired_engine() -> ArenaEngine:
    engine = ArenaEngine()
    engine.sim.setup_number_of_guns(2)
    engine.sync_paired_hardware_guns(
        [{"gun_num": 1, "gun_id": 2, "ref_hex": "aa", "name": "Finn", "avatar": "Dragon"}]
    )
    return engine


def test_execute_write_updates_display_stored_and_drops_override() -> None:
    engine = _paired_engine()
    engine.update_player_profile(gun_num=1, name="Milo")
    assert 1 in engine._explicit_profiles
    ok, detail = execute_profile_write(
        engine, WriteJob(gun_num=1, name="Milo", avatar="Lion")
    )
    assert ok and detail == "verified"
    assert engine.players[2].name == "Milo"
    assert engine.players[2].avatar == "Lion"
    assert engine.sim.read_gun_profile(2) == {"name": "Milo", "avatar": "Lion"}
    assert 1 not in engine._explicit_profiles
    assert engine.profile_write_status[1]["state"] == "saved"


def test_execute_write_rejected_on_step_2() -> None:
    engine = _paired_engine()
    engine.set_wizard_step(2)
    ok, detail = execute_profile_write(
        engine, WriteJob(gun_num=1, name="Milo", avatar="Lion")
    )
    assert not ok
    assert "pairing screen" in detail
    assert engine.sim.read_gun_profile(2)["name"] == "Finn"
    assert engine.profile_write_status[1]["state"] == "failed"
    assert engine.players[2].name == "Finn"  # display untouched


def test_execute_write_rejected_when_player_gone() -> None:
    engine = _paired_engine()
    ok, detail = execute_profile_write(
        engine, WriteJob(gun_num=9, name="Ghost", avatar="Cat")
    )
    assert not ok
    assert "no longer paired" in detail
    assert engine.profile_write_status[9]["state"] == "failed"
