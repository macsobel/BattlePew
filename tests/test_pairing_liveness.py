"""Tests for pairing/unpairing improvements: per-gun liveness, host
Remove with ignore-list, mid-match disconnect prompt, per-gun config
retry/drop, and ref->number memory across re-pairing.

Follows the original's model (PAIRING_UNPAIRING_REVIEW.md): one gun
leaving never disturbs the others, removal is silence-timeout only,
numbers stay stable, and there is no wire "unpair" message.
"""

from __future__ import annotations

import asyncio
import io
import time

import pytest
from fastapi import HTTPException

from battletag.hardware import RealHIDTransport
from battletag.modes import (
    LOBBY_QUIET_TIMEOUT_S,
    ArenaEngine,
    PlayerState,
)
from battletag.protocol import DecodedStatus

REF_A = "0000798E02000020"
REF_B = "000073BC02000020"


def _engine_with_players(specs: list[tuple[int, int, str, float]]) -> ArenaEngine:
    """ArenaEngine in LOBBY with players from (gun_num, gun_id, ref, last_seen_age_s)."""
    eng = ArenaEngine()
    eng.phase = "LOBBY"
    now = time.time()
    eng.players = {
        gid: PlayerState(
            gun_num=gnum,
            gun_id=gid,
            ref_hex=ref,
            name=f"P{gnum}",
            avatar="Dragon",
            last_seen_ts=(now - age) if age >= 0 else 0.0,
        )
        for gnum, gid, ref, age in specs
    }
    return eng


# ---------------------------------------------------------------------------
# Lobby liveness: quiet-timeout removal
# ---------------------------------------------------------------------------


def test_quiet_lobby_player_drops_with_went_quiet_notice() -> None:
    eng = _engine_with_players(
        [(1, 2, REF_A, 1.0), (2, 3, REF_B, LOBBY_QUIET_TIMEOUT_S + 6.0), (3, 4, "CC", 0.5)]
    )
    now = time.time()
    removed = eng.remove_quiet_lobby_players(now=now)
    assert [p.gun_num for p in removed] == [2]
    assert sorted(p.gun_num for p in eng.players.values()) == [1, 3]
    assert eng.lobby_notices, "a went-quiet notice is required"
    assert "went quiet" in eng.lobby_notices[0]["text"]
    assert "Player 2" in eng.lobby_notices[0]["text"]


def test_quiet_removal_never_outside_lobby() -> None:
    eng = _engine_with_players([(1, 2, REF_A, 60.0)])
    eng.phase = "PLAYING"
    assert eng.remove_quiet_lobby_players(now=time.time()) == []
    assert len(eng.players) == 1
    eng.phase = "UPLOADING"
    assert eng.remove_quiet_lobby_players(now=time.time()) == []
    assert len(eng.players) == 1


def test_never_heard_player_is_not_removed() -> None:
    # last_seen 0 means "no silence measured yet" (e.g. a fresh roster
    # that has not been swept): never drop it on a timeout.
    eng = _engine_with_players([(1, 2, REF_A, -1)])
    assert eng.remove_quiet_lobby_players(now=time.time()) == []
    assert len(eng.players) == 1


def test_fresh_player_is_kept() -> None:
    eng = _engine_with_players([(1, 2, REF_A, 1.0)])
    assert eng.remove_quiet_lobby_players(now=time.time()) == []
    assert len(eng.players) == 1


def test_vest_reply_updates_last_seen() -> None:
    eng = _engine_with_players([(1, 2, REF_A, -1)])
    before = time.time()
    eng.apply_vest_states({2: True})
    player = eng.players[2]
    assert player.last_seen_ts >= before
    assert player.gun_online is True


def test_status_report_updates_last_seen() -> None:
    eng = _engine_with_players([(1, 2, REF_A, -1)])
    before = time.time()
    eng.ingest_status_report(
        DecodedStatus(gun_id=2, word_count=0, send_words=[], hit_ids=[0, 0, 0, 0, 0])
    )
    player = eng.players[2]
    assert player.last_status_ts >= before
    assert player.last_seen_ts == player.last_status_ts


def test_sync_remembers_ref_numbers_and_resets_upload_state() -> None:
    eng = ArenaEngine()
    eng.bytecode_uploaded = True
    eng.config_failed_guns = [2]
    eng.sync_paired_hardware_guns(
        [
            {"gun_num": 2, "gun_id": 3, "ref_hex": REF_B, "name": "Aaron", "avatar": "dog"},
            {"gun_num": 1, "gun_id": 2, "ref_hex": REF_A, "name": "finn", "avatar": "snake"},
        ]
    )
    assert eng.ref_number_memory == {REF_A: 1, REF_B: 2}
    assert all(p.last_seen_ts > 0 for p in eng.players.values())
    assert eng.bytecode_uploaded is False
    assert eng.config_failed_guns == []


# ---------------------------------------------------------------------------
# Host Remove
# ---------------------------------------------------------------------------


def test_remove_player_keeps_other_numbers() -> None:
    eng = _engine_with_players([(1, 2, REF_A, 0.0), (2, 3, REF_B, 0.0), (3, 4, "CC", 0.0)])
    removed = eng.remove_player(2)
    assert removed is not None and removed.gun_num == 2
    assert sorted(p.gun_num for p in eng.players.values()) == [1, 3]
    assert eng.remove_player(2) is None  # already gone
    assert any("removed by the host" in n["text"] for n in eng.lobby_notices)


def test_remove_player_rejected_outside_lobby() -> None:
    eng = _engine_with_players([(1, 2, REF_A, 0.0)])
    eng.phase = "PLAYING"
    assert eng.remove_player(1) is None
    assert len(eng.players) == 1


def test_remove_player_clears_last_config_block() -> None:
    eng = _engine_with_players([(1, 2, REF_A, 0.0), (2, 3, REF_B, 0.0)])
    eng.bytecode_uploaded = True
    eng.bytecode_ready = False
    eng.config_failed_guns = [3]
    eng.remove_player(2)
    assert eng.config_failed_guns == []
    assert eng.bytecode_ready is True  # rest of the roster is ready


def test_remove_player_keeps_pending_block_for_other_gun() -> None:
    eng = _engine_with_players([(1, 2, REF_A, 0.0), (2, 3, REF_B, 0.0)])
    eng.bytecode_uploaded = True
    eng.bytecode_ready = False
    eng.config_failed_guns = [2, 3]
    eng.remove_player(2)
    assert eng.config_failed_guns == [2]  # gid 2 (Player 1) still pending
    assert eng.bytecode_ready is False


# ---------------------------------------------------------------------------
# Mid-match disconnect prompt
# ---------------------------------------------------------------------------


def _playing_engine(mode: str, ages: dict[int, float], match_age: float = 60.0) -> ArenaEngine:
    """PLAYING engine; ages maps gun_id -> seconds since last 0x95 (-1 = never)."""
    eng = ArenaEngine()
    eng.mode_name = mode
    eng.phase = "PLAYING"
    now = time.time()
    eng.match_start_ts = now - match_age
    eng.players = {
        gid: PlayerState(
            gun_num=gid - 1,
            gun_id=gid,
            ref_hex=f"R{gid}",
            name=f"P{gid - 1}",
            avatar="Dragon",
            last_status_ts=(now - age) if age >= 0 else 0.0,
        )
        for gid, age in ages.items()
    }
    return eng


def test_disconnect_prompt_none_when_healthy() -> None:
    eng = _playing_engine("FreeFrag", {2: 0.2, 3: 0.4, 4: 0.1})
    assert eng.get_disconnect_prompt(now=time.time()) is None


def test_disconnect_prompt_last_gun_answering() -> None:
    eng = _playing_engine("FreeFrag", {2: 0.2, 3: 30.0, 4: 25.0})
    prompt = eng.get_disconnect_prompt(now=time.time())
    assert prompt is not None
    assert prompt["reason"] == "last_gun"
    assert prompt["answering_count"] == 1
    assert prompt["total_count"] == 3
    assert sorted(prompt["silent_gun_nums"]) == [2, 3]


def test_disconnect_prompt_two_answering_stays_quiet() -> None:
    eng = _playing_engine("FreeFrag", {2: 0.2, 3: 0.3, 4: 30.0})
    assert eng.get_disconnect_prompt(now=time.time()) is None


def test_disconnect_prompt_never_at_match_start() -> None:
    # Nobody has answered yet, but the match is 1 s old: polls are still
    # starting, so no prompt.
    eng = _playing_engine("FreeFrag", {2: -1, 3: -1}, match_age=1.0)
    assert eng.get_disconnect_prompt(now=time.time()) is None


def test_disconnect_prompt_never_answered_after_timeout() -> None:
    eng = _playing_engine("FreeFrag", {2: 0.2, 3: -1, 4: -1}, match_age=60.0)
    prompt = eng.get_disconnect_prompt(now=time.time())
    assert prompt is not None and prompt["reason"] == "last_gun"


def test_disconnect_prompt_duel_immediate_on_silence() -> None:
    # OldFashionDuel: either gun silent prompts at once (3 s signal
    # threshold), without waiting for the 10 s general timeout.
    eng = _playing_engine("OldFashionDuel", {2: 0.2, 3: 4.0})
    prompt = eng.get_disconnect_prompt(now=time.time())
    assert prompt is not None
    assert prompt["reason"] == "mode_critical"
    assert prompt["silent_gun_nums"] == [2]


def test_disconnect_prompt_tag_then_shoot_immediate_on_silence() -> None:
    eng = _playing_engine("TagThenShoot", {2: 0.2, 3: 4.0})
    prompt = eng.get_disconnect_prompt(now=time.time())
    assert prompt is not None and prompt["reason"] == "mode_critical"


def test_disconnect_prompt_none_outside_playing() -> None:
    eng = _playing_engine("FreeFrag", {2: 30.0, 3: 30.0})
    eng.phase = "LOBBY"
    assert eng.get_disconnect_prompt(now=time.time()) is None


def test_snapshot_exposes_pairing_fields() -> None:
    eng = ArenaEngine()
    eng.add_lobby_notice("Player 2 (BOB) went quiet and was removed from the roster.")
    eng.config_failed_guns = [2]
    snap = eng.get_snapshot()
    assert snap["disconnect_prompt"] is None
    assert snap["lobby_notices"][0]["text"].startswith("Player 2")
    assert snap["config_retry_pending"] == [1]  # default sim roster: gid 2 -> Player 1


def test_lobby_notices_capped_newest_first() -> None:
    eng = ArenaEngine()
    for i in range(7):
        eng.add_lobby_notice(f"notice {i}")
    assert len(eng.lobby_notices) == 5
    assert eng.lobby_notices[0]["text"] == "notice 6"


# ---------------------------------------------------------------------------
# Transport: ref->number memory, ignore list, per-gun unpair
# ---------------------------------------------------------------------------


def _transport(fake=None, **kwargs) -> RealHIDTransport:
    hw = RealHIDTransport(confirm_write=True, **kwargs)
    hw._log_file = io.StringIO()  # keep test logs out of logs/
    if fake is not None:
        hw.dev = fake
    hw.initialized = True
    return hw


def test_choose_player_number_prefers_memory_over_lowest_free() -> None:
    hw = _transport(number_memory={REF_B: 3})
    assert hw.choose_player_number(REF_B, {}, {1, 2}) == 3


def test_choose_player_number_skips_taken_memory() -> None:
    hw = _transport(number_memory={REF_B: 2})
    assert hw.choose_player_number(REF_B, {}, {1, 2}) == 3


def test_choose_player_number_file_map_beats_memory() -> None:
    hw = _transport(number_memory={REF_B: 3})
    ref_map = {REF_B: {"player_id": 2, "name": "Aaron"}}
    assert hw.choose_player_number(REF_B, ref_map, {1}) == 2


def test_choose_player_number_target_beats_all() -> None:
    hw = _transport(number_memory={REF_B: 3})
    ref_map = {REF_B: {"player_id": 2}}
    assert hw.choose_player_number(REF_B, ref_map, {1}, target_pid=4) == 4


def test_unpair_gun_keeps_memory_and_other_guns() -> None:
    hw = _transport(number_memory={REF_A: 1, REF_B: 2})
    hw.paired_by_ref = {
        REF_A: {"gun_num": 1, "gun_id": 2, "ref_hex": REF_A},
        REF_B: {"gun_num": 2, "gun_id": 3, "ref_hex": REF_B},
    }
    removed = hw.unpair_gun(REF_A)
    assert removed is not None and removed["gun_num"] == 1
    assert list(hw.paired_by_ref) == [REF_B]
    assert hw._ref_number_memory == {REF_A: 1, REF_B: 2}
    assert hw.unpair_gun(REF_A) is None


def test_unpair_gun_ignore_lists_ref() -> None:
    hw = _transport()
    hw.paired_by_ref = {REF_A: {"gun_num": 1, "gun_id": 2, "ref_hex": REF_A}}
    hw.unpair_gun(REF_A, ignore=True)
    assert REF_A in hw.ignored_refs
    hw.clear_ignored_refs()
    assert hw.ignored_refs == set()


def test_unpair_all_clears_ignored_but_keeps_memory() -> None:
    hw = _transport(number_memory={REF_A: 1}, ignored_refs={REF_A})
    hw.paired_by_ref = {REF_A: {"gun_num": 1, "gun_id": 2, "ref_hex": REF_A}}
    hw.unpair_all()
    assert hw.paired_by_ref == {}
    assert hw.ignored_refs == set()
    assert hw._ref_number_memory == {REF_A: 1}


def test_shared_memory_survives_transport_replacement() -> None:
    """The UbiConnect-loss path: a fresh transport sharing the engine's
    memory restores numbers for re-registering guns."""
    memory: dict[str, int] = {REF_A: 1, REF_B: 2}
    hw_old = _transport(number_memory=memory)
    hw_old._ref_number_memory[REF_A] = 1  # as pair_single_gun records it
    hw_new = _transport(number_memory=memory)
    assert hw_new.choose_player_number(REF_B, {}, {1}) == 2
    assert hw_new.choose_player_number(REF_A, {}, set()) == 1


class AnnouncementFakeHIDDevice:
    """hid.device stand-in returning one gun's 10 00 12 announcement."""

    def __init__(self, ref_hex: str, repeat: bool = False) -> None:
        self.ref_hex = ref_hex
        self.repeat = repeat
        self.writes: list[bytes] = []
        self._sent = False

    def set_nonblocking(self, _flag: bool) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def read(self, _size: int, timeout_ms: int = 100) -> bytes:
        if self._sent and not self.repeat:
            return b""
        self._sent = True
        payload = bytearray(64)
        payload[0] = 0x10
        payload[1] = 0x00
        payload[2] = 0x12
        payload[3:11] = bytes.fromhex(self.ref_hex)
        return bytes([0x00]) + bytes(payload)


def test_ignored_ref_skipped_by_auto_pair_check() -> None:
    fake = AnnouncementFakeHIDDevice(REF_A)
    hw = _transport(fake, ignored_refs={REF_A})
    pair_calls: list[tuple] = []
    hw.pair_single_gun = lambda *a, **k: pair_calls.append(a)  # type: ignore[method-assign]
    assert hw.check_for_new_guns_in_pairing(timeout_ms=50) == []
    assert pair_calls == []
    assert fake.writes == []  # not even an ack: stay silent


def test_non_ignored_ref_reaches_pairing() -> None:
    fake = AnnouncementFakeHIDDevice(REF_A)
    hw = _transport(fake)
    pair_calls: list[tuple] = []

    def _stub(ref_hex, ref_bytes, **kwargs):
        pair_calls.append((ref_hex, ref_bytes))
        return {"gun_num": 1, "gun_id": 2, "ref_hex": ref_hex}

    hw.pair_single_gun = _stub  # type: ignore[method-assign]
    assert hw.check_for_new_guns_in_pairing(timeout_ms=50) != []
    assert pair_calls and pair_calls[0][0] == REF_A


def test_init_and_pair_skips_ignored_refs() -> None:
    fake = AnnouncementFakeHIDDevice(REF_A, repeat=True)
    hw = _transport(fake, ignored_refs={REF_A})
    pair_calls: list[tuple] = []
    hw.pair_single_gun = lambda *a, **k: pair_calls.append(a)  # type: ignore[method-assign]
    assert hw.init_and_pair(expected_guns=1, pair_timeout_s=0.3) == []
    assert pair_calls == []


def test_init_and_pair_pairs_non_ignored_ref() -> None:
    fake = AnnouncementFakeHIDDevice(REF_A, repeat=True)
    hw = _transport(fake)

    def _stub(ref_hex, ref_bytes, **kwargs):
        hw.paired_by_ref[ref_hex] = {"gun_num": 1, "gun_id": 2, "ref_hex": ref_hex}
        return hw.paired_by_ref[ref_hex]

    hw.pair_single_gun = _stub  # type: ignore[method-assign]
    paired = hw.init_and_pair(expected_guns=1, pair_timeout_s=2.0)
    assert [g["gun_num"] for g in paired] == [1]


# ---------------------------------------------------------------------------
# App endpoints: Remove, config retry, vest-sweep removal wiring
# ---------------------------------------------------------------------------


class _StubHardware:
    """Minimal hw stand-in for endpoint tests (no HID handle)."""

    def __init__(self) -> None:
        self.vest_states: dict[int, bool] = {}
        self.unpair_calls: list[tuple[str, bool]] = []
        self.config_results: dict[int, bool] = {}
        self.configure_calls: list[int] = []

    def vest_check(self, expect_gids=None) -> int:
        return 0

    def unpair_gun(self, ref_hex: str, ignore: bool = False):
        self.unpair_calls.append((ref_hex, ignore))
        return None

    def configure_gun(self, gun_id, mode_name, settings, team_id=1, volume=0, beam_power=3) -> bool:
        self.configure_calls.append(gun_id)
        return self.config_results.get(gun_id, True)


def test_remove_endpoint_drops_player_and_ignores_ref(monkeypatch) -> None:
    import battletag.app as app_mod

    eng = _engine_with_players([(1, 2, REF_A, 0.0), (2, 3, REF_B, 0.0)])
    eng.use_simulator = False
    stub = _StubHardware()
    eng.hw = stub  # type: ignore[assignment]
    monkeypatch.setattr(app_mod, "engine", eng)
    resp = asyncio.run(app_mod.api_remove_player(2))
    assert resp.status_code == 200
    assert [p.gun_num for p in eng.players.values()] == [1]
    assert stub.unpair_calls == [(REF_B, True)]


def test_remove_endpoint_rejects_mid_match(monkeypatch) -> None:
    import battletag.app as app_mod

    eng = _engine_with_players([(1, 2, REF_A, 0.0)])
    eng.phase = "PLAYING"
    monkeypatch.setattr(app_mod, "engine", eng)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(app_mod.api_remove_player(1))
    assert exc.value.status_code == 400


def test_remove_endpoint_404_for_unknown_player(monkeypatch) -> None:
    import battletag.app as app_mod

    eng = _engine_with_players([(1, 2, REF_A, 0.0)])
    monkeypatch.setattr(app_mod, "engine", eng)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(app_mod.api_remove_player(7))
    assert exc.value.status_code == 404


def test_config_retry_endpoint_success(monkeypatch) -> None:
    import battletag.app as app_mod

    eng = _engine_with_players([(1, 2, REF_A, 0.0), (2, 3, REF_B, 0.0)])
    eng.use_simulator = False
    eng.hardware_write_confirmed = True
    eng.bytecode_uploaded = True
    eng.bytecode_ready = False
    eng.config_failed_guns = [3]
    stub = _StubHardware()
    stub.config_results = {3: True}
    eng.hw = stub  # type: ignore[assignment]
    monkeypatch.setattr(app_mod, "engine", eng)
    resp = asyncio.run(app_mod.api_retry_config())
    assert resp.status_code == 200
    assert stub.configure_calls == [3]
    assert eng.config_failed_guns == []
    assert eng.bytecode_ready is True


def test_config_retry_endpoint_partial_failure(monkeypatch) -> None:
    import battletag.app as app_mod

    eng = _engine_with_players([(1, 2, REF_A, 0.0), (2, 3, REF_B, 0.0)])
    eng.use_simulator = False
    eng.hardware_write_confirmed = True
    eng.bytecode_uploaded = True
    eng.bytecode_ready = False
    eng.config_failed_guns = [3]
    stub = _StubHardware()
    stub.config_results = {3: False}
    eng.hw = stub  # type: ignore[assignment]
    monkeypatch.setattr(app_mod, "engine", eng)
    resp = asyncio.run(app_mod.api_retry_config())
    assert resp.status_code == 200
    assert eng.config_failed_guns == [3]
    assert eng.bytecode_ready is False


def test_config_retry_endpoint_rejects_without_pending(monkeypatch) -> None:
    import battletag.app as app_mod

    eng = _engine_with_players([(1, 2, REF_A, 0.0)])
    eng.use_simulator = False
    eng.hardware_write_confirmed = True
    eng.hw = _StubHardware()  # type: ignore[assignment]
    monkeypatch.setattr(app_mod, "engine", eng)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(app_mod.api_retry_config())
    assert exc.value.status_code == 400


def test_config_retry_endpoint_rejects_simulator(monkeypatch) -> None:
    import battletag.app as app_mod

    eng = _engine_with_players([(1, 2, REF_A, 0.0)])
    eng.use_simulator = True
    eng.bytecode_uploaded = True
    eng.config_failed_guns = [2]
    monkeypatch.setattr(app_mod, "engine", eng)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(app_mod.api_retry_config())
    assert exc.value.status_code == 400


def test_refresh_vest_states_removes_quiet_gun_without_ignore(monkeypatch) -> None:
    import battletag.app as app_mod

    eng = _engine_with_players([(1, 2, REF_A, 0.0), (2, 3, REF_B, 30.0)])
    eng.use_simulator = False
    stub = _StubHardware()
    eng.hw = stub  # type: ignore[assignment]
    monkeypatch.setattr(app_mod, "engine", eng)
    asyncio.run(app_mod._refresh_vest_states())
    assert [p.gun_num for p in eng.players.values()] == [1]
    # Quiet-drop unbinds but does NOT ignore-list: the gun may return.
    assert stub.unpair_calls == [(REF_B, False)]


def test_refresh_vest_states_keeps_fresh_gun(monkeypatch) -> None:
    import battletag.app as app_mod

    eng = _engine_with_players([(1, 2, REF_A, 0.0)])
    eng.use_simulator = False
    stub = _StubHardware()
    stub.vest_states = {2: True}
    eng.hw = stub  # type: ignore[assignment]
    monkeypatch.setattr(app_mod, "engine", eng)
    asyncio.run(app_mod._refresh_vest_states())
    assert len(eng.players) == 1
    assert stub.unpair_calls == []
