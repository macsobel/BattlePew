"""Tests for active vest checking: the C1 sweep (hardware) and the
miss-counting fold into player state (engine).

Vest state must stay live: the lobby sweeps every few seconds, the match
sweeps every 5 s, and a sweep right after pairing establishes the real
state instead of the default "connected". One dropped reply must never
flap the display, so silence only counts after two consecutive sweeps.
"""

from __future__ import annotations

import io
import time

from battletag.hardware import RealHIDTransport
from battletag.modes import ArenaEngine


class VestFakeHIDDevice:
    """hid.device stand-in that answers the C1 vest poll per gun.

    `replies` maps gun id -> vest connected. An echo of the broadcast
    poll itself (arg length 0) is queued first: it must NOT count as a
    vest confirmation.
    """

    def __init__(self, replies: dict[int, bool]) -> None:
        self.replies = replies
        self.queue: list[bytes] = []

    def set_nonblocking(self, _flag: bool) -> None:
        pass

    def write(self, data: bytes) -> None:
        payload = bytes(data[1:])  # strip the 0x00 report ID
        if len(payload) >= 3 and payload[2] == 0xC1:
            for gid, connected in self.replies.items():
                echo = bytearray(64)
                echo[0] = 0x00
                echo[1] = gid
                echo[2] = 0xC1
                self.queue.append(bytes(echo))
                reply = bytearray(64)
                reply[0] = 0x01
                reply[1] = gid
                reply[2] = 0xC1
                reply[3] = 0x01 if connected else 0x00
                self.queue.append(bytes(reply))

    def read(self, _size: int, timeout_ms: int = 100) -> bytes:
        if self.queue:
            return self.queue.pop(0)
        return b""


def _transport(fake: VestFakeHIDDevice) -> RealHIDTransport:
    hw = RealHIDTransport(confirm_write=True)
    hw._log_file = io.StringIO()  # keep test logs out of logs/
    hw.dev = fake
    hw.initialized = True
    return hw


def test_vest_check_records_replies_and_ignores_echoes() -> None:
    hw = _transport(VestFakeHIDDevice({0x02: True, 0x03: False}))
    assert hw.vest_check() == 1
    assert hw.vest_states == {0x02: True, 0x03: False}


def test_vest_check_exits_early_when_all_expected_guns_answer() -> None:
    hw = _transport(VestFakeHIDDevice({0x02: True, 0x03: True}))
    start = time.monotonic()
    assert hw.vest_check(expect_gids={0x02, 0x03}) == 2
    # Full collect window is 1.2 s; with every gun answering, the sweep
    # must finish well inside it so match-loop sweeps stay cheap.
    assert time.monotonic() - start < 0.9


def test_vest_check_waits_full_window_for_silent_guns() -> None:
    hw = _transport(VestFakeHIDDevice({}))
    start = time.monotonic()
    assert hw.vest_check(expect_gids={0x02}) == 0
    assert time.monotonic() - start >= 1.0
    assert hw.vest_states == {}


def _engine_gids() -> tuple[ArenaEngine, list[int]]:
    eng = ArenaEngine()
    gids = sorted(eng.players)
    assert len(gids) == 2
    assert all(eng.players[g].vest_ok for g in gids)
    return eng, gids


def test_apply_vest_states_reports_replies_immediately() -> None:
    eng, gids = _engine_gids()
    gid, other = gids
    # The other gun answers every sweep so its silence never counts.
    changed = eng.apply_vest_states({gid: False, other: True})
    assert [(p.gun_id, ok) for p, ok in changed] == [(gid, False)]
    assert eng.players[gid].vest_ok is False
    changed = eng.apply_vest_states({gid: True, other: True})
    assert [(p.gun_id, ok) for p, ok in changed] == [(gid, True)]
    assert eng.players[gid].vest_ok is True


def test_apply_vest_states_tolerates_one_silent_sweep() -> None:
    eng, gids = _engine_gids()
    # First silent sweep: still shown connected, no change reported.
    assert eng.apply_vest_states({}) == []
    assert all(eng.players[g].vest_ok for g in gids)
    # Second consecutive silent sweep: both flip to vest-less.
    changed = eng.apply_vest_states({})
    assert sorted(p.gun_id for p, _ in changed) == gids
    assert all(not eng.players[g].vest_ok for g in gids)
    # Further silence reports no new change.
    assert eng.apply_vest_states({}) == []
    # A reply restores the state immediately.
    changed = eng.apply_vest_states({g: True for g in gids})
    assert sorted(p.gun_id for p, _ in changed) == gids
    assert all(eng.players[g].vest_ok for g in gids)


def test_apply_vest_states_ignores_unknown_guns() -> None:
    eng, gids = _engine_gids()
    assert eng.apply_vest_states({0x09: False}) == []
    assert all(eng.players[g].vest_ok for g in gids)

def test_remove_silent_lobby_players_balances_drops_and_poweroff() -> None:
    eng, gids = _engine_gids()
    gid_a, gid_b = gids
    from battletag.modes import LOBBY_SILENT_SWEEPS_TO_REMOVE as N
    # Silent sweeps below the threshold (gid_b keeps answering): tolerated,
    # but the gun is flagged offline after the vest threshold.
    for _ in range(N - 1):
        eng.apply_vest_states({gid_b: True})
        assert eng.remove_silent_lobby_players() == []
        assert gid_a in eng.players
    assert eng.players[gid_a].gun_online is False
    # Final silent sweep for gid_a: removed in LOBBY.
    eng.apply_vest_states({gid_b: True})
    removed = eng.remove_silent_lobby_players()
    assert [p.gun_id for p in removed] == [gid_a]
    assert gid_a not in eng.players
    assert gid_b in eng.players
    # Outside LOBBY, silent players are never removed.
    eng.phase = "PLAYING"
    for _ in range(5):
        eng.apply_vest_states({})
        assert eng.remove_silent_lobby_players() == []
        assert gid_b in eng.players


def test_apply_vest_states_is_lenient_during_match() -> None:
    eng, gids = _engine_gids()
    gid_a, gid_b = gids
    from battletag.modes import VEST_MATCH_MISS_SWEEPS_TO_DISCONNECT as M

    eng.phase = "PLAYING"
    # Transient False (C1 00) and silent sweeps below M do not warn mid-game.
    for i in range(M - 1):
        states = {gid_a: False, gid_b: True} if i % 2 == 0 else {gid_b: True}
        assert eng.apply_vest_states(states) == []
        assert eng.players[gid_a].vest_ok is True

    # A single True reply resets the streak immediately.
    assert eng.apply_vest_states({gid_a: True, gid_b: True}) == []
    assert eng.players[gid_a].vest_ok is True

    # M consecutive misses during PLAYING eventually flag vest_ok = False.
    for _ in range(M - 1):
        assert eng.apply_vest_states({gid_a: False, gid_b: True}) == []
    changed = eng.apply_vest_states({gid_a: False, gid_b: True})
    assert [(p.gun_id, ok) for p, ok in changed] == [(gid_a, False)]
    assert eng.players[gid_a].vest_ok is False
