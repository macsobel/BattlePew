"""Automated tests for Battle Tag protocol bytes, status decoding, and ArenaEngine."""

from __future__ import annotations

import time

import pytest

from battletag.hardware import RealHIDTransport, load_mode_sequences
from battletag.modes import MODE_CATALOG, ArenaEngine
from battletag.protocol import (
    build_config_packet,
    build_countdown_packet,
    build_gameover_packet,
    build_poll_packet,
    decode_0x84_profile,
    decode_0x95_status,
    pad64,
)


def test_mode_sequences_integrity() -> None:
    seqs = load_mode_sequences()
    uploads = seqs["uploads"]
    expected = {
        "FreeFrag": (3, 120),
        "TeamFrag": (20, 1152),
        "TagThenShoot": (8, 439),
        "FreeForAll": (16, 877),
        "LastManStanding": (14, 801),
        "OldFashionDuel": (4, 225),
        "StarterFrag": (8, 409),
    }
    for mode, (exp_chunks, exp_bytes) in expected.items():
        up = uploads[mode]
        assert up["unlock"] == "01ff8600"
        assert up["erase"] == "04ff8100004000"
        assert up["lock"] == "01ff8601"
        chunks = up["chunks"]
        assert len(chunks) == exp_chunks
        total_len = sum(c["length"] for c in chunks)
        assert total_len == exp_bytes
        # Verify prepare packet encodes total_len in big-endian at bytes 7..9
        prep = bytes.fromhex(up["prepare"])
        assert prep[:7] == bytes.fromhex("06ff8200004000")
        assert int.from_bytes(prep[7:9], "big") == exp_bytes


def test_config_packet_builders() -> None:
    # FreeFrag: 03 <id> 92 00 03 00
    ff = build_config_packet(2, "FreeFrag", {})
    assert ff.hex() == "030292000300"

    # TeamFrag: 9 BE16 values -> size byte = 3 + 18 = 21 (0x15)
    tf = build_config_packet(
        2,
        "TeamFrag",
        {
            "lifePoints": 3,
            "ammunitions": 8,
            "clips": 5,
            "swap": 0,
            "respawnTime": 0,
            "teamFrag": 0,
            "assist": 0,
            "medkit": 0,
        },
        team_id=1,
    )
    assert tf.hex() == "150292000309000300080005000000000001000000000000"

    # TagThenShoot: 2 BE16 values -> size byte = 3 + 4 = 7 (0x07)
    tns = build_config_packet(3, "TagThenShoot", {"numberOfBase": 2, "assist": 0})
    assert tns.hex() == "07039200030200020000"


def test_countdown_poll_and_gameover_packets() -> None:
    assert build_countdown_packet(2, 10).hex() == "0102930a"
    assert build_countdown_packet(3, 3).hex() == "01039303"

    assert build_poll_packet(2, "FreeFrag").hex() == "01029500"
    assert build_poll_packet(2, "OldFashionDuel", downlink_word=1).hex() == "030295010001"

    assert build_gameover_packet(2, won=True, mode_name="FreeFrag").hex() == "060294010153443136"
    assert build_gameover_packet(3, won=False, mode_name="FreeFrag").hex() == "060394010253443136"
    assert build_gameover_packet(2, won=True, mode_name="OldFashionDuel").hex() == "060294010153443037"


def test_decode_0x95_status_freefrag_and_teamfrag() -> None:
    # Real hardware FreeFrag (2 vars -> size 0x0A, byte3 0x05): 0a <id=02> 95 05 <hits=0003> <shots=0012> <slots=03 03 03 00 00> 00
    raw_ff = pad64(bytes.fromhex("0a02950500030012030303000000"))
    st_ff = decode_0x95_status(raw_ff, "FreeFrag")
    assert st_ff is not None
    assert st_ff.gun_id == 2
    assert st_ff.hits_received == 3
    assert st_ff.shots_fired == 18
    assert st_ff.hit_ids == [3, 3, 3, 0, 0]

    # Real hardware StarterFrag (3 vars -> size 0x0C, byte3 0x07): 0c <id=02> 95 07 <hits=0002> <ammo=0005> <clips=0004> <slots=03 03 00 00 00> 00
    raw_sf = pad64(bytes.fromhex("0c029507000200050004030300000000"))
    st_sf = decode_0x95_status(raw_sf, "StarterFrag")
    assert st_sf is not None
    assert st_sf.gun_id == 2
    assert st_sf.hits_received == 2
    assert st_sf.ammo == 5
    assert st_sf.clips == 4
    assert st_sf.hit_ids == [3, 3, 0, 0, 0]

    # TeamFrag (captured 4-word format): 0e <id=03> 95 09 <life=0002> <ammo=0007> <clips=0004> <shots=0009> <slots=02 00 00 00 00> 00
    raw_tf = pad64(bytes.fromhex("0e0395090002000700040009020000000000"))
    st_tf = decode_0x95_status(raw_tf, "TeamFrag")
    assert st_tf is not None
    assert st_tf.gun_id == 3
    assert st_tf.life_points == 2
    assert st_tf.ammo == 7
    assert st_tf.clips == 4
    assert st_tf.shots_fired == 9
    assert st_tf.hit_ids == [2, 0, 0, 0, 0]

    # Real hardware 0x84 EEPROM profile packets (including trailing flash garbage, 0xFF padding, and FA8A7FFA trailer)
    p1 = decode_0x84_profile(bytes.fromhex("35028400000011300466696E6E05736E616B652E746761FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFA8A7FFAFA8A7FFA"))
    assert p1["name"] == "finn"
    assert p1["avatar"] == "Snake"
    p2 = decode_0x84_profile(bytes.fromhex("3503840000001130056161726F6E03646F672E74676136FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFA8A7FFAFA8A7FFA"))
    assert p2["name"] == "aaron"
    assert p2["avatar"] == "Dog"
    p3 = decode_0x84_profile(bytes.fromhex("3503840000001130056161726F6E056561676C652E74676159FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF0000000000000000"))
    assert p3["name"] == "aaron"
    assert p3["avatar"] == "Eagle"


def test_arena_engine_freefrag_and_teamfrag_scoring() -> None:
    eng = ArenaEngine()
    eng.select_mode("FreeFrag")
    eng.reset_stats_for_new_match()

    # Baseline poll at t=0
    for gid in (2, 3):
        st = eng.sim.poll_gun(gid, "FreeFrag")
        assert st is not None
        eng.ingest_status_report(st)

    # Gun 2 shoots and hits Gun 3 twice
    eng.sim.sim_fire_shot(2, 3)
    eng.sim.sim_fire_shot(2, 3)
    for gid in (2, 3):
        st = eng.sim.poll_gun(gid, "FreeFrag")
        assert st is not None
        eng.ingest_status_report(st)

    assert eng.players[2].hits_landed == 2
    assert eng.players[2].score == 2
    assert eng.players[3].hits_received == 2
    outcomes = eng.compute_gameover_outcomes()
    assert outcomes == {2: True, 3: False}

    # Tie game -> both False (red LED)
    eng.sim.sim_fire_shot(3, 2)
    eng.sim.sim_fire_shot(3, 2)
    for gid in (2, 3):
        st = eng.sim.poll_gun(gid, "FreeFrag")
        assert st is not None
        eng.ingest_status_report(st)
    assert eng.compute_gameover_outcomes() == {2: False, 3: False}


def test_hardware_write_protection() -> None:
    hw = RealHIDTransport(confirm_write=False)
    with pytest.raises(PermissionError):
        hw.open()
    with pytest.raises(PermissionError):
        hw.write_packet(bytes([0x00, 0xFF, 0x05]))


def test_all_seven_tier1_modes_and_profiles() -> None:
    eng = ArenaEngine()
    for mode_id in (
        "FreeFrag",
        "TeamFrag",
        "FreeForAll",
        "LastManStanding",
        "OldFashionDuel",
        "StarterFrag",
        "TagThenShoot",
    ):
        eng.select_mode(mode_id)
        eng.reset_stats_for_new_match()
        snap = eng.get_snapshot()
        assert snap["mode"]["id"] == mode_id
        assert len(snap["players"]) == 2

    updated = eng.update_player_profile(1, name="AARON", avatar="Eagle", team_id=2)
    assert updated is not None
    assert updated.name == "AARON"
    assert updated.avatar == "Eagle"
    assert updated.team_id == 2


def test_eight_player_support_and_duel_tagnshoot() -> None:
    eng = ArenaEngine()
    eng.set_num_simulated_guns(8)
    assert len(eng.players) == 8
    assert sorted(eng.players.keys()) == [2, 3, 4, 5, 6, 7, 8, 9]

    # Verify hardware sync with 8 paired guns preserves custom profile on Gun #1
    eng.update_player_profile(1, name="CAPTAIN", avatar="Dragon", team_id=1)
    paired_8 = [
        {"gun_num": i, "gun_id": i + 1, "ref_hex": f"021A02003044E90{i}", "name": f"HW {i}", "avatar": "Bear"}
        for i in range(1, 9)
    ]
    eng.sync_paired_hardware_guns(paired_8)
    assert len(eng.players) == 8
    assert eng.players[2].name == "CAPTAIN"
    assert eng.players[9].gun_num == 8

    # Verify TagThenShoot 0xC3 UbiConnect hit ingestion during the
    # active player's timed run (scoring is gated to live runs).
    eng.select_mode("TagThenShoot")
    eng.reset_stats_for_new_match()
    eng.phase = "PLAYING"
    eng._turn_triggered = True
    eng.tick_race_turns()
    eng.race_state["countdown_ends_at"] = time.time() - 1.0
    eng.tick_race_turns()
    assert eng.race_state["turn_phase"] == "RUNNING"
    eng.ingest_ubiconnect_hits({2: 3})
    assert eng.players[2].score == 3
    assert eng.players[2].frags == 3


