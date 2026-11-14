"""F21: duel round announcements at round transitions.

Each round opens with its announcement line -- "First round!"
(GLOBAL_115) for round 1, "Final round" (GLOBAL_116) for the last
round, "Next round!" (GLOBAL_114) for the rest -- and the setup intro
(round 1) or the per-round position calls follow once the announcement's
modeled window ends, via the pending-audio queue.

The file-selection tests record the direct file calls (no audio tree
needed); the AUDIO test asserts the emitted cues resolve to real files
when the handoff game tree is present.
"""

from __future__ import annotations

import time

import pytest

from battletag.audio import ANNOUNCER_DIR
from battletag.modes import ArenaEngine

AUDIO = pytest.mark.skipif(not ANNOUNCER_DIR.is_dir(), reason="game audio tree not present")


def _start(eng: ArenaEngine, rounds: int = 3) -> None:
    eng.set_num_simulated_guns(2)
    eng.select_mode("OldFashionDuel", {"rounds": rounds})
    eng.reset_stats_for_new_match()
    eng.phase = "PLAYING"
    eng.match_start_ts = time.time()
    for gid in sorted(eng.players.keys()):
        st = eng.sim.poll_gun(gid, eng.mode_name)
        if st is not None:
            eng.ingest_status_report(st)
    eng.audio.active_until = 0.0
    eng.audio.active_priority = 99
    eng.audio.recent_cues.clear()


def _tick(eng: ArenaEngine) -> None:
    """Force the next duel transition to fire now."""
    eng.duel_state["next_transition_at"] = time.time() - 1.0
    eng.tick_mode_timers()


def _to_wait(eng: ArenaEngine) -> None:
    _tick(eng)  # INTRO/ROUND_END -> WAIT
    assert eng.duel_state["subphase"] == "WAIT"


def _finish_round_by_timeout(eng: ArenaEngine) -> None:
    _tick(eng)  # WAIT -> DRAW
    assert eng.duel_state["subphase"] == "DRAW"
    _tick(eng)  # DRAW -> ROUND_END (timeout draw)
    assert eng.duel_state["subphase"] == "ROUND_END"


def _spy_on_round_files(eng: ArenaEngine) -> list:
    """Record direct file calls; timeout-driven rounds make no other
    play_announcer_file calls, so every capture is a round line."""
    calls = []

    def spy(wav_name, subtitle, priority=0, duration=2.0):
        calls.append((wav_name, subtitle, priority, duration))
        return None

    eng.audio.play_announcer_file = spy
    return calls


def test_round_lines_play_first_next_final_in_order() -> None:
    """F21: a 3-round duel announces First / Next / Final, in order."""
    eng = ArenaEngine()
    _start(eng, rounds=3)
    calls = _spy_on_round_files(eng)
    for _ in range(3):
        _to_wait(eng)
        _finish_round_by_timeout(eng)
    assert [c[0] for c in calls] == [
        "DLG_GM_GLOBAL_115.wav",
        "DLG_GM_GLOBAL_114.wav",
        "DLG_GM_GLOBAL_116.wav",
    ]
    assert [c[1] for c in calls] == ["First round!", "Next round!", "Final round"]
    assert all(c[2] == -1 for c in calls)


def test_setup_and_prepare_pools_follow_the_announcement() -> None:
    """F21: each transition schedules the intro/position pool as a
    followup (fired by tick_pending_audio after the round line's
    modeled window), rather than playing it at the transition."""
    eng = ArenaEngine()
    _start(eng, rounds=3)
    _spy_on_round_files(eng)
    for _ in range(3):
        _to_wait(eng)
        _finish_round_by_timeout(eng)
    pools = [
        e for e in eng._pending_audio if e.get("kind") == "pool"
    ]
    assert [e["pool_key"] for e in pools] == [
        "duel_setup",
        "duel_prepare",
        "duel_prepare",
    ]
    assert all(e.get("force") is True for e in pools)


def test_single_round_match_announces_first_round_only() -> None:
    """F21: when round 1 is also the final round, "First round!" wins
    and no further announcement plays once the match completes."""
    eng = ArenaEngine()
    _start(eng, rounds=1)
    calls = _spy_on_round_files(eng)
    _to_wait(eng)
    _finish_round_by_timeout(eng)
    _tick(eng)  # ROUND_END -> COMPLETE (no more rounds)
    assert eng.duel_state["subphase"] == "COMPLETE"
    assert [c[0] for c in calls] == ["DLG_GM_GLOBAL_115.wav"]


def test_round_one_standoff_floor_covers_both_lines() -> None:
    """F21: round 1's WAIT floor is 6 s (round line + setup intro),
    so the DRAW cue never talks over the intro; later rounds keep
    the usual 5 s floor."""
    eng = ArenaEngine()
    _start(eng, rounds=3)
    _to_wait(eng)
    remaining = eng.duel_state["next_transition_at"] - time.time()
    assert 5.9 <= remaining <= 8.0
    _finish_round_by_timeout(eng)
    _to_wait(eng)
    remaining = eng.duel_state["next_transition_at"] - time.time()
    assert 4.9 <= remaining <= 8.0


@AUDIO
def test_round_line_cues_resolve_to_real_files() -> None:
    """F21: with the game tree present, the round lines emit real
    announcer cues in First / Next / Final order."""
    eng = ArenaEngine()
    _start(eng, rounds=3)
    for _ in range(3):
        _to_wait(eng)
        _finish_round_by_timeout(eng)
    names = [c.filename for c in eng.audio.recent_cues]
    rounds = [n for n in names if n in (
        "DLG_GM_GLOBAL_114.wav",
        "DLG_GM_GLOBAL_115.wav",
        "DLG_GM_GLOBAL_116.wav",
    )]
    assert rounds == [
        "DLG_GM_GLOBAL_115.wav",
        "DLG_GM_GLOBAL_114.wav",
        "DLG_GM_GLOBAL_116.wav",
    ]
