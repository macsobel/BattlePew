"""TagThenShoot rounds + auto/manual + roster: per-round history,
best-time ranking with shared wins, the race roster, auto/manual
transitions, vocal round announcements, skip-run, stop snapshots,
session-best keying, countdown-echo retry, and announcer gating.

Helpers mirror tests/test_race_turns.py; the two files share no code
so each reads standalone.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from battletag.modes import ArenaEngine, PlayerState


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
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    eng.tick_race_turns()
    eng.race_state["callout_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()


def _pass_intro(eng: ArenaEngine) -> None:
    """Advance a ROUND_INTRO through the Player callout to the run's
    start phase (wait/countdown)."""
    assert eng.race_state["turn_phase"] == "ROUND_INTRO"
    eng.tick_race_turns()
    eng.race_state["intro_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    _pass_callout(eng)


def _begin_run(eng: ArenaEngine) -> None:
    """Drive the current run to RUNNING in either launch mode."""
    if eng.race_state["turn_phase"] == "ROUND_INTRO":
        _pass_intro(eng)
    if eng.race_state["turn_phase"] == "PLAYER_INTRO":
        _pass_callout(eng)
    if eng.race_state["turn_phase"] == "WAIT_TRIGGER":
        eng._turn_triggered = True
        eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "COUNTDOWN"
    eng.race_state["countdown_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "RUNNING"


def _run_course(eng: ArenaEngine, gid: int, bases: int = 4) -> None:
    for base in range(1, bases + 1):
        eng.sim.sim_scan_rfid(gid, f"RF0{base + 2}")
        _poll_all(eng)
        eng.sim.sim_fire_shot(gid, gid)
        _ingest_tower(eng)


def _finish_turn(eng: ArenaEngine) -> None:
    assert eng.race_state["turn_phase"] == "TURN_END"
    eng.race_state["beat_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()


def _fake_elapsed(eng: ArenaEngine, seconds: float) -> None:
    eng.race_state["turn_started_at"] = time.time() - seconds


# ---------------------------------------------------------------- history


def test_history_accumulates_across_rounds_best_and_total() -> None:
    eng = ArenaEngine()
    _start(eng, guns=2, settings={"rounds": 2})
    assert eng.race_state["max_rounds"] == 2

    # Round 1: P1 finishes slow (40 s), P2 caps out with 2 points.
    _begin_run(eng)
    _fake_elapsed(eng, 40.0)
    _run_course(eng, 2)
    _finish_turn(eng)
    _begin_run(eng)
    eng.sim.sim_scan_rfid(3, "RF03")
    _poll_all(eng)
    eng.sim.sim_fire_shot(3, 3)
    _ingest_tower(eng)
    eng.race_state["turn_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    _finish_turn(eng)
    assert eng.race_state["turn_phase"] == "ROUND_INTRO"
    assert eng.race_state["round_index"] == 2

    # Round 2: P1 finishes fast (19.5 s), P2 finishes (30 s).
    _begin_run(eng)
    _fake_elapsed(eng, 19.5)
    _run_course(eng, 2)
    _finish_turn(eng)
    _begin_run(eng)
    _fake_elapsed(eng, 30.0)
    _run_course(eng, 3)
    _finish_turn(eng)
    assert eng.race_state["turn_phase"] == "DONE"
    assert eng.check_auto_end_conditions() is True

    p1, p2 = eng.players[2], eng.players[3]
    assert [r["round"] for r in p1.run_history] == [1, 2]
    assert p1.run_history[0]["time"] == pytest.approx(40.0, abs=0.5)
    assert p1.run_history[1]["time"] == pytest.approx(19.5, abs=0.5)
    assert p1.best_run_time == pytest.approx(19.5, abs=0.5)
    assert p1.best_run["points"] == 8
    # Score is the TOTAL across runs (8 + 8), not the last run.
    assert p1.score == 16
    assert p2.run_history[0]["completed"] is False
    assert p2.run_history[0]["points"] == 2
    assert p2.run_history[1]["completed"] is True
    assert p2.best_run_time == pytest.approx(30.0, abs=0.5)
    assert p2.score == 10
    # Best time decides: P1's one fast finish beats P2's finish.
    assert [p.gun_id for p in eng.race_ranked_players()] == [2, 3]
    assert eng.race_winners() == [p1]
    assert eng.compute_gameover_outcomes() == {2: True, 3: False}


def test_one_fast_finish_beats_consistent_slower_finishes() -> None:
    eng = ArenaEngine()
    _start(eng, guns=2, settings={"rounds": 2})
    # P1: one fast finish + one DNF. P2: two slower finishes.
    _begin_run(eng)
    _fake_elapsed(eng, 18.0)
    _run_course(eng, 2)
    _finish_turn(eng)
    _begin_run(eng)
    _fake_elapsed(eng, 25.0)
    _run_course(eng, 3)
    _finish_turn(eng)
    _begin_run(eng)
    eng.race_state["turn_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    _finish_turn(eng)
    _begin_run(eng)
    _fake_elapsed(eng, 26.0)
    _run_course(eng, 3)
    _finish_turn(eng)
    assert [p.gun_id for p in eng.race_ranked_players()] == [2, 3]


def test_shared_win_on_exact_best_time_tie() -> None:
    eng = ArenaEngine()
    _start(eng, guns=3, settings={"rounds": 2})
    # Craft identical best times (live runs never tie exactly).
    for gid in (2, 3):
        p = eng.players[gid]
        p.run_history = [
            {"round": 1, "time": 20.0, "completed": True, "points": 8, "tags": 4, "tower_hits": 4},
            {"round": 2, "time": 30.0, "completed": True, "points": 8, "tags": 4, "tower_hits": 4},
        ]
        p.score = 16
    eng.players[4].run_history = [
        {"round": 1, "time": 25.0, "completed": True, "points": 8, "tags": 4, "tower_hits": 4},
    ]
    eng.players[4].score = 8
    winners = eng.race_winners()
    assert [p.gun_id for p in winners] == [2, 3]
    # Shared win: BOTH tied racers get winner LEDs (no tiebreak chain).
    assert eng.compute_gameover_outcomes() == {2: True, 3: True, 4: False}
    # Both share rank 1; the slower finisher is 3rd.
    ranked = eng.race_ranked_players()
    assert [p.gun_id for p in ranked] == [2, 3, 4]


def test_no_finish_shared_win_on_points_tie() -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    for gid in (2, 3):
        eng.players[gid].run_history = [
            {"round": 1, "time": None, "completed": False, "points": 5, "tags": 3, "tower_hits": 2}
        ]
        eng.players[gid].score = 5
    assert [p.gun_id for p in eng.race_winners()] == [2, 3]
    assert eng.compute_gameover_outcomes() == {2: True, 3: True}


def test_no_finish_best_single_breaks_points_tie_for_win() -> None:
    eng = ArenaEngine()
    _start(eng, guns=2, settings={"rounds": 2})
    # Same total (6), different best single round: only the 5-point
    # single-round leader wins — winners are the place-1 set.
    eng.players[2].run_history = [
        {"round": 1, "time": None, "completed": False, "points": 3, "tags": 2, "tower_hits": 1},
        {"round": 2, "time": None, "completed": False, "points": 3, "tags": 2, "tower_hits": 1},
    ]
    eng.players[2].score = 6
    eng.players[3].run_history = [
        {"round": 1, "time": None, "completed": False, "points": 5, "tags": 3, "tower_hits": 2},
        {"round": 2, "time": None, "completed": False, "points": 1, "tags": 1, "tower_hits": 0},
    ]
    eng.players[3].score = 6
    assert [p.gun_id for p in eng.race_winners()] == [3]
    assert eng.compute_gameover_outcomes() == {2: False, 3: True}


def test_nonfinisher_ranking_points_then_best_single() -> None:
    eng = ArenaEngine()
    _start(eng, guns=3, settings={"rounds": 2})
    # All DNF: same total, different best single run.
    eng.players[2].run_history = [
        {"round": 1, "time": None, "completed": False, "points": 3, "tags": 2, "tower_hits": 1},
        {"round": 2, "time": None, "completed": False, "points": 3, "tags": 2, "tower_hits": 1},
    ]
    eng.players[2].score = 6
    eng.players[3].run_history = [
        {"round": 1, "time": None, "completed": False, "points": 5, "tags": 3, "tower_hits": 2},
        {"round": 2, "time": None, "completed": False, "points": 1, "tags": 1, "tower_hits": 0},
    ]
    eng.players[3].score = 6
    eng.players[4].run_history = [
        {"round": 1, "time": None, "completed": False, "points": 2, "tags": 1, "tower_hits": 1},
    ]
    eng.players[4].score = 2
    assert [p.gun_id for p in eng.race_ranked_players()] == [3, 2, 4]


def test_winner_announcement_names_shared_winners(monkeypatch) -> None:
    import battletag.app as app_mod

    eng = ArenaEngine()
    _start(eng, guns=2)
    for gid in (2, 3):
        eng.players[gid].run_history = [
            {"round": 1, "time": 20.0, "completed": True, "points": 8, "tags": 4, "tower_hits": 4}
        ]
        eng.players[gid].score = 8
    monkeypatch.setattr(app_mod, "engine", eng)
    calls: list[str] = []
    orig = eng.audio.play_announcer_file

    def recorder(name, subtitle, priority=0, duration=2.0):
        calls.append(name)
        return None

    monkeypatch.setattr(eng.audio, "play_announcer_file", recorder)

    async def _run() -> None:
        app_mod._trigger_winner_announcement()
        await asyncio.sleep(0.05)
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()
        await asyncio.sleep(0)

    asyncio.run(_run())
    # Shared win: the plural intro ("And the winners are...").
    assert calls and calls[0] == "DLG_GM_GLOBAL_54.wav"
    assert orig is not None


# ------------------------------------------------------------------ roster


def _lobby_race(eng: ArenaEngine, guns: int = 3) -> None:
    eng.set_num_simulated_guns(guns)
    eng.select_mode("TagThenShoot")
    assert eng.phase == "LOBBY"


def test_roster_defaults_all_in_gun_number_order() -> None:
    eng = ArenaEngine()
    _lobby_race(eng, guns=3)
    assert eng.race_roster_snapshot() == {"order": [1, 2, 3], "bench": []}


def test_roster_set_validates_partition() -> None:
    eng = ArenaEngine()
    _lobby_race(eng, guns=3)
    ok, reason = eng.set_race_roster([2, 1], [3])
    assert ok and reason == ""
    assert eng.race_roster == {"order": [2, 1], "bench": [3]}
    # Missing a player.
    ok, _ = eng.set_race_roster([1, 2], [])
    assert not ok
    # Unknown player.
    ok, _ = eng.set_race_roster([1, 2, 9], [])
    assert not ok
    # Duplicate.
    ok, _ = eng.set_race_roster([1, 1, 2], [3])
    assert not ok
    # Nobody racing.
    ok, _ = eng.set_race_roster([], [1, 2, 3])
    assert not ok
    # The valid roster survived the rejections.
    assert eng.race_roster == {"order": [2, 1], "bench": [3]}


def test_roster_rejected_outside_setup_or_other_modes() -> None:
    eng = ArenaEngine()
    _lobby_race(eng, guns=2)
    eng.phase = "PLAYING"
    ok, _ = eng.set_race_roster([2, 1], [])
    assert not ok
    ok, _ = eng.toggle_race_roster(1, False)
    assert not ok
    eng.phase = "LOBBY"
    eng.select_mode("FreeFrag")
    ok, _ = eng.set_race_roster([2, 1], [])
    assert not ok


def test_roster_toggle_appends_and_never_reorders() -> None:
    eng = ArenaEngine()
    _lobby_race(eng, guns=3)
    assert eng.set_race_roster([3, 1, 2], [])[0]
    ok, _ = eng.toggle_race_roster(1, False)
    assert ok
    assert eng.race_roster == {"order": [3, 2], "bench": [1]}
    ok, _ = eng.toggle_race_roster(1, True)
    assert ok
    # Opt-in appends to the END; 3 and 2 keep their order.
    assert eng.race_roster == {"order": [3, 2, 1], "bench": []}
    # No-op toggles are fine.
    assert eng.toggle_race_roster(1, True)[0]
    assert eng.race_roster["order"] == [3, 2, 1]


def test_roster_cannot_bench_last_racer() -> None:
    eng = ArenaEngine()
    _lobby_race(eng, guns=2)
    assert eng.toggle_race_roster(1, False)[0]
    ok, reason = eng.toggle_race_roster(2, False)
    assert not ok
    assert "At least one" in reason
    assert eng.race_roster == {"order": [2], "bench": [1]}


def test_roster_syncs_with_pairing_changes() -> None:
    eng = ArenaEngine()
    _lobby_race(eng, guns=2)
    assert eng.set_race_roster([2, 1], [])[0]
    # A new gun appends to the end, in gun-number order.
    eng.set_num_simulated_guns(3)
    assert eng.race_roster_snapshot() == {"order": [2, 1, 3], "bench": []}
    # Dropping back to 2 guns removes gun 3 from the roster.
    eng.set_num_simulated_guns(2)
    assert eng.race_roster_snapshot() == {"order": [2, 1], "bench": []}


def test_roster_freezes_at_match_start_bench_never_runs() -> None:
    eng = ArenaEngine()
    _lobby_race(eng, guns=3)
    assert eng.set_race_roster([2, 1], [3])[0]
    eng.reset_stats_for_new_match()
    eng.phase = "PLAYING"
    eng.match_start_ts = time.time()
    _poll_all(eng)
    # Frozen snapshot: gun_ids for gun_nums [2, 1]; gun 3 (gid 4) absent.
    assert eng.race_state["turn_order"] == [3, 2]
    # Drive both runs to DONE (first runner faster, so ranked first).
    _begin_run(eng)
    _fake_elapsed(eng, 20.0)
    _run_course(eng, 3)
    _finish_turn(eng)
    _begin_run(eng)
    _fake_elapsed(eng, 30.0)
    _run_course(eng, 2)
    _finish_turn(eng)
    assert eng.race_state["turn_phase"] == "DONE"
    assert eng.players[4].run_history == []
    assert eng.players[4].score == 0
    # Benched: no outcome entry, never a winner, ranked last in the
    # full player list but excluded from the racer ranking.
    assert 4 not in eng.compute_gameover_outcomes()
    assert 4 not in [p.gun_id for p in eng.race_winners()]
    assert [p.gun_id for p in eng.race_ranked_players()] == [3, 2]
    assert [p.gun_id for p in eng.get_sorted_players()] == [3, 2, 4]
    # The benched gun is still paired and still polled.
    assert eng.players[4].paired is True


def test_disconnect_prompt_ignores_benched_guns() -> None:
    eng = ArenaEngine()
    _lobby_race(eng, guns=3)
    assert eng.set_race_roster([1, 2], [3])[0]
    eng.reset_stats_for_new_match()
    eng.phase = "PLAYING"
    now = time.time()
    eng.match_start_ts = now - 30
    for gid in (2, 3):
        eng.players[gid].last_status_ts = now
    eng.players[4].last_status_ts = now - 60  # benched + silent
    assert eng.get_disconnect_prompt(now) is None
    # A silent RACER still prompts, counting racers only.
    eng.players[3].last_status_ts = now - 60
    prompt = eng.get_disconnect_prompt(now)
    assert prompt is not None
    assert prompt["silent_gun_nums"] == [2]
    assert prompt["total_count"] == 2


# -------------------------------------------------------------- auto/manual


def test_auto_countdown_10_before_every_run_no_wait() -> None:
    eng = ArenaEngine()
    _start(eng, guns=2, settings={"gameLaunch": 0, "rounds": 2})
    assert eng.race_state["launch_mode"] == "auto"
    for _ in range(4):
        _pass_intro(eng) if eng.race_state["turn_phase"] == "ROUND_INTRO" else None
        _pass_callout(eng) if eng.race_state["turn_phase"] == "PLAYER_INTRO" else None
        # Auto never waits for a trigger.
        assert eng.race_state["turn_phase"] == "COUNTDOWN"
        assert eng.race_state["countdown_seconds"] == 10
        eng.race_state["countdown_ends_at"] = time.time() - 1.0
        eng.tick_race_turns()
        assert eng.race_state["turn_phase"] == "RUNNING"
        eng.race_state["turn_ends_at"] = time.time() - 1.0
        eng.tick_race_turns()
        _finish_turn(eng)
    assert eng.race_state["turn_phase"] == "DONE"


def test_manual_countdown_5_every_round() -> None:
    eng = ArenaEngine()
    _start(eng, guns=2, settings={"gameLaunch": 1, "rounds": 2})
    for _ in range(4):
        if eng.race_state["turn_phase"] == "ROUND_INTRO":
            _pass_intro(eng)
        if eng.race_state["turn_phase"] == "PLAYER_INTRO":
            _pass_callout(eng)
        assert eng.race_state["turn_phase"] == "WAIT_TRIGGER"
        eng._turn_triggered = True
        eng.tick_race_turns()
        assert eng.race_state["countdown_seconds"] == 5
        eng.race_state["countdown_ends_at"] = time.time() - 1.0
        eng.tick_race_turns()
        assert eng.race_state["turn_phase"] == "RUNNING"
        eng.race_state["turn_ends_at"] = time.time() - 1.0
        eng.tick_race_turns()
        _finish_turn(eng)
    assert eng.race_state["turn_phase"] == "DONE"


def test_round_announcements_sequence_and_files(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=1, settings={"rounds": 3, "gameLaunch": 0})
    played: list[str] = []
    numbers: list[int] = []
    pools: list[str] = []

    def fake_file(name, subtitle, priority=0, duration=2.0):
        played.append(name)
        # Mirror the real modeled window so sequencing is testable.
        eng.audio.active_until = time.time() + duration
        eng.audio.active_priority = priority
        return object()

    def fake_number(n):
        numbers.append(n)
        return None

    def fake_pool(key, custom_subtitle=None, force=False):
        pools.append(key)
        return None

    monkeypatch.setattr(eng.audio, "play_announcer_file", fake_file)
    monkeypatch.setattr(eng.audio, "play_countdown_number", fake_number)
    monkeypatch.setattr(eng.audio, "trigger_pool", fake_pool)

    # Round 1: "First round!" plays, intro holds until its window ends.
    eng.tick_race_turns()
    assert played == ["DLG_GM_GLOBAL_115.wav"]
    assert numbers == []  # no countdown numbers during the intro
    assert eng.race_state["turn_phase"] == "ROUND_INTRO"
    eng.tick_race_turns()  # still inside the modeled window
    assert eng.race_state["turn_phase"] == "ROUND_INTRO"
    eng.race_state["intro_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    # The Player callout plays next, before any countdown number, and
    # holds the machine until its window ends (no overlap in normal
    # flow; the 0x93 send waits with the numbers in COUNTDOWN).
    eng.tick_race_turns()
    assert played == ["DLG_GM_GLOBAL_115.wav", "DLG_GM_GLOBAL_55.wav"]
    assert numbers == []
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    assert eng.race_state["callout_ends_at"] > time.time()
    eng.tick_race_turns()  # still inside the callout window
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    eng.race_state["callout_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "COUNTDOWN"
    assert eng.race_state["countdown_sent"] is False  # 0x93 goes next loop cycle
    # No countdown_intro pool in the race (numbers must stay in sync
    # with the gun's 0x93 countdown).
    assert "countdown_intro" not in pools
    # Run 1 finishes; round 2 announces "Next round!".
    eng.race_state["countdown_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "RUNNING"
    # The go signal is the single GLOBAL_13 "Go!", not the pool.
    assert played[-1] == "DLG_GM_GLOBAL_13.wav"
    eng.race_state["turn_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    _finish_turn(eng)
    assert eng.race_state["round_index"] == 2
    eng.tick_race_turns()
    assert played[-1] == "DLG_GM_GLOBAL_114.wav"
    eng.race_state["intro_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    eng.tick_race_turns()
    assert played[-1] == "DLG_GM_GLOBAL_55.wav"  # callout repeats every run
    eng.race_state["callout_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    eng.race_state["countdown_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    eng.race_state["turn_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    _finish_turn(eng)
    # Round 3 (final): "Final round".
    assert eng.race_state["round_index"] == 3
    eng.tick_race_turns()
    assert played[-1] == "DLG_GM_GLOBAL_116.wav"
    assert "DLG_GM_GLOBAL_113.wav" not in played  # NEXT GAME! unwired


def test_new_best_announced_only_for_match_best(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=2, settings={"rounds": 2})
    pools: list[str] = []

    def fake_pool(key, custom_subtitle=None, force=False):
        pools.append(key)
        return None

    monkeypatch.setattr(eng.audio, "trigger_pool", fake_pool)
    # P1 finishes 30 s (match best) -> race_new_best scheduled.
    _begin_run(eng)
    _fake_elapsed(eng, 30.0)
    _run_course(eng, 2)
    assert "race_finish" in pools
    followups = [e for e in eng._pending_audio if e.get("pool_key") == "race_new_best"]
    assert len(followups) == 1
    eng._pending_audio.clear()
    pools.clear()
    _finish_turn(eng)
    # P2 finishes slower (40 s) but it IS a session best for P2 ->
    # no vocal new-best (match best only).
    _begin_run(eng)
    _fake_elapsed(eng, 40.0)
    _run_course(eng, 3)
    followups = [e for e in eng._pending_audio if e.get("pool_key") == "race_new_best"]
    assert followups == []
    assert eng.race_session_best[(3, 4)]["time"] == pytest.approx(40.0, abs=0.5)


# ------------------------------------------------------------------- skip


def test_skip_run_in_wait_records_zero_dnf_and_advances() -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    _pass_intro(eng)
    assert eng.race_state["turn_phase"] == "WAIT_TRIGGER"
    ok, _ = eng.skip_race_run()
    assert ok
    p = eng.players[2]
    assert len(p.run_history) == 1
    assert p.run_history[0] == {
        "round": 1, "time": None, "completed": False, "points": 0, "tags": 0, "tower_hits": 0,
    }
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    assert eng.race_state["active_gun_id"] == 3


def test_skip_run_in_countdown_records_zero_dnf() -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    _pass_intro(eng)
    eng._turn_triggered = True
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "COUNTDOWN"
    ok, _ = eng.skip_race_run()
    assert ok
    assert eng.players[2].run_history[0]["points"] == 0
    assert eng.race_state["active_gun_id"] == 3


def test_skip_run_mid_run_keeps_partial_points() -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    _begin_run(eng)
    eng.sim.sim_scan_rfid(2, "RF03")
    _poll_all(eng)
    eng.sim.sim_fire_shot(2, 2)
    _ingest_tower(eng)
    ok, _ = eng.skip_race_run()
    assert ok
    assert eng.race_state["turn_phase"] == "TURN_END"
    p = eng.players[2]
    assert p.run_history[0]["completed"] is False
    assert p.run_history[0]["points"] == 2
    assert p.score == 2
    # The beat is skipped: the next tick advances.
    eng.tick_race_turns()
    assert eng.race_state["active_gun_id"] == 3


def test_skip_run_in_turn_end_advances_immediately() -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    _begin_run(eng)
    _run_course(eng, 2)
    assert eng.race_state["turn_phase"] == "TURN_END"
    ok, _ = eng.skip_race_run()
    assert ok
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    assert eng.race_state["active_gun_id"] == 3


def test_skip_run_in_intro_skips_announcement_wait() -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    assert eng.race_state["turn_phase"] == "ROUND_INTRO"
    ok, _ = eng.skip_race_run()
    assert ok
    # The round line is hurried, but the Player callout still plays:
    # skip lands in PLAYER_INTRO for the same run, unrecorded.
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    assert eng.players[2].run_history == []


def test_skip_run_rejected_when_done_or_not_playing() -> None:
    eng = ArenaEngine()
    _start(eng, guns=1)
    _begin_run(eng)
    _run_course(eng, 2)
    _finish_turn(eng)
    assert eng.race_state["turn_phase"] == "DONE"
    ok, _ = eng.skip_race_run()
    assert not ok
    eng.phase = "LOBBY"
    ok, _ = eng.skip_race_run()
    assert not ok


# ------------------------------------------------------------ stop / bests


def test_stop_mid_run_snapshots_partial_points() -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    _begin_run(eng)
    eng.sim.sim_scan_rfid(2, "RF03")
    _poll_all(eng)
    assert eng.finalize_race_on_stop() is True
    p = eng.players[2]
    assert len(p.run_history) == 1
    assert p.run_history[0]["completed"] is False
    assert p.run_history[0]["points"] == 1
    assert p.score == 1
    assert eng.race_state["turn_phase"] == "DONE"


def test_stop_before_run_fabricates_nothing() -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    _pass_intro(eng)
    assert eng.finalize_race_on_stop() is False
    assert eng.players[2].run_history == []
    assert eng.race_state["turn_phase"] == "DONE"


def test_session_best_keyed_by_base_count() -> None:
    eng = ArenaEngine()
    # Match 1: 4 bases, 30 s.
    _start(eng, guns=1, settings={"numberOfBase": 4})
    _begin_run(eng)
    _fake_elapsed(eng, 30.0)
    _run_course(eng, 2, bases=4)
    assert eng.race_session_best[(2, 4)]["time"] == pytest.approx(30.0, abs=0.5)
    # Match 2: 2 bases, 10 s — must NOT overwrite the 4-base best.
    eng.select_mode("TagThenShoot", {"numberOfBase": 2})
    eng.reset_stats_for_new_match()
    eng.phase = "PLAYING"
    eng.match_start_ts = time.time()
    _poll_all(eng)
    _begin_run(eng)
    _fake_elapsed(eng, 10.0)
    _run_course(eng, 2, bases=2)
    assert eng.race_session_best[(2, 4)]["time"] == pytest.approx(30.0, abs=0.5)
    assert eng.race_session_best[(2, 2)]["time"] == pytest.approx(10.0, abs=0.5)
    # A faster 4-base run updates only the 4-base entry.
    eng.select_mode("TagThenShoot", {"numberOfBase": 4})
    eng.reset_stats_for_new_match()
    eng.phase = "PLAYING"
    eng.match_start_ts = time.time()
    _poll_all(eng)
    _begin_run(eng)
    _fake_elapsed(eng, 25.0)
    _run_course(eng, 2, bases=4)
    assert eng.race_session_best[(2, 4)]["time"] == pytest.approx(25.0, abs=0.5)
    assert eng.race_session_best[(2, 2)]["time"] == pytest.approx(10.0, abs=0.5)
    # Rematch rebuilds history fresh; session bests persist.
    eng.reset_stats_for_new_match()
    assert eng.players[2].run_history == []
    assert (2, 4) in eng.race_session_best


# ------------------------------------------------------- announcer gating


def test_miss_taunt_and_idle_banter_gated_off_in_race(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=1)
    _begin_run(eng)
    pools: list[str] = []

    def fake_pool(key, custom_subtitle=None, force=False):
        pools.append(key)
        return None

    monkeypatch.setattr(eng.audio, "trigger_pool", fake_pool)
    # Six tower shots in one run: would trip the 5-miss threshold.
    for _ in range(6):
        eng.sim.sim_fire_shot(2, None)
        _poll_all(eng)
    assert "miss_taunt" not in pools
    # Idle banter: long silence, plenty of cap left.
    eng.last_action_ts = time.time() - 30
    eng._last_idle_banter_ts = time.time() - 30
    eng.check_idle_announcer()
    assert "idle_banter" not in pools

    # Control: the same shots in FreeFrag DO taunt.
    eng2 = ArenaEngine()
    eng2.set_num_simulated_guns(1)
    eng2.select_mode("FreeFrag")
    eng2.reset_stats_for_new_match()
    eng2.phase = "PLAYING"
    eng2.match_start_ts = time.time()
    _poll_all(eng2)
    pools2: list[str] = []
    monkeypatch.setattr(eng2.audio, "trigger_pool", lambda k, custom_subtitle=None, force=False: pools2.append(k))
    for _ in range(6):
        eng2.sim.sim_fire_shot(2, None)
        _poll_all(eng2)
    assert "miss_taunt" in pools2


def test_miss_streak_resets_each_run() -> None:
    eng = ArenaEngine()
    _start(eng, guns=1, settings={"rounds": 2})
    _begin_run(eng)
    # The taunt is gated off in race mode, so status reports never
    # raise the streak; simulate a streak left over from a prior mode
    # and confirm the run start clears it.
    eng.players[2].miss_streak = 4
    eng.race_state["turn_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    _finish_turn(eng)
    _begin_run(eng)
    assert eng.players[2].miss_streak == 0


# ------------------------------------------------------------- app layer


class _FakeHw:
    def __init__(self, results: list[bool]) -> None:
        self.results = list(results)
        self.calls = 0

    def start_countdown(self, gun_ids, seconds=10) -> bool:
        self.calls += 1
        return self.results.pop(0) if self.results else False


def test_race_countdown_retries_once_then_continues(monkeypatch) -> None:
    import battletag.app as app_mod

    eng = ArenaEngine()
    monkeypatch.setattr(app_mod, "engine", eng)
    # Fail once, succeed on retry.
    eng.hw = _FakeHw([False, True])  # type: ignore[assignment]
    assert asyncio.run(app_mod._send_race_countdown(2, 5)) is True
    assert eng.hw.calls == 2
    # Fail twice: returns False (caller toasts + continues, never aborts).
    eng.hw = _FakeHw([False, False])  # type: ignore[assignment]
    assert asyncio.run(app_mod._send_race_countdown(2, 5)) is False
    assert eng.hw.calls == 2


def _stub_broadcast(monkeypatch) -> None:
    import battletag.app as app_mod

    async def _noop() -> None:
        return None

    monkeypatch.setattr(app_mod, "broadcast_state", _noop)


def test_race_roster_endpoints(monkeypatch) -> None:
    import battletag.app as app_mod
    from fastapi import HTTPException

    eng = ArenaEngine()
    eng.set_num_simulated_guns(3)
    eng.select_mode("TagThenShoot")
    monkeypatch.setattr(app_mod, "engine", eng)
    _stub_broadcast(monkeypatch)

    res = asyncio.run(app_mod.api_race_roster(app_mod.RaceRosterRequest(order=[3, 1], bench=[2])))
    assert res.status_code == 200
    assert eng.race_roster == {"order": [3, 1], "bench": [2]}

    # HUD toggle: opt 2 back in (appends), opt 3 out.
    asyncio.run(app_mod.api_race_roster(app_mod.RaceRosterRequest(gun_num=2, in_race=True)))
    assert eng.race_roster == {"order": [3, 1, 2], "bench": []}
    asyncio.run(app_mod.api_race_roster(app_mod.RaceRosterRequest(gun_num=3, in_race=False)))
    assert eng.race_roster == {"order": [1, 2], "bench": [3]}

    # Invalid partition -> 400.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(app_mod.api_race_roster(app_mod.RaceRosterRequest(order=[1], bench=[])))
    assert exc.value.status_code == 400

    # Outside setup -> 400 (display gate is not the only enforcement).
    eng.phase = "PLAYING"
    with pytest.raises(HTTPException) as exc2:
        asyncio.run(app_mod.api_race_roster(app_mod.RaceRosterRequest(gun_num=3, in_race=True)))
    assert exc2.value.status_code == 400


def test_skip_run_endpoint(monkeypatch) -> None:
    import battletag.app as app_mod
    from fastapi import HTTPException

    eng = ArenaEngine()
    _start(eng, guns=2)
    monkeypatch.setattr(app_mod, "engine", eng)
    _stub_broadcast(monkeypatch)
    _pass_intro(eng)
    res = asyncio.run(app_mod.api_race_skip_run())
    assert res.status_code == 200
    assert eng.players[2].run_history[0]["completed"] is False

    eng.phase = "LOBBY"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(app_mod.api_race_skip_run())
    assert exc.value.status_code == 400


def test_match_start_gates_on_roster_not_pairing(monkeypatch) -> None:
    import battletag.app as app_mod
    from fastapi import HTTPException

    eng = ArenaEngine()
    eng.set_num_simulated_guns(3)
    eng.select_mode("TagThenShoot")
    # Bench player 3 with a dead vest/offline blaster: must not block.
    eng.players[4].vest_ok = False
    eng.players[4].gun_online = False
    assert eng.set_race_roster([1, 2], [3])[0]
    monkeypatch.setattr(app_mod, "engine", eng)
    _stub_broadcast(monkeypatch)

    async def _fake_lifecycle() -> None:
        return None

    monkeypatch.setattr(app_mod, "_run_match_lifecycle", _fake_lifecycle)
    res = asyncio.run(app_mod.api_start_match())
    assert res.status_code == 200

    # A RACER with a dead vest blocks.
    eng.players[2].vest_ok = False
    with pytest.raises(HTTPException) as exc:
        asyncio.run(app_mod.api_start_match())
    assert exc.value.status_code == 400
    assert "racers" in exc.value.detail.lower()


# ------------------------------------------------------- UP NEXT derivation


def _race_started(
    eng: ArenaEngine,
    guns: int = 3,
    order: list[int] | None = None,
    bench: list[int] | None = None,
    rounds: int = 2,
) -> None:
    eng.set_num_simulated_guns(guns)
    eng.select_mode("TagThenShoot", {"rounds": rounds, "gameLaunch": 1, "numberOfBase": 4})
    if order is not None:
        assert eng.set_race_roster(order, bench or [])[0]
    eng.reset_stats_for_new_match()
    eng.phase = "PLAYING"
    eng.match_start_ts = time.time()
    _poll_all(eng)


def _skip_wait(eng: ArenaEngine) -> None:
    """Skip the current unstarted run from WAIT_TRIGGER/PLAYER_INTRO/ROUND_INTRO."""
    if eng.race_state["turn_phase"] == "ROUND_INTRO":
        _pass_intro(eng)
    assert eng.race_state["turn_phase"] in ("WAIT_TRIGGER", "PLAYER_INTRO")
    assert eng.skip_race_run()[0]


def test_next_gun_mid_round_wrap_and_turn_end() -> None:
    eng = ArenaEngine()
    _race_started(eng, order=[1, 2, 3], rounds=2)
    # gun_nums [1, 2, 3] -> gun_ids [2, 3, 4].
    assert eng.race_state["active_gun_id"] == 2
    assert eng.race_state["next_gun_id"] == 3
    assert eng.race_next_gun_id() == 3
    # The snapshot carries the same field (both displays read it).
    assert eng.get_snapshot()["race_state"]["next_gun_id"] == 3

    _begin_run(eng)
    _run_course(eng, 2)
    assert eng.race_state["turn_phase"] == "TURN_END"
    # TURN_END still points at the following racer.
    assert eng.race_state["next_gun_id"] == 3
    _finish_turn(eng)
    assert eng.race_state["active_gun_id"] == 3
    assert eng.race_state["next_gun_id"] == 4

    _begin_run(eng)
    _run_course(eng, 3)
    _finish_turn(eng)
    # Last run of round 1: next wraps to round 2's first racer.
    assert eng.race_state["round_index"] == 1
    assert eng.race_state["active_gun_id"] == 4
    assert eng.race_state["next_gun_id"] == 2

    _begin_run(eng)
    _run_course(eng, 4)
    _finish_turn(eng)
    # Round 2 opens with the first racer; next is the second.
    assert eng.race_state["round_index"] == 2
    assert eng.race_state["active_gun_id"] == 2
    assert eng.race_state["next_gun_id"] == 3


def test_next_gun_none_on_final_run_and_done() -> None:
    eng = ArenaEngine()
    _race_started(eng, order=[1, 2], bench=[3], rounds=2)
    # Skip the first three runs (2, 3, 2); the fourth is the final run.
    for _ in range(3):
        _skip_wait(eng)
    assert eng.race_state["active_gun_id"] == 3
    assert eng.race_state["round_index"] == 2
    assert eng.race_state["next_gun_id"] is None
    _begin_run(eng)
    assert eng.race_state["next_gun_id"] is None
    _run_course(eng, 3)
    _finish_turn(eng)
    assert eng.race_state["turn_phase"] == "DONE"
    assert eng.race_state["active_gun_id"] is None
    assert eng.race_state["next_gun_id"] is None


def test_next_gun_none_in_solo_race() -> None:
    eng = ArenaEngine()
    _race_started(eng, guns=1, order=[1], rounds=2)
    assert eng.race_state["active_gun_id"] == 2
    assert eng.race_state["next_gun_id"] is None
    _begin_run(eng)
    assert eng.race_state["next_gun_id"] is None
    _run_course(eng, 2)
    _finish_turn(eng)
    # Round 2, same player again: still no UP NEXT (never both pills).
    assert eng.race_state["round_index"] == 2
    assert eng.race_state["active_gun_id"] == 2
    assert eng.race_state["next_gun_id"] is None


def test_next_gun_updates_after_skip() -> None:
    eng = ArenaEngine()
    _race_started(eng, order=[1, 2, 3], rounds=2)
    assert eng.race_state["next_gun_id"] == 3
    # Skipping the intro wait does not move the turn: next unchanged.
    assert eng.race_state["turn_phase"] == "ROUND_INTRO"
    assert eng.skip_race_run()[0]
    assert eng.race_state["next_gun_id"] == 3
    # Skipping the wait advances: next follows the new active racer.
    assert eng.skip_race_run()[0]
    assert eng.race_state["active_gun_id"] == 3
    assert eng.race_state["next_gun_id"] == 4
    # Run + skip the beat: next wraps to round 2's first racer.
    _begin_run(eng)
    _run_course(eng, 3)
    assert eng.race_state["turn_phase"] == "TURN_END"
    assert eng.skip_race_run()[0]
    assert eng.race_state["active_gun_id"] == 4
    assert eng.race_state["next_gun_id"] == 2


def test_next_gun_never_a_benched_gun() -> None:
    eng = ArenaEngine()
    _race_started(eng, order=[2, 1], bench=[3], rounds=2)
    # turn_order is gun_ids [3, 2]; gun 3 (gid 4) is benched.
    assert eng.race_state["turn_order"] == [3, 2]
    seen: set[int | None] = set()
    for _ in range(4):
        seen.add(eng.race_state["next_gun_id"])
        _skip_wait(eng)
    seen.add(eng.race_state["next_gun_id"])
    assert 4 not in seen
    assert seen <= {None, 2, 3}
    assert eng.race_state["turn_phase"] == "DONE"


# ------------------------------------------------- spoken prompts (audio)


def _install_fake_audio(eng: ArenaEngine, monkeypatch):
    """Record announcer files/numbers/pools, modeling active_until like
    the real engine so intro/callout windows are testable. Returns
    (played, numbers, pools): played entries are (file, subtitle,
    priority, duration); pools entries are (pool_key, custom_subtitle)."""
    played: list[tuple] = []
    numbers: list[int] = []
    pools: list[tuple] = []

    def fake_file(name, subtitle, priority=0, duration=2.0):
        played.append((name, subtitle, priority, duration))
        eng.audio.active_until = time.time() + duration
        eng.audio.active_priority = priority
        return object()

    def fake_number(n):
        numbers.append(n)
        return None

    def fake_pool(key, custom_subtitle=None, force=False):
        pools.append((key, custom_subtitle))
        eng.audio.active_until = time.time() + 1.5
        eng.audio.active_priority = 3
        return object()

    monkeypatch.setattr(eng.audio, "play_announcer_file", fake_file)
    monkeypatch.setattr(eng.audio, "play_countdown_number", fake_number)
    monkeypatch.setattr(eng.audio, "trigger_pool", fake_pool)
    return played, numbers, pools


def _next_prompts(eng: ArenaEngine) -> list[dict]:
    return [e for e in eng._pending_audio if e.get("kind") == "file" and str(e.get("subtitle", "")).startswith("Next:")]


def _tag_and_shoot(eng: ArenaEngine, gid: int, base: int) -> None:
    eng.sim.sim_scan_rfid(gid, f"RF0{base + 2}")
    _poll_all(eng)
    eng.sim.sim_fire_shot(gid, gid)
    _ingest_tower(eng)


def test_player_callout_every_run_manual_order_files_subtitles(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=2, settings={"rounds": 2})
    played, _, _ = _install_fake_audio(eng, monkeypatch)
    p1, p2 = eng.players[2], eng.players[3]
    for gid in (2, 3, 2, 3):
        _begin_run(eng)
        _run_course(eng, gid)
        _finish_turn(eng)
    assert eng.race_state["turn_phase"] == "DONE"
    callouts = [p for p in played if p[1].endswith("is up!")]
    assert [c[0] for c in callouts] == [
        "DLG_GM_GLOBAL_55.wav",
        "DLG_GM_GLOBAL_56.wav",
        "DLG_GM_GLOBAL_55.wav",
        "DLG_GM_GLOBAL_56.wav",
    ]
    assert [c[1] for c in callouts] == [
        f"Player 1 ({p1.display_name}) is up!",
        f"Player 2 ({p2.display_name}) is up!",
        f"Player 1 ({p1.display_name}) is up!",
        f"Player 2 ({p2.display_name}) is up!",
    ]
    assert all(c[2] == -1 for c in callouts)
    assert all(c[3] == 1.9 for c in callouts)
    # Round lines, callouts, and Go lines interleave in run order.
    seq = [p[0] for p in played if p[0] in {
        "DLG_GM_GLOBAL_115.wav", "DLG_GM_GLOBAL_114.wav", "DLG_GM_GLOBAL_116.wav",
        "DLG_GM_GLOBAL_55.wav", "DLG_GM_GLOBAL_56.wav", "DLG_GM_GLOBAL_13.wav",
    }]
    assert seq == [
        "DLG_GM_GLOBAL_115.wav", "DLG_GM_GLOBAL_55.wav", "DLG_GM_GLOBAL_13.wav",
        "DLG_GM_GLOBAL_56.wav", "DLG_GM_GLOBAL_13.wav",
        "DLG_GM_GLOBAL_116.wav", "DLG_GM_GLOBAL_55.wav", "DLG_GM_GLOBAL_13.wav",
        "DLG_GM_GLOBAL_56.wav", "DLG_GM_GLOBAL_13.wav",
    ]


def test_player_callout_auto_no_numbers_until_countdown(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=2, settings={"gameLaunch": 0, "rounds": 1})
    played, numbers, _ = _install_fake_audio(eng, monkeypatch)
    eng.tick_race_turns()
    assert played[0][0] == "DLG_GM_GLOBAL_115.wav"
    eng.race_state["intro_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    eng.tick_race_turns()
    assert played[-1][0] == "DLG_GM_GLOBAL_55.wav"
    assert numbers == []
    assert eng.race_state["callout_ends_at"] > time.time()
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    assert numbers == []
    eng.race_state["callout_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "COUNTDOWN"
    # The 0x93 send is gated on COUNTDOWN entry (app loop, countdown_sent):
    # it cannot fire before the callout window ends.
    assert eng.race_state["countdown_sent"] is False
    eng.tick_race_turns()
    assert numbers == [10]


def test_player_callout_after_skip_and_trigger_during_callout_ignored(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    played, _, _ = _install_fake_audio(eng, monkeypatch)
    _pass_intro(eng)
    assert eng.race_state["turn_phase"] == "WAIT_TRIGGER"
    assert eng.skip_race_run()[0]
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    assert eng.race_state["active_gun_id"] == 3
    eng.tick_race_turns()
    assert played[-1][0] == "DLG_GM_GLOBAL_56.wav"

    # A trigger pull during the callout window does not release the
    # wait: entering WAIT_TRIGGER clears it, so the player pulls again
    # after hearing who is up.
    eng2 = ArenaEngine()
    _start(eng2, guns=2)
    eng2.tick_race_turns()
    eng2.race_state["intro_ends_at"] = time.time() - 1.0
    eng2.tick_race_turns()
    assert eng2.race_state["turn_phase"] == "PLAYER_INTRO"
    eng2._turn_triggered = True
    eng2.tick_race_turns()
    eng2.race_state["callout_ends_at"] = time.time() - 1.0
    eng2.tick_race_turns()
    assert eng2.race_state["turn_phase"] == "WAIT_TRIGGER"
    assert eng2._turn_triggered is False


def test_skip_from_player_intro_forfeits_next_run_gets_callout(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    played, _, _ = _install_fake_audio(eng, monkeypatch)
    eng.tick_race_turns()
    eng.race_state["intro_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    assert eng.skip_race_run()[0]
    assert len(eng.players[2].run_history) == 1
    assert eng.players[2].run_history[0]["completed"] is False
    assert eng.players[2].run_history[0]["points"] == 0
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    assert eng.race_state["active_gun_id"] == 3
    eng.tick_race_turns()
    assert played[-1][0] == "DLG_GM_GLOBAL_56.wav"


def test_next_base_prompts_4_base_course_final_hit_silent(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=2, settings={"numberOfBase": 4})
    played, _, pools = _install_fake_audio(eng, monkeypatch)
    _begin_run(eng)
    assert eng.race_state["turn_phase"] == "RUNNING"
    # T-Base 1 prompt is queued at Go (fired below with the others).
    assert [e["filename"] for e in _next_prompts(eng)] == ["DLG_GM_GLOBAL_72.wav"]
    for base, prompt_file in ((1, "DLG_GM_GLOBAL_73.wav"), (2, "DLG_GM_GLOBAL_74.wav"), (3, "DLG_GM_GLOBAL_75.wav")):
        _tag_and_shoot(eng, 2, base)
        assert eng.race_state["turn_phase"] == "RUNNING"
        assert prompt_file in [e["filename"] for e in _next_prompts(eng)]
        entry = [e for e in _next_prompts(eng) if e["filename"] == prompt_file][0]
        assert entry["subtitle"] == f"Next: T-Base {base + 1}!"
        assert entry["race_run"] == 0
        assert entry["priority"] == 2
    # Fire everything due: prompts sound in order with the tag/tower
    # confirmations still present.
    eng.tick_pending_audio(now=time.time() + 10)
    subs = [p[1] for p in played]
    assert "Next: T-Base 1!" in subs
    assert "Next: T-Base 2!" in subs
    assert "Next: T-Base 3!" in subs
    assert "Next: T-Base 4!" in subs
    assert [k for k, _ in pools].count("tower_hit") == 3
    # Final tag + tower hit: the run completes, no prompt is queued.
    before = len(_next_prompts(eng))
    _tag_and_shoot(eng, 2, 4)
    assert eng.race_state["turn_phase"] == "TURN_END"
    assert len(_next_prompts(eng)) == before
    assert "race_finish" in [k for k, _ in pools]


def test_next_base_prompt_2_base_course(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=2, settings={"numberOfBase": 2})
    played, _, _ = _install_fake_audio(eng, monkeypatch)
    _begin_run(eng)
    _tag_and_shoot(eng, 2, 1)
    assert eng.race_state["turn_phase"] == "RUNNING"
    assert [e["filename"] for e in _next_prompts(eng)] == [
        "DLG_GM_GLOBAL_72.wav",
        "DLG_GM_GLOBAL_73.wav",
    ]
    before = [e["filename"] for e in _next_prompts(eng)]
    _tag_and_shoot(eng, 2, 2)
    assert eng.race_state["turn_phase"] == "TURN_END"
    # The final hit queues no new prompt.
    assert [e["filename"] for e in _next_prompts(eng)] == before
    eng.tick_pending_audio(now=time.time() + 10)
    # The queued prompts belonged to the finished run: dropped, never
    # sounded into the next run's sequence.
    assert not [p for p in played if p[1].startswith("Next:")]


def test_next_prompt_fires_while_run_live(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    played, _, _ = _install_fake_audio(eng, monkeypatch)
    _begin_run(eng)
    eng.tick_pending_audio(now=time.time() + 10)
    assert ("DLG_GM_GLOBAL_72.wav", "Next: T-Base 1!", 2, 1.5) in played


def test_next_prompt_dropped_after_cap(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    played, _, _ = _install_fake_audio(eng, monkeypatch)
    _begin_run(eng)
    _tag_and_shoot(eng, 2, 1)
    assert _next_prompts(eng)
    eng.race_state["turn_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "TURN_END"
    eng.tick_pending_audio(now=time.time() + 10)
    assert not [p for p in played if p[1].startswith("Next:")]


def test_next_prompt_dropped_after_skip(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    played, _, _ = _install_fake_audio(eng, monkeypatch)
    _begin_run(eng)
    _tag_and_shoot(eng, 2, 1)
    assert _next_prompts(eng)
    assert eng.skip_race_run()[0]
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "PLAYER_INTRO"
    eng.tick_pending_audio(now=time.time() + 10)
    assert not [p for p in played if p[1].startswith("Next:")]


def test_next_prompt_dropped_after_stop(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    played, _, _ = _install_fake_audio(eng, monkeypatch)
    _begin_run(eng)
    _tag_and_shoot(eng, 2, 1)
    assert _next_prompts(eng)
    assert eng.finalize_race_on_stop() is True
    eng.phase = "GAME_OVER"
    eng.tick_pending_audio(now=time.time() + 10)
    assert not [p for p in played if p[1].startswith("Next:")]
    assert _next_prompts(eng) == []


def test_confirmations_unchanged_no_shoot_line(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    played, _, pools = _install_fake_audio(eng, monkeypatch)
    _begin_run(eng)
    _run_course(eng, 2)
    eng.tick_pending_audio(now=time.time() + 10)
    # Tag confirmations still fire: base names + tagnshoot_base pool.
    tag_names = [p[1] for p in played if p[1] in {"T-Base 1!", "T-Base 2!", "T-Base 3!", "T-Base 4!"}]
    assert len(tag_names) == 4
    assert "tagnshoot_base" in [k for k, _ in pools]
    assert [k for k, _ in pools].count("tower_hit") == 4
    # No spoken shoot-the-tower line anywhere: no INIT file, and no
    # played/scheduled subtitle tells the runner to shoot.
    assert not [p for p in played if "INIT" in p[0]]
    assert not [p for p in played if "shoot" in p[1].lower()]
    assert not [e for e in eng._pending_audio if "shoot" in str(e.get("subtitle", "")).lower()]


def test_next_prompts_never_fire_out_of_order(monkeypatch) -> None:
    eng = ArenaEngine()
    _start(eng, guns=2)
    played, _, _ = _install_fake_audio(eng, monkeypatch)
    # Block the tower-hit pool (as priority arbitration does while a
    # tag callout window is still active): the tower prompt then
    # falls back to the 0.1s delay and must still not precede the
    # T-Base 1 prompt queued at Go.
    monkeypatch.setattr(eng.audio, "trigger_pool", lambda *a, **k: None)
    _begin_run(eng)
    _tag_and_shoot(eng, 2, 1)
    prompts = _next_prompts(eng)
    assert [e["subtitle"] for e in prompts] == ["Next: T-Base 1!", "Next: T-Base 2!"]
    assert prompts[1]["fire_at"] > prompts[0]["fire_at"]
    eng.tick_pending_audio(now=prompts[0]["fire_at"] + 0.01)
    assert [p[1] for p in played if p[1].startswith("Next:")] == ["Next: T-Base 1!"]
    eng.tick_pending_audio(now=prompts[1]["fire_at"] + 0.01)
    assert [p[1] for p in played if p[1].startswith("Next:")] == ["Next: T-Base 1!", "Next: T-Base 2!"]
