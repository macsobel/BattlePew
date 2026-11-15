"""TeamFrag mode: 2-4 teams, life points, T-Base respawn, frag bonuses.

Life-based scoring with team standings, friendly-fire checks, team
T-Base respawns, and team game-over outcomes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from battletag.modes.base import GameMode
from battletag.protocol import DecodedStatus

if TYPE_CHECKING:
    from battletag.modes.engine import ArenaEngine, PlayerState

ORDER = 2


class TeamFragMode(GameMode):
    """Team Frag: squad up, +1 per hit, +3 frag bonus, T-Base respawn."""

    METADATA: dict[str, Any] = {
        "id": "TeamFrag",
        "title": "Team Frag",
        "subtitle": "2–4 Teams · Life Points · T-Base Respawn",
        "tier": 1,
        "badge": "Team Battle",
        "timed": True,
        "min_players": 2,
        "max_players": 8,
        "teams": True,
        "needs_accessories": True,
        "accessories": ["ammo", "tbase", "medkit"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/TeamFrag.png",
        "banner": "/static/assets/textures/Packages/TeamFrag/Ui/Loading_TeamFrag.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_TeamFrag.png",
        "description": "Squad up into Red, Blue, Yellow, or Green teams! Every hit scores +1 point and depletes enemy Life Points; knocking a player to 0 HP scores a +3 Frag bonus. Eliminated players scan their team's T-Base (RF03–RF06) to respawn!",
        "bullets": [
            "Red, Blue, Yellow & Green teams",
            "+1 pt per hit · +3 pt Frag bonus",
            "Scan team T-Base to respawn",
        ],
        "defaults": {
            "durationSeconds": 180,
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "lifePoints": 5,
            "ammunitions": 9,
            "clips": 5,
            "swap": 0,
            "respawnTime": 0,
            "teamFrag": 0,
            "assist": 1,
            "medkit": 1,
        },
        "options": [
            {"key": "durationSeconds", "label": "Match Duration", "choices": [120, 180, 300, 600, 900, 1200]},
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "lifePoints", "label": "Life Points", "choices": [3, 5, 7, 9]},
            {"key": "ammunitions", "label": "Shots per Clip", "choices": [6, 9, 12, 20]},
            {"key": "clips", "label": "Clips (magazines)", "choices": [1, 3, 5, 6]},
            {"key": "teamFrag", "label": "Friendly Fire", "choices": [0, 1]},
            {"key": "medkit", "label": "Medkit Boxes", "choices": [0, 1]},
            {"key": "swap", "label": "Swap Boxes", "choices": [0, 1]},
            {"key": "respawnTime", "label": "Respawn Delay", "choices": [0, 3, 5, 10]},
        ],
    }

    finite_ammo = True
    respawn_uses_team_base = True

    def on_status_report(
        self, ctx: ArenaEngine, player: PlayerState, status: DecodedStatus, ammo_dropped: bool
    ) -> None:
        self.on_life_status_report(ctx, player, status)

    def record_hit(
        self, ctx: ArenaEngine, shooter: PlayerState, victim: PlayerState, is_frag: bool = False
    ) -> None:
        # Check friendly fire in TeamFrag
        if (
            shooter.team_id == victim.team_id
            and shooter.gun_id != victim.gun_id
        ):
            ctx.add_event(
                "hit",
                f"Friendly fire! {shooter.display_name} hit teammate {victim.display_name}",
                actor=shooter,
                target=victim,
            )
            ctx.audio.trigger_pool("friendly_fire")
            return
        super().record_hit(ctx, shooter, victim, is_frag=is_frag)

    def gameover_outcomes(self, ctx: ArenaEngine) -> dict[int, bool]:
        """Team outcomes: every member of the outright top team wins;
        a tied top score means nobody wins."""
        teams = ctx.get_team_standings()
        if not teams:
            return {gid: False for gid in ctx.players}
        top_score = teams[0]["score"]
        top_teams = [t["team_id"] for t in teams if t["score"] == top_score]
        is_tie = len(top_teams) > 1
        return {
            gid: (not is_tie and p.team_id == top_teams[0])
            for gid, p in ctx.players.items()
        }


MODE_CLASS = TeamFragMode
