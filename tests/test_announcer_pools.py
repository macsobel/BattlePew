"""Announcer pool mapping: every pool must name files that exist in the
extracted game audio, and every pool must emit a cue. Skipped when the
handoff game tree is absent (e.g. a bare checkout without the assets)."""

import pytest

from battletag.audio import ANNOUNCER_DIR, ANNOUNCER_POOLS, GameMasterAudio

pytestmark = pytest.mark.skipif(
    not ANNOUNCER_DIR.is_dir(), reason="game audio tree not present"
)


def test_all_pool_files_exist():
    missing = [
        (key, f)
        for key, pool in ANNOUNCER_POOLS.items()
        for f in pool["files"]
        if not (ANNOUNCER_DIR / f).exists()
    ]
    assert missing == []


def test_countdown_number_files_exist():
    for n in range(1, 11):
        assert (ANNOUNCER_DIR / f"DLG_GM_GLOBAL_{13 - n:02d}.wav").exists()


def test_every_pool_emits_a_cue():
    gm = GameMasterAudio(emit_cb=lambda _cue: None)
    for key in ANNOUNCER_POOLS:
        cue = gm.trigger_pool(key, force=True)
        assert cue is not None, key
        assert cue.url.startswith("/audio/announcer/DLG_GM_")


def test_countdown_numbers_emit():
    gm = GameMasterAudio(emit_cb=lambda _cue: None)
    for n in (1, 5, 10):
        cue = gm.play_countdown_number(n)
        assert cue is not None
        assert cue.filename == f"DLG_GM_GLOBAL_{13 - n:02d}.wav"


def test_no_mode_intro_lines_exist():
    # No spoken mode names in the extracted set; mode selection stays silent.
    gm = GameMasterAudio(emit_cb=lambda _cue: None)
    assert gm.play_mode_intro("TeamFrag") is None
    assert gm.play_mode_intro("FreeFrag") is None


def test_winner_announcer_files_exist_and_emit():
    cues = []
    gm = GameMasterAudio(emit_cb=cues.append)
    expected = (
        ["DLG_GM_GLOBAL_53.wav", "DLG_GM_GLOBAL_54.wav"]
        + [f"DLG_GM_GLOBAL_{54 + i:02d}.wav" for i in range(1, 9)]
        + [f"DLG_GM_FRAG_TEAM_{i:02d}.wav" for i in range(5, 9)]
    )
    for fname in expected:
        cue = gm.play_announcer_file(fname, subtitle=fname)
        assert cue is not None, fname
        assert cue.filename == fname
        assert cue.url == f"/audio/announcer/{fname}"
    assert len(cues) == len(expected)


def test_reveal_results_triggers_winner_announcement():
    import asyncio
    from battletag import app as bt_app
    from battletag.modes import PlayerState

    eng = bt_app.engine
    orig_players = dict(eng.players)
    orig_phase = eng.phase
    orig_mode = eng.mode_name
    orig_revealed = eng.results_revealed
    try:
        # Solo winner (P2)
        eng.mode_name = "FreeFrag"
        eng.phase = "GAME_OVER"
        eng.results_revealed = False
        eng.players = {
            1: PlayerState(gun_num=1, gun_id=1, ref_hex="0101", name="ALPHA", avatar="Shark", score=5),
            2: PlayerState(gun_num=2, gun_id=2, ref_hex="0102", name="BRAVO", avatar="Eagle", score=12),
        }
        bt_app.pending_notifications.clear()
        asyncio.run(bt_app.api_reveal_results())
        assert eng.results_revealed is True
        filenames = [c.filename for c in eng.audio.recent_cues[-1:]]
        assert filenames == ["DLG_GM_GLOBAL_53.wav"]

        # Team winner (Blue team, team_id=2)
        eng.mode_name = "TeamFrag"
        eng.results_revealed = False
        eng.players = {
            1: PlayerState(gun_num=1, gun_id=1, ref_hex="0101", name="ALPHA", avatar="Shark", team_id=1, score=6),
            2: PlayerState(gun_num=2, gun_id=2, ref_hex="0102", name="BRAVO", avatar="Eagle", team_id=2, score=10),
        }
        bt_app.pending_notifications.clear()
        asyncio.run(bt_app.api_reveal_results())
        assert eng.audio.recent_cues[-1].filename == "DLG_GM_GLOBAL_54.wav"
    finally:
        eng.players = orig_players
        eng.phase = orig_phase
        eng.mode_name = orig_mode
        eng.results_revealed = orig_revealed
        bt_app.pending_notifications.clear()
