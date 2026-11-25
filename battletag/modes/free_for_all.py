"""FreeForAll mode: every player for themselves, life + clips + respawn.

Life-based scoring like TeamFrag but solo: no teams, neutral respawn
wording, standard top-score outcomes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from battletag.modes.base import GameMode
from battletag.protocol import DecodedStatus

if TYPE_CHECKING:
    from battletag.modes.engine import ArenaEngine, PlayerState

ORDER = 3


class FreeForAllMode(GameMode):
    """Free For All: solo fight, +1 per hit, +3 frag bonus."""

    METADATA: dict[str, Any] = {
        "id": "FreeForAll",
        "title": "Free For All",
        "subtitle": "Every Player for Themselves · Life + Clips + Respawn",
        "tier": 1,
        "badge": "Free For All",
        "timed": True,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": True,
        "accessories": ["ammo", "medkit"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/FreeForAll.png",
        "banner": "/static/assets/textures/Packages/FreeForAll/Ui/Loading_FreeForAll.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_FreeForAll.png",
        "description": "Every player for themselves! 2–8 players fight solo with Life Points, Ammo clips, and automatic timed respawn. Score +1 per hit landed and +3 bonus per Frag.",
        "bullets": [
            "Every player for themselves",
            "+1 pt per hit · +3 pt Frag bonus",
            "Limited life · Optional ammo/medkits · Timed respawn",
        ],
        "defaults": {
            "durationSeconds": 180,
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "lifePoints": 5,
            "ammoBox": 1,
            "ammunitions": 9,
            "clips": 3,
            "swap": 0,
            "respawnTime": 0,
            "assist": 1,
            "medkit": 1,
        },
        "options": [
            {"key": "durationSeconds", "label": "Match Duration", "choices": [120, 180, 300, 600, 900, 1200]},
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "lifePoints", "label": "Life Points", "choices": [3, 5, 7, 9]},
            {"key": "ammoBox", "label": "Ammo Boxes", "choices": [0, 1]},
            {"key": "ammunitions", "label": "Shots per Clip", "choices": [6, 9, 12, 20]},
            {"key": "clips", "label": "Clips (magazines)", "choices": [1, 2, 3, 4, 5, 6]},
            {"key": "medkit", "label": "Medkit Boxes", "choices": [0, 1]},
            {"key": "swap", "label": "Swap Boxes", "choices": [0, 1]},
            {"key": "respawnTime", "label": "Respawn Delay", "choices": [0, 3, 5, 10]},
        ],
    }

    finite_ammo = True

    def on_status_report(
        self, ctx: ArenaEngine, player: PlayerState, status: DecodedStatus, ammo_dropped: bool
    ) -> None:
        self.on_life_status_report(ctx, player, status)


MODE_CLASS = FreeForAllMode
