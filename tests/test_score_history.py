from __future__ import annotations

from battletag.modes import ArenaEngine, PlayerState


def test_score_history_tracks_initial_changes_and_final_snapshot() -> None:
    eng = ArenaEngine()
    eng.players = {
        1: PlayerState(gun_id=1, gun_num=1, ref_hex="0101", name="ALPHA", avatar="Shark", team_id=1),
        2: PlayerState(gun_id=2, gun_num=2, ref_hex="0102", name="BETA", avatar="Eagle", team_id=2),
    }
    eng.reset_stats_for_new_match()
    assert len(eng.score_history) == 1
    assert eng.score_history[0]["t"] == 0.0
    assert eng.score_history[0]["scores"] == {"1": 0, "2": 0}

    eng.phase = "PLAYING"
    eng.match_start_ts = eng.match_start_ts or 1.0

    # No score change -> should not add duplicate point
    eng.record_score_snapshot()
    assert len(eng.score_history) == 1

    # Score change -> records new point
    eng.players[1].score = 3
    eng.record_score_snapshot()
    assert len(eng.score_history) == 2
    assert eng.score_history[-1]["scores"] == {"1": 3, "2": 0}

    # Snapshot exposes score_history
    snap = eng.get_snapshot()
    assert "score_history" in snap
    assert len(snap["score_history"]) == 2

