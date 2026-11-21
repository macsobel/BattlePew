"""Focused tests for the mode/announcer review fixes (F1/F3/F4/F6-F17).

Covers: frag credit to the killing shooter, duel 6-ammo + timerless
rounds, LMS survival scoring + alive-based outcomes, TagThenShoot
separate tag/tower stats + 0xC3 counter reset, winner callout Set B +
shared winners on ties, the new announcer triggers resolving to real
files, and simulator timer/scan respawn + auto-reload.

Audio-file assertions are skipped when the handoff game tree is absent
(a bare checkout ships silent); the engine/sim logic tests always run.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from battletag.audio import ANNOUNCER_DIR, GameMasterAudio
from battletag.modes import MODE_CATALOG, ArenaEngine

AUDIO = pytest.mark.skipif(not ANNOUNCER_DIR.is_dir(), reason="game audio tree not present")


def _start(eng: ArenaEngine, mode: str, guns: int = 2, settings: dict | None = None) -> None:
    eng.set_num_simulated_guns(guns)
    eng.select_mode(mode, settings)
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


def _start_run(eng: ArenaEngine) -> None:
    """Drive the active player's timed turn from the round intro to RUNNING."""
    if eng.race_state["turn_phase"] == "ROUND_INTRO":
        eng.tick_race_turns()
        eng.race_state["intro_ends_at"] = time.time() - 1.0
        eng.tick_race_turns()
    if eng.race_state["turn_phase"] == "PLAYER_INTRO":
        eng.tick_race_turns()
        eng.race_state["callout_ends_at"] = time.time() - 1.0
        eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "WAIT_TRIGGER"
    eng._turn_triggered = True
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "COUNTDOWN"
    eng.race_state["countdown_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "RUNNING"


def test_frag_credit_goes_to_killing_shooter() -> None:
    """F6: in a multi-shooter report the frag (+3) goes to shooters[0],
    the most-recent shooter (the killing blow per the shift-register
    model), not to the oldest shooter in the slots."""
    eng = ArenaEngine()
    _start(eng, "TeamFrag", guns=4)
    # Gid 2 and 4 are team 1; gid 3 is team 2. Three hits from gid 2
    # leave the victim at 2 life.
    for _ in range(3):
        eng.sim.sim_fire_shot(2, 3)
        _poll_all(eng)
    assert eng.players[3].life_points == 2
    # Two more shots land before the next poll: gid 2, then the killing
    # blow from gid 4. One report carries both shooters.
    eng.sim.sim_fire_shot(2, 3)
    eng.sim.sim_fire_shot(4, 3)
    _poll_all(eng)
    assert eng.players[3].life_points == 0
    assert eng.players[4].frags == 1
    assert eng.players[2].frags == 0
    assert eng.players[4].score == 4  # 1 hit + 3 frag bonus
    assert eng.players[2].score == 4  # 4 plain hits, no frag


def test_duel_six_ammo_no_timer_and_rounds_advance() -> None:
    """F3/F4/F10/F15: 6 bullets per round, no countdown/timer end, the
    DRAW signal gates canShoot, and round results are recorded."""
    eng = ArenaEngine()
    _start(eng, "OldFashionDuel")
    p1, p2 = eng.players[2], eng.players[3]
    assert p1.max_ammo == 6 and p1.ammo == 6
    assert eng.sim.guns[2].ammo == 6
    assert MODE_CATALOG["OldFashionDuel"]["timed"] is False
    assert eng.get_remaining_seconds() is None
    eng.match_start_ts = time.time() - 1000
    assert eng.check_auto_end_conditions() is False
    assert eng.format_clock() == "00:00"  # before Round 1 begins
    eng.match_start_ts = time.time()

    # Round 1: INTRO -> WAIT -> DRAW. canShoot stays False until the
    # modeled DRAW line finishes.
    eng.duel_state["next_transition_at"] = 0
    eng.tick_mode_timers()
    assert eng.duel_state["subphase"] == "WAIT"
    eng.duel_state["round_started_at"] = time.time() - 15.0
    assert eng.format_clock() == "00:15"  # counts up per round
    eng.duel_state["next_transition_at"] = 0
    eng.tick_mode_timers()
    assert eng.duel_state["subphase"] == "DRAW"
    assert eng.duel_state["can_shoot"] is False
    eng.tick_pending_audio(now=time.time() + 3.0)
    assert eng.duel_state["can_shoot"] is True

    # Five misses leave the round live with 6 bullets; the 6th wins it.
    # Even if both players land hits in the same poll cycle, only 1 point
    # can be awarded in the round.
    for _ in range(5):
        eng.sim.sim_fire_shot(2, None)
        _poll_all(eng)
    assert eng.duel_state["subphase"] == "DRAW" and p1.ammo == 1
    eng.sim.sim_fire_shot(2, 3)
    _poll_all(eng)
    assert p1.score == 1
    assert eng.duel_state["subphase"] == "ROUND_END"
    # A subsequent hit in the same round cannot award a second point.
    eng.sim.sim_fire_shot(3, 2)
    _poll_all(eng)
    assert p1.score == 1 and p2.score == 0
    assert eng.duel_state["round_scores"] == {2: 1, 3: 0}  # keyed by gun_id
    assert eng.duel_state["round_results"] == [{"round": 1, "winner_gun_num": 1}]
    if ANNOUNCER_DIR.is_dir():
        # Winner callout (Set A) with no generic lead-change line
        # stepping on the round-win sequence.
        names = [c.filename for c in eng.audio.recent_cues]
        assert "DLG_GM_GLOBAL_55.wav" in names
        assert not {"DLG_GM_GLOBAL_26.wav", "DLG_GM_GLOBAL_27.wav", "DLG_GM_GLOBAL_28.wav"} & set(names)

    # Round 2: an early hit (before DRAW) forfeits the round to the victim
    # without counting as a legal hit landed or received, or penalizing accuracy.
    shots_before_early = p1.shots_fired
    acc_before_early = p1.accuracy
    eng.duel_state["next_transition_at"] = 0
    eng.tick_mode_timers()
    assert eng.duel_state["round"] == 2 and eng.duel_state["subphase"] == "WAIT"
    eng.sim.sim_fire_shot(2, 3)
    _poll_all(eng)
    assert p2.score == 1
    assert p1.early_fires == 1 and p2.early_fires == 0
    assert p1.hits_landed == 1 and p2.hits_received == 1
    assert p1.shots_fired == shots_before_early
    assert p1.accuracy == acc_before_early
    assert eng.duel_state["round_results"][-1] == {"round": 2, "winner_gun_num": 2}

    # Round 3: both duelists empty their guns -> a recorded draw.
    eng.duel_state["next_transition_at"] = 0
    eng.tick_mode_timers()
    eng.duel_state["next_transition_at"] = 0
    eng.tick_mode_timers()
    eng.tick_pending_audio(now=time.time() + 3.0)
    for _ in range(6):
        eng.sim.sim_fire_shot(2, None)
        eng.sim.sim_fire_shot(3, None)
        _poll_all(eng)
    assert eng.duel_state["round_results"][-1] == {"round": 3, "winner_gun_num": None}
    assert len(eng.duel_state["round_results"]) == 3


def test_lms_survival_scoring_and_alive_based_outcomes() -> None:
    """F4/F7: no timer; alive score is life + #players, eliminated
    score is #players - #alive, and the sole survivor wins even with
    the lowest score."""
    eng = ArenaEngine()
    _start(eng, "LastManStanding", guns=3)
    assert MODE_CATALOG["LastManStanding"]["timed"] is False
    assert eng.get_remaining_seconds() is None
    eng.match_start_ts = time.time() - 1000
    assert eng.check_auto_end_conditions() is False
    eng.match_start_ts = time.time()

    victim = eng.players[3]
    eng.sim.sim_fire_shot(2, 3)
    _poll_all(eng)
    assert victim.score == 4 + 3  # life 4 + 3 players
    for _ in range(4):
        eng.sim.sim_fire_shot(2, 3)
        _poll_all(eng)
    assert victim.life_points == 0
    assert victim.status_badge == "ELIMINATED"
    assert victim.score == 3 - 2  # 3 players, 2 alive

    for _ in range(5):
        eng.sim.sim_fire_shot(2, 4)
        _poll_all(eng)
    assert eng.players[4].score == 3 - 1
    # The survivor never took a hit: their score is the starting
    # life + #players (5 + 3), above every eliminated player's.
    assert eng.players[2].score == 5 + 3
    assert eng.compute_gameover_outcomes() == {2: True, 3: False, 4: False}
    assert eng.check_auto_end_conditions() is True


def test_tagtenshoot_separate_stats_and_counter_reset() -> None:
    """F1/F8/F9/F11: tags and tower hits score +1 as separate stats,
    out-of-order scans score nothing, and the 0xC3 counter resets
    across matches (no phantom tower hits)."""
    eng = ArenaEngine()
    _start(eng, "TagThenShoot")
    _start_run(eng)
    assert MODE_CATALOG["TagThenShoot"]["max_players"] == 8
    p = eng.players[2]

    eng.sim.sim_scan_rfid(2, "RF03")
    _poll_all(eng)
    assert (p.tags, p.tower_hits, p.score) == (1, 0, 1)
    eng.sim.sim_fire_shot(2, 2)
    eng.ingest_ubiconnect_hits(eng.sim.ubiconnect_hits)
    assert (p.tags, p.tower_hits, p.frags, p.score) == (1, 1, 1, 2)

    eng.sim.sim_scan_rfid(2, "RF05")  # wrong base: expecting base 2
    _poll_all(eng)
    assert (p.tags, p.score) == (1, 2)

    eng.sim.sim_scan_rfid(2, "RF04")
    _poll_all(eng)
    eng.sim.sim_fire_shot(2, 2)
    eng.ingest_ubiconnect_hits(eng.sim.ubiconnect_hits)
    assert (p.tags, p.tower_hits, p.score) == (2, 2, 4)

    # Second match: everything resets, including the hardware counter.
    eng.hw = SimpleNamespace(ubiconnect_hits={2: 5})
    eng.reset_stats_for_new_match()
    assert eng.hw.ubiconnect_hits == {}
    assert eng.sim.ubiconnect_hits == {}
    eng.hw = None
    eng.phase = "PLAYING"
    eng.match_start_ts = time.time()
    assert (p.tags, p.tower_hits, p.frags, p.score) == (0, 0, 0, 0)
    _poll_all(eng)
    _start_run(eng)
    eng.sim.sim_scan_rfid(2, "RF03")
    _poll_all(eng)
    eng.sim.sim_fire_shot(2, 2)
    eng.ingest_ubiconnect_hits(eng.sim.ubiconnect_hits)
    assert (p.tags, p.tower_hits, p.score) == (1, 1, 2)


@AUDIO
def test_winner_callout_set_b_solo() -> None:
    """F12: a solo winner gets the Set B callout (GLOBAL_97-104), not
    the duel-round Set A, after the GLOBAL_53 intro."""
    from battletag import app as bt_app
    from battletag.modes import PlayerState

    eng = bt_app.engine
    saved = (dict(eng.players), eng.phase, eng.mode_name, eng.results_revealed)
    try:
        eng.mode_name = "FreeFrag"
        eng.phase = "GAME_OVER"
        eng.results_revealed = False
        eng.players = {
            1: PlayerState(gun_num=1, gun_id=1, ref_hex="0101", name="ALPHA", avatar="Shark", score=5),
            2: PlayerState(gun_num=2, gun_id=2, ref_hex="0102", name="BRAVO", avatar="Eagle", score=12),
        }
        eng.audio.recent_cues.clear()
        eng.audio.active_until = 0.0
        eng.audio.active_priority = 99

        async def _run() -> None:
            await bt_app.api_reveal_results()
            await asyncio.sleep(2.2)

        asyncio.run(_run())
        names = [c.filename for c in eng.audio.recent_cues]
        assert names == ["DLG_GM_GLOBAL_53.wav", "DLG_GM_GLOBAL_98.wav"]
    finally:
        eng.players, eng.phase, eng.mode_name, eng.results_revealed = saved
        bt_app.pending_notifications.clear()


@AUDIO
def test_winner_shared_winners_on_ties() -> None:
    """F12: ties announce every shared winner (no tie-breaks) — Set B
    callouts for solo ties, team lines for team ties."""
    from battletag import app as bt_app
    from battletag.modes import PlayerState

    eng = bt_app.engine
    saved = (dict(eng.players), eng.phase, eng.mode_name, eng.results_revealed)
    try:
        # Solo tie: GLOBAL_54 intro + one Set B callout per winner.
        eng.mode_name = "FreeFrag"
        eng.phase = "GAME_OVER"
        eng.results_revealed = False
        eng.players = {
            1: PlayerState(gun_num=1, gun_id=1, ref_hex="0101", name="ALPHA", avatar="Shark", score=10),
            2: PlayerState(gun_num=2, gun_id=2, ref_hex="0102", name="BRAVO", avatar="Eagle", score=10),
        }
        eng.audio.recent_cues.clear()
        eng.audio.active_until = 0.0
        eng.audio.active_priority = 99

        async def _run() -> None:
            await bt_app.api_reveal_results()
            await asyncio.sleep(4.3)

        asyncio.run(_run())
        names = [c.filename for c in eng.audio.recent_cues]
        assert names == [
            "DLG_GM_GLOBAL_54.wav",
            "DLG_GM_GLOBAL_97.wav",
            "DLG_GM_GLOBAL_98.wav",
        ]

        # Team tie: GLOBAL_54 intro + one team line per tied team.
        eng.mode_name = "TeamFrag"
        eng.results_revealed = False
        eng.players = {
            1: PlayerState(gun_num=1, gun_id=1, ref_hex="0101", name="ALPHA", avatar="Shark", team_id=1, score=10),
            2: PlayerState(gun_num=2, gun_id=2, ref_hex="0102", name="BRAVO", avatar="Eagle", team_id=2, score=10),
        }
        eng.audio.recent_cues.clear()
        eng.audio.active_until = 0.0
        eng.audio.active_priority = 99
        asyncio.run(_run())
        names = [c.filename for c in eng.audio.recent_cues]
        assert names == [
            "DLG_GM_GLOBAL_54.wav",
            "DLG_GM_FRAG_TEAM_05.wav",
            "DLG_GM_FRAG_TEAM_06.wav",
        ]
    finally:
        eng.players, eng.phase, eng.mode_name, eng.results_revealed = saved
        bt_app.pending_notifications.clear()


@AUDIO
def test_new_announcer_triggers_resolve_to_real_files() -> None:
    """F10/F13/F14: the new triggers (respawn, low-life, duel setup,
    duel drawn, LMS count lines) resolve to real DLG_GM_* files, and
    TagThenShoot skips the 60s warning but keeps the 30s one."""
    gm = GameMasterAudio(emit_cb=lambda _c: None)
    assert gm.trigger_pool("respawn", force=True).filename == "DLG_GM_GLOBAL_36.wav"
    assert gm.trigger_pool("low_life", force=True).filename in (
        "DLG_GM_GLOBAL_18.wav",
        "DLG_GM_GLOBAL_19.wav",
    )
    assert gm.trigger_pool("duel_setup", force=True).filename in (
        "DLG_GM_OW_DUEL_01.wav",
        "DLG_GM_OW_DUEL_02.wav",
    )
    assert gm.trigger_pool("duel_drawn", force=True).filename == "DLG_GM_GLOBAL_118.wav"
    assert gm.trigger_pool("tower_hit", force=True).filename in (
        "DLG_GM_GLOBAL_88.wav",
        "DLG_GM_GLOBAL_89.wav",
        "DLG_GM_GLOBAL_90.wav",
    )
    for remaining in range(2, 8):
        cue = gm.play_lms_elimination(remaining)
        assert cue is not None
        assert cue.filename == f"DLG_GM_FRAG_SURVIVAL_{8 - remaining:02d}.wav"
    cue = gm.play_lms_elimination(1)
    assert cue is not None
    assert cue.filename in [f"DLG_GM_FRAG_SURVIVAL_{i:02d}.wav" for i in range(7, 13)]

    gm2 = GameMasterAudio(emit_cb=lambda _c: None)
    assert gm2.check_time_remaining(60.0, "TagThenShoot") == []
    gm3 = GameMasterAudio(emit_cb=lambda _c: None)
    cues = gm3.check_time_remaining(30.0, "TagThenShoot")
    assert [c.filename for c in cues] == ["DLG_GM_GLOBAL_46.wav"]


def test_sim_ffa_timer_respawn() -> None:
    """F17a: FreeForAll respawn is timer-only. The dead gun cannot
    shoot and no scan shortens the wait; once respawnTime elapses the
    next poll restores full life/ammo/clips."""
    eng = ArenaEngine()
    _start(eng, "FreeForAll", settings={"respawnTime": 10})
    sg = eng.sim.guns[3]
    for _ in range(5):
        eng.sim.sim_fire_shot(2, 3)
    assert sg.life_points == 0 and sg.dead_at is not None
    assert eng.sim.sim_fire_shot(3, 2)["ok"] is False
    st = eng.sim.poll_gun(3, "FreeForAll")
    assert st is not None and st.life_points == 0  # wait not elapsed
    sg.dead_at = time.time() - 11
    st = eng.sim.poll_gun(3, "FreeForAll")
    assert st is not None
    assert (sg.life_points, sg.ammo, sg.clips) == (sg.max_life, sg.max_ammo, sg.max_clips)
    assert sg.dead_at is None
    assert eng.sim.sim_fire_shot(3, 2)["ok"] is True


def test_sim_teamfrag_scan_then_timer_respawn() -> None:
    """F17b: TeamFrag respawn needs an own-base scan AND the wait. An
    enemy-base scan does nothing; with respawnTime 0 the scan restores
    at once."""
    eng = ArenaEngine()
    _start(eng, "TeamFrag", guns=4, settings={"respawnTime": 10})
    sg = eng.sim.guns[3]  # gun_num 2 -> team 2 -> own base is RF04
    for _ in range(5):
        eng.sim.sim_fire_shot(2, 3)
    assert sg.life_points == 0
    eng.sim.sim_scan_rfid(3, "RF03")  # enemy base (team 1)
    assert sg.respawn_at is None and sg.life_points == 0
    eng.sim.sim_scan_rfid(3, "RF04")  # own base: starts the wait
    assert sg.respawn_at is not None and sg.life_points == 0
    st = eng.sim.poll_gun(3, "TeamFrag")
    assert st is not None and st.life_points == 0
    sg.respawn_at = time.time() - 1
    eng.sim.poll_gun(3, "TeamFrag")
    assert (sg.life_points, sg.ammo, sg.clips) == (sg.max_life, sg.max_ammo, sg.max_clips)
    assert sg.respawn_at is None

    # respawnTime 0: the own-base scan restores immediately.
    eng2 = ArenaEngine()
    _start(eng2, "TeamFrag", guns=4, settings={"respawnTime": 0})
    sg2 = eng2.sim.guns[3]
    for _ in range(5):
        eng2.sim.sim_fire_shot(2, 3)
    eng2.sim.sim_scan_rfid(3, "RF04")
    assert sg2.life_points == sg2.max_life and sg2.respawn_at is None


def test_sim_auto_reload() -> None:
    """F17c: firing the last bullet arms the gun's auto-reload; the
    next tick consumes a clip and refills the ammo. With no clips
    left (duel) the gun stays empty."""
    eng = ArenaEngine()
    _start(eng, "StarterFrag")  # ammo 12, clips 3
    sg = eng.sim.guns[2]
    for _ in range(12):
        assert eng.sim.sim_fire_shot(2, None)["ok"] is True
    assert sg.ammo == 0 and sg.auto_reload_pending is True
    assert eng.sim.sim_fire_shot(2, None)["ok"] is False  # reloading
    eng.sim.poll_gun(2, "StarterFrag")
    assert (sg.ammo, sg.clips) == (12, 2)
    assert sg.auto_reload_pending is False

    eng2 = ArenaEngine()
    _start(eng2, "OldFashionDuel")  # ammo 6, clips 0
    sg2 = eng2.sim.guns[2]
    for _ in range(6):
        assert eng2.sim.sim_fire_shot(2, None)["ok"] is True
    assert sg2.ammo == 0 and sg2.auto_reload_pending is False
    assert eng2.sim.sim_fire_shot(2, None)["ok"] is False
    eng2.sim.poll_gun(2, "OldFashionDuel")
    assert sg2.ammo == 0  # no clips: no refill


@AUDIO
def test_duel_announces_round_winner_on_hit_and_early_fire() -> None:
    """Normal hit preempts 'Fire!' with Player X! (priority -1) + Wins the round!,
    and early fire awards +1 pt to opponent and schedules Player X! + Wins the round!
    after the full duel_cheat clip finishes."""
    eng = ArenaEngine()
    _start(eng, "OldFashionDuel")

    # Round 1: INTRO -> WAIT (flush setup intro) -> DRAW -> can_shoot -> P1 hits P2
    eng.duel_state["next_transition_at"] = 0
    eng.tick_mode_timers()  # INTRO -> WAIT
    eng.tick_pending_audio(now=time.time() + 10.0)  # flush duel_setup + OW_DUEL_03 during WAIT
    eng.duel_state["next_transition_at"] = 0
    eng.tick_mode_timers()  # WAIT -> DRAW (triggers duel_draw at priority=0)
    eng.tick_pending_audio(now=time.time() + 1.0)
    assert eng.duel_state["can_shoot"] is True

    eng.audio.recent_cues.clear()
    eng.sim.sim_fire_shot(2, 3)  # P1 (gun_num 1) hits P2
    _poll_all(eng)
    assert eng.duel_state["last_winner_gun_num"] == 1
    assert "PLAYER 1" in eng.duel_state["last_result_text"]
    assert eng.duel_state["next_transition_at"] - time.time() >= 7.5

    # Immediate cue is DLG_GM_GLOBAL_55.wav ("Player 1!") with priority=-1
    assert [c.filename for c in eng.audio.recent_cues] == ["DLG_GM_GLOBAL_55.wav"]
    assert eng.audio.recent_cues[0].priority == -1

    # Flush followup -> DLG_GM_GLOBAL_119.wav ("Wins the round!")
    eng.tick_pending_audio(now=time.time() + 3.0)
    assert [c.filename for c in eng.audio.recent_cues] == [
        "DLG_GM_GLOBAL_55.wav",
        "DLG_GM_GLOBAL_119.wav",
    ]

    # 3, 2, 1 countdown before Round 2 starts
    eng.audio.recent_cues.clear()
    for expected_cd, offset in [(3, 2.5), (2, 1.5), (1, 0.5)]:
        eng.duel_state["next_transition_at"] = time.time() + offset
        eng.tick_mode_timers()
        assert eng.duel_state["countdown_remaining"] == expected_cd
        assert eng.format_clock() == f"00:0{expected_cd}"
    assert [c.filename for c in eng.audio.recent_cues] == [
        "DLG_GM_GLOBAL_10.wav",
        "DLG_GM_GLOBAL_11.wav",
        "DLG_GM_GLOBAL_12.wav",
    ]

    # Round 2: early fire during WAIT by P1 -> P2 gets the point + full sequence
    eng.duel_state["next_transition_at"] = 0
    eng.tick_mode_timers()  # ROUND_END -> WAIT (round 2)
    eng.tick_pending_audio(now=time.time() + 2.5)  # flush duel_prepare (OW_DUEL_03) during WAIT
    assert any(c.filename == "DLG_GM_OW_DUEL_03.wav" for c in eng.audio.recent_cues)
    assert eng.duel_state["round"] == 2
    assert eng.duel_state["last_winner_gun_num"] is None
    assert eng.duel_state["last_result_text"] == ""

    eng.audio.recent_cues.clear()
    eng.sim.sim_fire_shot(2, None)  # P1 misses early
    _poll_all(eng)
    assert eng.players[3].score == 1
    assert eng.duel_state["last_winner_gun_num"] == 2
    assert "PLAYER 2" in eng.duel_state["last_result_text"]
    assert eng.duel_state["next_transition_at"] - time.time() >= 8.5

    # duel_cheat fires immediately; Player 2! + Wins the round! are queued
    assert eng.audio.recent_cues[0].filename in ("DLG_GM_OW_DUEL_08.wav", "DLG_GM_OW_DUEL_09.wav")
    eng.tick_pending_audio(now=time.time() + 10.0)
    assert [c.filename for c in eng.audio.recent_cues[1:]] == [
        "DLG_GM_GLOBAL_56.wav",
        "DLG_GM_GLOBAL_119.wav",
    ]
    assert eng.audio.recent_cues[1].priority == -1


def test_duel_tracks_shots_accuracy_h2h_and_streaks_without_clips() -> None:
    """Clip-less hardware reports (status.clips=None) in OldFashionDuel still
    track shots_fired, accuracy, hits_dealt_to, hits_taken_from, and best_streak."""
    from battletag.protocol import DecodedStatus

    eng = ArenaEngine()
    _start(eng, "OldFashionDuel")
    p1, p2 = eng.players[2], eng.players[3]

    eng.duel_state["next_transition_at"] = 0
    eng.tick_mode_timers()  # INTRO -> WAIT -> Round 1
    eng.duel_state["next_transition_at"] = 0
    eng.tick_mode_timers()  # WAIT -> DRAW
    eng.tick_pending_audio(now=time.time() + 1.0)

    # Simulate hardware status where clips is None: P1 fires 2 shots (6 -> 4)
    # and hits P2 once.
    eng.ingest_status_report(
        DecodedStatus(gun_id=2, word_count=2, send_words=[4, 0], ammo=4, clips=None, shots_fired=None, hits_received=0, hit_ids=[0, 0, 0, 0, 0])
    )
    eng.ingest_status_report(
        DecodedStatus(gun_id=3, word_count=2, send_words=[6, 1], ammo=6, clips=None, shots_fired=None, hits_received=1, hit_ids=[2, 0, 0, 0, 0])
    )
    assert p1.shots_fired == 2
    assert p1.hits_landed == 1
    assert p1.accuracy == 50.0
    assert p1.hits_dealt_to == {2: 1}
    assert p2.hits_taken_from == {1: 1}
    assert p1.best_streak == 1
