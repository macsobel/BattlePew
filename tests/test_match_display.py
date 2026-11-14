"""Tests for match-screen display data: kill-feed gun numbers (laser blasts)
and unresponsive-gun signal tracking."""

from __future__ import annotations

import time

from battletag.modes import ArenaEngine


def _two_player_engine() -> ArenaEngine:
    eng = ArenaEngine()
    eng.select_mode("FreeFrag")
    eng.reset_stats_for_new_match()
    for gid in (2, 3):
        st = eng.sim.poll_gun(gid, "FreeFrag")
        assert st is not None
        eng.ingest_status_report(st)
    return eng


def test_hit_event_carries_actor_and_target_gun_numbers() -> None:
    eng = _two_player_engine()
    eng.sim.sim_fire_shot(2, 3)
    for gid in (2, 3):
        st = eng.sim.poll_gun(gid, "FreeFrag")
        assert st is not None
        eng.ingest_status_report(st)
    hits = [e for e in eng.events if e.kind == "hit"]
    assert hits, "expected a hit event"
    d = hits[0].to_dict()
    assert d["actor_gun_num"] == eng.players[2].gun_num
    assert d["target_gun_num"] == eng.players[3].gun_num
    assert d["actor_name"] == eng.players[2].name
    assert d["icon_url"].endswith("/Hud_1.png")

    eng.sim.sim_fire_shot(3, 2)
    for gid in (2, 3):
        st = eng.sim.poll_gun(gid, "FreeFrag")
        assert st is not None
        eng.ingest_status_report(st)
    hits2 = [e for e in eng.events if e.kind == "hit"]
    assert len(hits2) >= 2
    d2 = hits2[0].to_dict()
    assert d2["actor_gun_num"] == eng.players[3].gun_num
    assert d2["icon_url"].endswith("/Hud_2.png")


def test_signal_lost_false_after_ingest_true_after_silence() -> None:
    eng = _two_player_engine()
    p = eng.players[2]
    assert p.to_dict()["signal_lost"] is False
    p.last_status_ts = time.time() - 5.0
    assert p.to_dict()["signal_lost"] is True
    p.last_status_ts = time.time()
    assert p.to_dict()["signal_lost"] is False


def test_signal_lost_false_before_first_report_of_match() -> None:
    eng = _two_player_engine()
    eng.reset_stats_for_new_match()
    for p in eng.players.values():
        assert p.last_status_ts == 0.0
        assert p.to_dict()["signal_lost"] is False
