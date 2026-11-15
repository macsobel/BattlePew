"""StarterFrag mode: infinite life, limited ammo reloaded by ammo cards.

StarterFrag shares the standard hits-based scoring with FreeFrag; the
only difference is finite ammo (low/empty-ammo warnings) and its
fixed settings.
"""

from __future__ import annotations

from typing import Any

from battletag.modes.base import GameMode

ORDER = 6


class StarterFragMode(GameMode):
    """Starter Frag: infinite life, scan Ammo Boxes to reload."""

    METADATA: dict[str, Any] = {
        "id": "StarterFrag",
        "title": "Starter Frag",
        "subtitle": "Infinite Life · Reloads with Ammo Cards",
        "tier": 1,
        "badge": "Card Reload",
        "timed": True,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": True,
        "accessories": ["ammo"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/StarterFrag.png",
        "banner": "/static/assets/textures/Packages/StarterFrag/Ui/Loading_StarterFrag.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_StarterFrag.png",
        "description": "Bridges Free Frag and full tactical modes: players have infinite Life Points, but limited ammunition that must be reloaded by scanning physical Ammo Boxes!",
        "bullets": [
            "Infinite Life Points",
            "Limited ammo · Scan Ammo Box to reload",
            "+1 pt per hit · Highest score wins",
        ],
        # The original fixes all three settings (playtime 5 min, ammo
        # 12, clips 3) with no UI options; only the launch mode varies.
        "defaults": {
            "durationSeconds": 300,
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "ammunitions": 12,
            "clips": 3,
            "assist": 1,
        },
        "options": [
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
        ],
    }

    finite_ammo = True


MODE_CLASS = StarterFragMode
