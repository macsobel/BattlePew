"""FreeFrag mode: instant action, infinite life and ammo, +1 per hit.

FreeFrag IS the default combat behavior, so this module carries only
its metadata — every hook is inherited from :class:`GameMode`.
"""

from __future__ import annotations

from typing import Any

from battletag.modes.base import GameMode

ORDER = 1


class FreeFragMode(GameMode):
    """Free Frag: highest score when the timer hits 0:00 wins."""

    METADATA: dict[str, Any] = {
        "id": "FreeFrag",
        "title": "Free Frag",
        "subtitle": "Instant Action · Infinite Ammo · No Reloads",
        "tier": 1,
        "badge": "Arcade Action",
        "timed": True,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": False,
        "accessories": [],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/FreeFrag.png",
        "banner": "/static/assets/textures/Packages/FreeFrag/Ui/Loading_FreeFrag.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_FreeFrag.png",
        "description": "Pure laser tag action with zero setup cards required. Infinite life and infinite ammo — every hit landed scores +1 point. Highest score when the timer hits 0:00 wins!",
        "bullets": [
            "Infinite life & infinite ammo",
            "No setup boxes required",
            "+1 pt per hit · Highest score wins",
        ],
        "defaults": {
            "durationSeconds": 180,
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "scoreLimit": 0,
        },
        "options": [
            {"key": "durationSeconds", "label": "Match Duration", "choices": [120, 180, 300, 600, 900, 1200]},
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "scoreLimit", "label": "Hit Limit (0 = Timed)", "choices": [0, 10, 20, 30, 50]},
        ],
    }


MODE_CLASS = FreeFragMode
