"""Tests for empty hardware lobby startup and round-start readiness gates."""

from __future__ import annotations

import asyncio
import importlib
import sys

import pytest
from fastapi import HTTPException

from battletag.modes import ArenaEngine, PlayerState


def test_hardware_startup_starts_with_empty_roster(monkeypatch) -> None:
    monkeypatch.setenv("BATTLETAG_CONFIRM_WRITE", "1")
    sys.modules.pop("battletag.app", None)
    app_mod = importlib.import_module("battletag.app")
    try:
        assert app_mod.engine.use_simulator is False
        assert app_mod.engine.players == {}
    finally:
        sys.modules.pop("battletag.app", None)


def test_match_start_requires_min_players_and_connected_vests(monkeypatch) -> None:
    import battletag.app as app_mod

    eng = ArenaEngine()
    eng.use_simulator = True
    eng.mode_name = "FreeFrag"  # min_players = 2
    eng.players = {
        2: PlayerState(gun_num=1, gun_id=2, ref_hex="a1", name="P1", avatar="Dragon", vest_ok=True, gun_online=True),
    }
    monkeypatch.setattr(app_mod, "engine", eng)

    with pytest.raises(HTTPException) as exc1:
        asyncio.run(app_mod.api_start_match())
    assert exc1.value.status_code == 400
    assert "at least 2" in exc1.value.detail

    eng.players[3] = PlayerState(
        gun_num=2, gun_id=3, ref_hex="b2", name="P2", avatar="Shark", vest_ok=False, gun_online=True
    )
    with pytest.raises(HTTPException) as exc2:
        asyncio.run(app_mod.api_start_match())
    assert exc2.value.status_code == 400
    assert "vests connected" in exc2.value.detail
