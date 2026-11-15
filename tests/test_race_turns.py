"""Timed TagThenShoot turns: the turn machine, per-run timing, the
active-runner scoring gate, fastest-run ranking, and winner outcomes.

The mode runs sequential solo turns: each player pulls the trigger to
start their run (mandatory, one pull per turn in Manual mode), runs
one course (all T-Bases in order, tower shot after each), and the
fastest finished run wins. Points still count for unfinished runs.
Audio-file assertions are skipped when the handoff game tree is
absent; the engine/sim logic tests always run.
"""

from __future__ import annotations

import time

import pytest

from battletag.audio import ANNOUNCER_DIR, ANNOUNCER_POOLS
from battletag.modes import ArenaEngine

AUDIO = pytest.mark.skipif(not ANNOUNCER_DIR.is_dir(), reason="game audio tree not present")


def _start(eng: ArenaEngine, guns: int = 2, settings: dict | None = None) -> None:
    eng.set_num_simulated_guns(guns)
    eng.select_mode("TagThenShoot", settings)
    eng.reset_stats_for_new_match()
    eng.phase = "PLAYING"
    eng.match_start_ts = time.time()
    _poll_all(eng)
    eng.audio.active_until = 0.0
    eng.audio.active_priority = 99
    eng.audio.recent_cues.clear()


def _poll_all(eng: ArenaEngine) -> None:
    for gid in sorted(eng.players.keys()):
        st = eng.sim.poll_gun(gid, eng.mode_name)
        if st is not None:
            eng.ingest_status_report(st)


def _ingest_tower(eng: ArenaEngine) -> None:
    eng.ingest_ubiconnect_hits(eng.sim.ubiconnect_hits)


def _pass_callout(eng: ArenaEngine) -> None:
    """Advance a PLAYER_INTRO to the run's start phase (wait/countdown)."""
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    eng.tick_race_turns()
    assert eng.race_state["callout_played"] is True
    eng.race_state["callout_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()


def _pass_intro(eng: ArenaEngine) -> None:
    """Advance a ROUND_INTRO through the Player callout to the run's
    start phase (wait/countdown)."""
    assert eng.race_state["turn_phase"] == "ROUND_INTRO"
    eng.tick_race_turns()
    assert eng.race_state["intro_played"] is True
    eng.race_state["intro_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    _pass_callout(eng)


def _start_run(eng: ArenaEngine) -> None:
    """Drive the waiting turn through its trigger + countdown to RUNNING."""
    if eng.race_state["turn_phase"] == "ROUND_INTRO":
        _pass_intro(eng)
    if eng.race_state["turn_phase"] == "PLAYER_INTRO":
        _pass_callout(eng)
    assert eng.race_state["turn_phase"] == "WAIT_TRIGGER"
    eng._turn_triggered = True
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "COUNTDOWN"
    eng.race_state["countdown_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "RUNNING"


def _run_course(eng: ArenaEngine, gid: int, bases: int = 4) -> None:
    """Play one full alternating tag/tower course for `gid`."""
    for base in range(1, bases + 1):
        eng.sim.sim_scan_rfid(gid, f"RF0{base + 2}")
        _poll_all(eng)
        eng.sim.sim_fire_shot(gid, gid)
        _ingest_tower(eng)


def _finish_turn(eng: ArenaEngine) -> None:
    """Advance the TURN_END beat to the next run (or DONE)."""
    assert eng.race_state["turn_phase"] == "TURN_END"
    eng.race_state["beat_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()


def test_turn_machine_waits_for_each_players_trigger() -> None:
    eng = ArenaEngine()
    _start(eng)
    rs = eng.race_state
    assert rs["turn_order"] == [2, 3]
    assert rs["turn_index"] == 0
    assert rs["round_index"] == 1
    assert rs["max_rounds"] == 1
    assert rs["launch_mode"] == "manual"
    # The match opens on the round announcement, then the wait.
    assert rs["turn_phase"] == "ROUND_INTRO"
    assert rs["active_gun_id"] == 2
    assert rs["bases"] == 4
    assert rs["turn_cap_s"] == 60

    _pass_intro(eng)
    assert rs["turn_phase"] == "WAIT_TRIGGER"

    # No trigger, no countdown: the wait holds across ticks.
    eng.tick_race_turns()
    assert rs["turn_phase"] == "WAIT_TRIGGER"

    # The pull releases a 5 s countdown (Manual launch semantics).
    eng._turn_triggered = True
    eng.tick_race_turns()
    assert rs["turn_phase"] == "COUNTDOWN"
    assert rs["countdown_seconds"] == 5
    assert eng.countdown_remaining == 5


def test_only_the_active_runner_scores() -> None:
    eng = ArenaEngine()
    _start(eng)
    _start_run(eng)
    runner, waiter = eng.players[2], eng.players[3]

    # The waiting player's scans and tower shots are ignored for
    # scoring (their baselines still advance, so nothing leaks into
    # their own turn later).
    eng.sim.sim_scan_rfid(3, "RF03")
    _poll_all(eng)
    eng.sim.sim_fire_shot(3, 3)
    _ingest_tower(eng)
    assert (waiter.tags, waiter.tower_hits, waiter.score) == (0, 0, 0)
    assert waiter.frags == 1  # the cumulative mirror still advances

    # The runner's identical actions score.
    eng.sim.sim_scan_rfid(2, "RF03")
    _poll_all(eng)
    eng.sim.sim_fire_shot(2, 2)
    _ingest_tower(eng)
    assert (runner.tags, runner.tower_hits, runner.score) == (1, 1, 2)


def test_course_completion_records_time_and_advances() -> None:
    eng = ArenaEngine()
    _start(eng)
    _start_run(eng)
    eng.race_state["turn_started_at"] = time.time() - 23.4
    _run_course(eng, 2)

    rs = eng.race_state
    assert rs["turn_phase"] == "TURN_END"
    p = eng.players[2]
    assert len(p.run_history) == 1
    entry = p.run_history[0]
    assert entry["completed"] is True
    assert entry["time"] == pytest.approx(23.4, abs=0.5)
    assert entry["points"] == 8
    assert entry["round"] == 1
    assert p.best_run_time == pytest.approx(23.4, abs=0.5)
    assert (p.tags, p.tower_hits, p.score) == (4, 4, 8)
    assert eng.race_session_best[(2, 4)]["time"] == pytest.approx(23.4, abs=0.5)

    # The beat advances to the next player's callout.
    _finish_turn(eng)
    assert rs["turn_phase"] == "PLAYER_INTRO"
    assert rs["turn_index"] == 1
    assert rs["active_gun_id"] == 3


def test_cap_expiry_ends_the_run_unfinished() -> None:
    eng = ArenaEngine()
    _start(eng, settings={"durationSeconds": 60})
    _start_run(eng)
    eng.sim.sim_scan_rfid(2, "RF03")
    _poll_all(eng)
    eng.sim.sim_fire_shot(2, 2)
    _ingest_tower(eng)

    # The cap expires: unfinished, points kept, no time recorded.
    eng.race_state["turn_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "TURN_END"
    p = eng.players[2]
    assert len(p.run_history) == 1
    assert p.run_history[0]["completed"] is False
    assert p.run_history[0]["time"] is None
    assert p.run_history[0]["points"] == 2
    assert p.best_run_time is None
    assert p.score == 2

    # A late tower report cannot re-complete the finished turn: the
    # cumulative mirror advances, but nothing scores and the result
    # stands.
    eng.ingest_ubiconnect_hits({2: 99})
    assert eng.race_state["turn_phase"] == "TURN_END"
    assert len(p.run_history) == 1
    assert p.tower_hits == 1
    assert p.score == 2
    assert p.frags == 99


def test_last_turn_done_ends_the_match() -> None:
    eng = ArenaEngine()
    _start(eng)
    _start_run(eng)
    _run_course(eng, 2)
    _finish_turn(eng)
    _start_run(eng)
    _run_course(eng, 3)
    _finish_turn(eng)

    assert eng.race_state["turn_phase"] == "DONE"
    assert eng.race_state["active_gun_id"] is None
    assert eng.check_auto_end_conditions() is True


def test_clock_never_ends_a_race_early() -> None:
    eng = ArenaEngine()
    _start(eng)
    # Even with the match clock long expired, the race runs until
    # every turn is done: the cap belongs to each turn, not the match.
    eng.match_start_ts = time.time() - 600
    assert eng.check_auto_end_conditions() is False
    assert eng.get_remaining_seconds() == pytest.approx(60.0)
    _start_run(eng)
    assert eng.get_remaining_seconds() <= 60.0
    assert eng.get_remaining_seconds() > 59.0


def test_fastest_run_ranks_first_and_wins() -> None:
    eng = ArenaEngine()
    _start(eng, guns=3)
    # Player 1: slow finish. Player 2: fast finish. Player 3: cap out.
    _start_run(eng)
    eng.race_state["turn_started_at"] = time.time() - 40.0
    _run_course(eng, 2)
    _finish_turn(eng)
    _start_run(eng)
    eng.race_state["turn_started_at"] = time.time() - 19.5
    _run_course(eng, 3)
    _finish_turn(eng)
    _start_run(eng)
    eng.sim.sim_scan_rfid(4, "RF03")
    _poll_all(eng)
    eng.race_state["turn_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()

    ranked = eng.get_sorted_players()
    assert [p.gun_id for p in ranked] == [3, 2, 4]
    outcomes = eng.compute_gameover_outcomes()
    assert outcomes == {2: False, 3: True, 4: False}


def test_no_finish_falls_back_to_points() -> None:
    eng = ArenaEngine()
    _start(eng)
    _start_run(eng)
    eng.sim.sim_scan_rfid(2, "RF03")
    _poll_all(eng)
    eng.race_state["turn_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    _finish_turn(eng)
    _start_run(eng)
    eng.sim.sim_scan_rfid(3, "RF03")
    _poll_all(eng)
    eng.sim.sim_fire_shot(3, 3)
    _ingest_tower(eng)
    eng.race_state["turn_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()

    # Nobody finished: most points wins (player 2, with 2).
    assert [p.gun_id for p in eng.get_sorted_players()] == [3, 2]
    assert eng.compute_gameover_outcomes() == {2: False, 3: True}


def test_snapshot_exposes_race_state_and_run_times() -> None:
    eng = ArenaEngine()
    _start(eng)
    snap = eng.get_snapshot()
    assert snap["race_state"]["turn_phase"] == "ROUND_INTRO"
    assert snap["race_state"]["active_gun_id"] == 2
    assert snap["duel_state"] is None
    assert snap["players"][0]["run_history"] == []
    assert snap["players"][0]["best_run_time"] is None
    assert snap["race_roster"] == {"order": [1, 2], "bench": []}

    _start_run(eng)
    _run_course(eng, 2)
    snap = eng.get_snapshot()
    by_gid = {p["gun_id"]: p for p in snap["players"]}
    assert len(by_gid[2]["run_history"]) == 1
    assert by_gid[2]["run_history"][0]["completed"] is True
    assert by_gid[2]["best_run_time"] is not None
    assert snap["race_state"]["turn_phase"] == "TURN_END"


@AUDIO
def test_race_pools_reference_real_files_and_emit() -> None:
    from battletag.audio import GameMasterAudio

    for key, expected in (
        ("race_finish", ["DLG_GM_GLOBAL_93.wav"]),
        ("race_new_best", ["DLG_GM_GLOBAL_95.wav"]),
        ("race_over", ["DLG_GM_GLOBAL_94.wav", "DLG_GM_GLOBAL_96.wav"]),
    ):
        pool = ANNOUNCER_POOLS[key]
        assert pool["files"] == expected
        for f in expected:
            assert (ANNOUNCER_DIR / f).exists(), f
    gm = GameMasterAudio(emit_cb=lambda _cue: None)
    for key in ("race_finish", "race_new_best", "race_over"):
        cue = gm.trigger_pool(key, force=True)
        assert cue is not None, key
        assert cue.url.startswith("/audio/announcer/DLG_GM_GLOBAL_9")
