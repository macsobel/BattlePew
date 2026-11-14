from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

from battletag.app import ProfileUpdateRequest, api_update_profile, engine
from battletag.modes import PlayerState


def test_api_profile_team_only_on_game_selection_in_team_mode() -> None:
    orig_phase = engine.phase
    orig_step = engine.wizard_step
    orig_mode = engine.mode_name
    orig_players = dict(engine.players)
    try:
        engine.phase = "LOBBY"
        engine.players = {1: PlayerState(gun_id=1, gun_num=1, ref_hex="0101", name="PLAYER1", avatar="Shark", team_id=1)}

        # On lobby pairing page (wizard_step == 1), team changes are rejected.
        engine.set_wizard_step(1)
        engine.select_mode("TeamFrag")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(api_update_profile(ProfileUpdateRequest(gun_num=1, team_id=2)))
        assert exc.value.status_code == 400

        # On game selection (wizard_step == 2) in non-team mode, rejected.
        engine.set_wizard_step(2)
        engine.select_mode("FreeFrag")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(api_update_profile(ProfileUpdateRequest(gun_num=1, team_id=2)))
        assert exc.value.status_code == 400

        # On game selection (wizard_step == 2) in team mode, accepted.
        engine.select_mode("TeamFrag")
        resp = asyncio.run(api_update_profile(ProfileUpdateRequest(gun_num=1, team_id=2)))
        assert resp.status_code == 200
        assert json.loads(resp.body)["player"]["team_id"] == 2

        # During a round, rejected.
        engine.phase = "PLAYING"
        with pytest.raises(HTTPException) as exc:
            asyncio.run(api_update_profile(ProfileUpdateRequest(gun_num=1, team_id=3)))
        assert exc.value.status_code == 400
    finally:
        engine.phase = orig_phase
        engine.wizard_step = orig_step
        engine.mode_name = orig_mode
        engine.players = orig_players
