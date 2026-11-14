"""Tests ensuring player names are always formatted in all caps when shown to users."""

from __future__ import annotations

from battletag.modes import ArenaEngine, DecodedStatus, PlayerState


def test_player_display_name_is_always_uppercase() -> None:
    p = PlayerState(gun_num=1, gun_id=2, ref_hex="aa", name="super Finn", avatar="Dragon")
    assert p.display_name == "SUPER FINN"
    d = p.to_dict()
    assert d["display_name"] == "SUPER FINN"


def test_feed_events_contain_uppercase_names() -> None:
    eng = ArenaEngine()
    eng.players = {
        2: PlayerState(gun_num=1, gun_id=2, ref_hex="aa", name="super Finn", avatar="Dragon"),
        3: PlayerState(gun_num=2, gun_id=3, ref_hex="bb", name="Fartimus prime", avatar="Shark"),
    }
    # Test _record_hit generates uppercase names in event text and target tracking
    eng._record_hit(eng.players[3], eng.players[2], is_frag=False)
    assert "FARTIMUS PRIME" in eng.events[0].text
    assert "SUPER FINN" in eng.events[0].text
    assert eng.players[2].last_tagged_by == "FARTIMUS PRIME"
    assert eng.players[3].last_tagged_target == "SUPER FINN"

    # Test frag
    eng._record_hit(eng.players[3], eng.players[2], is_frag=True)
    assert "FRAG! FARTIMUS PRIME" in eng.events[0].text
    assert "SUPER FINN" in eng.events[0].text

    # Test add_event sanitizes custom strings with lowercase names
    ev = eng.add_event("custom", "fartimus prime took super finn's flag")
    assert "FARTIMUS PRIME" in ev.text
    assert "SUPER FINN" in ev.text

