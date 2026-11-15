"""LastManStanding mode: survival elimination, no respawns.

Hits and kills award no points — only counters. While alive a
player's score is life + #players; on elimination it becomes
#players - #alive. The last player standing wins.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Any

from battletag.modes.base import FRAG_VERBS, KILL_VERBS, GameMode
from battletag.protocol import DecodedStatus

if TYPE_CHECKING:
    from battletag.modes.engine import ArenaEngine, PlayerState

ORDER = 4


class LastManStandingMode(GameMode):
    """Last Man Standing: limited life, no respawns, final survivor wins."""

    METADATA: dict[str, Any] = {
        "id": "LastManStanding",
        "title": "Last Man Standing",
        "subtitle": "Survival Elimination · No Respawns · Final Survivor Wins",
        "tier": 1,
        "badge": "Elimination",
        # The original has no timer: the match ends when one player is
        # left alive. No duration setting exists for this mode.
        "timed": False,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": False,
        "accessories": ["ammo"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/LastManStanding.png",
        "banner": "/static/assets/textures/Packages/LastManStanding/Ui/Loading_LastManStanding.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_LastManStanding.png",
        "description": "High-stakes survival! Players have limited Life Points and NO T-Base respawns. Once your HP hits 0, you are eliminated — the last player standing takes victory!",
        "bullets": [
            "Limited Life Points & ammo",
            "No T-Base respawns",
            "Last player standing wins",
        ],
        "defaults": {
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "lifePoints": 5,
            "ammunitions": 9,
            "clips": 3,
            "swap": 0,
            "assist": 1,
            "medkit": 0,
        },
        "options": [
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "lifePoints", "label": "Starting Lives", "choices": [3, 5, 7, 9]},
            {"key": "ammunitions", "label": "Shots per Clip", "choices": [6, 9, 12, 20]},
            {"key": "clips", "label": "Clips (magazines)", "choices": [1, 2, 3, 4, 5]},
            {"key": "swap", "label": "Swap Boxes", "choices": [0, 1]},
        ],
    }

    finite_ammo = True
    respawn_audio = False
    no_respawn = True

    def initial_score(
        self, ctx: ArenaEngine, player: PlayerState, max_life: int, num_players: int
    ) -> int:
        # The original survival formula sets an alive player's score
        # to life_points + num_players; initialize it at match start
        # so an untouched survivor does not stay at 0.
        return max_life + num_players

    def on_status_report(
        self, ctx: ArenaEngine, player: PlayerState, status: DecodedStatus, ammo_dropped: bool
    ) -> None:
        self.on_life_status_report(ctx, player, status)

    def record_life_hits(
        self, ctx: ArenaEngine, shooters: list[int], victim: PlayerState, curr_life: int
    ) -> None:
        """Score LastManStanding hits by the original's survival formula.

        Hits and kills award no points — only counters. While alive a
        player's score is life + #players, recomputed when their life
        drops; on elimination it becomes #players - #alive, set once at
        the moment of elimination. Healing never raises the score: the
        recompute lives in the life-decrease branch only, as in the
        original's RoundLoop (a quirk, preserved).
        """
        num_players = len(ctx.players)
        for sgid in shooters:
            shooter = ctx.players.get(sgid)
            if not shooter:
                continue
            ctx.last_action_ts = time.time()
            shooter.miss_streak = 0
            shooter.hits_landed += 1
            shooter.last_tagged_target = victim.display_name
            victim.last_tagged_by = shooter.display_name
            victim.last_tagged_by_time = time.time()
            shooter.hits_dealt_to[victim.gun_num] = shooter.hits_dealt_to.get(victim.gun_num, 0) + 1
            victim.hits_taken_from[shooter.gun_num] = victim.hits_taken_from.get(shooter.gun_num, 0) + 1
            verb = random.choice(KILL_VERBS)
            ctx.add_event(
                "hit",
                f"{shooter.display_name} {verb} {victim.display_name}",
                actor=shooter,
                target=victim,
            )
            if curr_life > 0:
                ctx.audio.trigger_pool("hit_scored")
        if curr_life > 0:
            victim.score = victim.life_points + num_players
        else:
            alive = [p for p in ctx.players.values() if p.life_points > 0]
            victim.score = num_players - len(alive)
            killer = ctx.players.get(shooters[0]) if shooters else None
            if killer is not None:
                killer.frags += 1
                fverb = random.choice(FRAG_VERBS)
                ctx.add_event(
                    "frag",
                    f"ELIMINATED! {killer.display_name} {fverb} {victim.display_name}",
                    actor=killer,
                    target=victim,
                )
            else:
                ctx.add_event(
                    "frag",
                    f"ELIMINATED! {victim.display_name} is out!",
                    actor=victim,
                )
            ctx.audio.play_lms_elimination(
                len(alive),
                subtitle=f"{victim.display_name} is eliminated! {len(alive)} left!",
            )
        ctx._check_lead_change()

    def sort_players(self, ctx: ArenaEngine, players: list[PlayerState]) -> list[PlayerState]:
        return sorted(
            players,
            key=lambda p: (p.life_points > 0, p.life_points, p.score, p.hits_landed, -p.deaths),
            reverse=True,
        )

    def gameover_outcomes(self, ctx: ArenaEngine) -> dict[int, bool]:
        # Survival decides: every player still alive gets winner
        # LEDs regardless of score, so the sole survivor is never
        # shown red behind an eliminated player's higher score. If
        # somehow nobody is alive, fall back to the score rule.
        alive = {gid for gid, p in ctx.players.items() if p.life_points > 0}
        if alive:
            return {gid: gid in alive for gid in ctx.players}
        return super().gameover_outcomes(ctx)

    def check_end_condition(self, ctx: ArenaEngine) -> bool:
        if len(ctx.players) >= 2:
            alive = [p for p in ctx.players.values() if p.life_points > 0]
            if len(alive) <= 1:
                return True
        return False


MODE_CLASS = LastManStandingMode
