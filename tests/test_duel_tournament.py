"""Wild West Duel 2–8 player round-robin tournament tests."""

from __future__ import annotations

import asyncio
import time

import pytest

from battletag.modes import MODE_CATALOG, ArenaEngine
from battletag.modes.duel import _build_round_robin_matches
from battletag.protocol import DecodedStatus


def _start_tournament(eng: ArenaEngine, guns: int = 3, rounds: int = 1) -> None:
    eng.set_num_simulated_guns(guns)
    eng.select_mode("OldFashionDuel", {"rounds": rounds})
    eng.reset_stats_for_new_match()
    eng.phase = "PLAYING"
    eng.match_start_ts = time.time()
    eng.audio.active_until = 0.0
    eng.audio.active_priority = 99
    eng.audio.recent_cues.clear()


def _advance_to_wait(eng: ArenaEngine) -> None:
    """Advance tournament duel from MATCH_INTRO -> WAIT_TRIGGER -> COUNTDOWN -> WAIT."""
    ds = eng.duel_state
    if ds["subphase"] == "MATCH_INTRO":
        eng.tick_mode_timers()
        ds["next_transition_at"] = time.time() - 1.0
        eng.tick_mode_timers()
    assert ds["subphase"] == "WAIT_TRIGGER"
    eng._duel_round_triggered = True
    eng.tick_mode_timers()
    assert ds["subphase"] == "COUNTDOWN"
    assert ds["countdown_sent"] is False
    ds["countdown_ends_at"] = time.time() - 1.0
    eng.tick_mode_timers()
    assert ds["subphase"] == "WAIT"
    for gid in ds["active_pair"]:
        st = eng.sim.poll_gun(gid, eng.mode_name)
        if st is not None:
            eng.ingest_status_report(st)


def _advance_to_draw(eng: ArenaEngine) -> None:
    ds = eng.duel_state
    ds["next_transition_at"] = time.time() - 1.0
    eng.tick_mode_timers()
    assert ds["subphase"] == "DRAW"
    eng.tick_pending_audio(now=time.time() + 1.0)
    assert ds["can_shoot"] is True


def _poll_active_pair(eng: ArenaEngine) -> None:
    for gid in eng.duel_state["active_pair"]:
        st = eng.sim.poll_gun(gid, eng.mode_name)
        if st is not None:
            eng.ingest_status_report(st)


def test_duel_metadata_allows_2_to_8_players_and_1_round() -> None:
    meta = MODE_CATALOG["OldFashionDuel"]
    assert meta["min_players"] == 2
    assert meta["max_players"] == 8
    round_opt = next(o for o in meta["options"] if o["key"] == "rounds")
    assert round_opt["choices"] == [1, 3, 5, 7]


def test_round_robin_scheduler_all_pairs_and_interleaving() -> None:
    for n in range(2, 9):
        gids = list(range(2, 2 + n))
        matches = _build_round_robin_matches(gids)
        assert len(matches) == n * (n - 1) // 2
        unordered = {frozenset(m) for m in matches}
        assert len(unordered) == len(matches)
        assert all(len(pair) == 2 for pair in matches)
        if n >= 5:
            for prev_m, next_m in zip(matches, matches[1:]):
                assert set(prev_m).isdisjoint(next_m), f"Overlap in N={n}: {prev_m} -> {next_m}"


def test_two_player_duel_keeps_single_match_flow() -> None:
    eng = ArenaEngine()
    _start_tournament(eng, guns=2, rounds=1)
    assert eng.duel_state["is_tournament"] is False
    assert eng.duel_state["subphase"] == "INTRO"
    assert eng.duel_state["total_matches"] == 1


def test_three_player_tournament_end_to_end() -> None:
    eng = ArenaEngine()
    _start_tournament(eng, guns=3, rounds=1)
    ds = eng.duel_state
    assert ds["is_tournament"] is True
    assert ds["total_matches"] == 3
    assert ds["subphase"] == "MATCH_INTRO"

    file_calls: list[tuple[str, str]] = []
    orig_play = eng.audio.play_announcer_file

    def spy_play(wav_name, subtitle, priority=0, duration=2.0):
        file_calls.append((wav_name, subtitle))
        return orig_play(wav_name, subtitle, priority=priority, duration=duration)

    eng.audio.play_announcer_file = spy_play

    # Pairing 1: intro emits player callouts + duel_setup, waits for trigger
    pair1_files = [
        f"DLG_GM_GLOBAL_{54 + eng.players[g].gun_num:02d}.wav"
        for g in ds["active_pair"]
    ]
    eng.tick_mode_timers()
    eng.tick_pending_audio(now=time.time() + 10.0)
    for expected_wav in pair1_files:
        assert any(f == expected_wav for f, _ in file_calls)

    ds["next_transition_at"] = time.time() - 1.0
    eng.tick_mode_timers()
    assert ds["subphase"] == "WAIT_TRIGGER"
    assert eng._mode.disconnect_players(eng) == []

    # Trigger pull -> COUNTDOWN -> WAIT -> DRAW -> hit -> ROUND_END -> MATCH_END
    _advance_to_wait(eng)
    pa_gid, pb_gid = ds["active_pair"]
    assert [p.gun_id for p in eng._mode.disconnect_players(eng)] == [pa_gid, pb_gid]

    _advance_to_draw(eng)
    eng.sim.sim_fire_shot(pa_gid, pb_gid)
    _poll_active_pair(eng)
    assert ds["subphase"] == "ROUND_END"
    assert eng.players[pa_gid].score == 1

    ds["next_transition_at"] = time.time() - 1.0
    eng.tick_mode_timers()
    assert ds["subphase"] == "MATCH_END"
    assert ds["pairing_gameover_pending"] == {pa_gid: True, pb_gid: False}
    assert len(ds["completed_matches"]) == 1
    assert eng._mode.disconnect_players(eng) == []

    # Advance to Pairing 2 -> MATCH_INTRO emits GLOBAL_113 "Next game!"
    file_calls.clear()
    ds["next_transition_at"] = time.time() - 1.0
    eng.tick_mode_timers()
    assert ds["match_index"] == 1
    assert ds["subphase"] == "MATCH_INTRO"
    eng.tick_mode_timers()
    assert any(f == "DLG_GM_GLOBAL_113.wav" for f, _ in file_calls)

    # Complete Pairing 2 & Pairing 3
    for idx in (1, 2):
        _advance_to_wait(eng)
        a_gid, b_gid = ds["active_pair"]
        _advance_to_draw(eng)
        eng.sim.sim_fire_shot(a_gid, b_gid)
        _poll_active_pair(eng)
        assert ds["subphase"] == "ROUND_END"
        ds["next_transition_at"] = time.time() - 1.0
        eng.tick_mode_timers()
        if idx == 1:
            assert ds["subphase"] == "MATCH_END"
            assert ds["pairing_gameover_pending"] == {a_gid: True, b_gid: False}
            ds["next_transition_at"] = time.time() - 1.0
            eng.tick_mode_timers()
        else:
            assert ds["subphase"] == "COMPLETE"
            assert ds["pairing_gameover_pending"] is None
            assert eng.check_auto_end_conditions() is True


def test_spectator_isolation_early_fire_and_ammo_draw() -> None:
    eng = ArenaEngine()
    _start_tournament(eng, guns=4, rounds=1)
    _advance_to_wait(eng)
    pa_gid, pb_gid = eng.duel_state["active_pair"]
    spec_gid = next(g for g in eng.players if g not in (pa_gid, pb_gid))

    # Spectator reports must be ignored and never resolve rounds or leak points
    spec_status = DecodedStatus(
        gun_id=spec_gid,
        word_count=5,
        send_words=[2, 3],
        hit_ids=[pa_gid, pb_gid, 0, 0, 0],
        hits_received=3,
        ammo=2,
    )
    eng.ingest_status_report(spec_status)
    eng.ingest_status_report(
        DecodedStatus(
            gun_id=spec_gid,
            word_count=5,
            send_words=[1, 4],
            hit_ids=[pa_gid, pb_gid, 0, 0, 0],
            hits_received=4,
            ammo=1,
        )
    )
    assert eng.duel_state["subphase"] == "WAIT"
    assert eng.players[pa_gid].score == 0
    assert eng.players[pb_gid].score == 0

    # Early fire by active Player A credits ONLY active opponent Player B
    eng.sim.sim_fire_shot(pa_gid, None)
    _poll_active_pair(eng)
    assert eng.duel_state["subphase"] == "ROUND_END"
    assert eng.players[pb_gid].score == 1
    assert eng.players[spec_gid].score == 0

    # Advance to Pairing 2 and verify both-out-of-ammo checks active_pair only
    eng.duel_state["next_transition_at"] = time.time() - 1.0
    eng.tick_mode_timers()
    eng.duel_state["next_transition_at"] = time.time() - 1.0
    eng.tick_mode_timers()
    _advance_to_wait(eng)
    a2_gid, b2_gid = eng.duel_state["active_pair"]
    _advance_to_draw(eng)
    for _ in range(6):
        eng.sim.sim_fire_shot(a2_gid, None)
        eng.sim.sim_fire_shot(b2_gid, None)
    _poll_active_pair(eng)
    assert eng.duel_state["subphase"] == "ROUND_END"
    assert eng.duel_state["last_winner_gun_num"] is None


def test_skip_duel_pairing_and_followup_guard() -> None:
    eng = ArenaEngine()
    _start_tournament(eng, guns=3, rounds=1)
    eng.tick_mode_timers()  # schedules match 0 followups
    assert any(e.get("duel_match") == 0 for e in eng._pending_audio)

    ok, err = eng.skip_duel_pairing()
    assert ok is True and err == ""
    assert eng.duel_state["match_index"] == 1
    assert eng.duel_state["pairing_gameover_pending"] is None
    # Match 0 followups are rejected once match_index moved to 1
    assert not eng._mode.followup_still_live(eng, {"duel_match": 0})
    eng.tick_pending_audio(now=time.time() + 10.0)


def test_duel_api_trigger_and_skip_endpoints(monkeypatch) -> None:
    import battletag.app as app_mod
    from fastapi import HTTPException

    eng = ArenaEngine()
    _start_tournament(eng, guns=3, rounds=1)

    async def noop_broadcast() -> None:
        return None

    monkeypatch.setattr(app_mod, "engine", eng)
    monkeypatch.setattr(app_mod, "broadcast_state", noop_broadcast)

    eng.tick_mode_timers()
    eng.duel_state["next_transition_at"] = time.time() - 1.0
    eng.tick_mode_timers()
    assert eng.duel_state["subphase"] == "WAIT_TRIGGER"

    res = asyncio.run(app_mod.api_manual_launch_trigger())
    assert res.status_code == 200
    assert eng._duel_round_triggered is True

    res_skip = asyncio.run(app_mod.api_duel_skip())
    assert res_skip.status_code == 200
    assert eng.duel_state["match_index"] == 1

    eng.phase = "LOBBY"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(app_mod.api_duel_skip())
    assert exc.value.status_code == 400
