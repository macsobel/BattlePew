"""LastManStanding mode: survival elimination with combat scoring, no respawns.

Players earn +1 point per hit landed and +3 bonus points per elimination.
Survival remains the primary win condition: alive players always rank
above eliminated players, so the last player standing always wins. If
maximum game time expires with multiple survivors, the survivor with the
most points (then remaining HP) wins.
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
        # Arena Master maximum game time cap (5/10/15/20m); the match also
        # ends immediately as soon as at most one survivor remains alive.
        "timed": True,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": False,
        "accessories": ["ammo"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/LastManStanding.png",
        "banner": "/static/assets/textures/Packages/LastManStanding/Ui/Loading_LastManStanding.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_LastManStanding.png",
        "description": "High-stakes survival! Players have limited Life Points and NO T-Base respawns. Earn +1 pt per hit and +3 bonus per elimination — the last player standing takes victory!",
        "bullets": [
            "+1 pt per hit · +3 pt Elimination bonus",
            "No T-Base respawns · limited Life & Ammo",
            "Last player standing wins",
        ],
        "defaults": {
            "durationSeconds": 300,
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "lifePoints": 25,
            "ammunitions": 9,
            "clips": 3,
            "swap": 0,
            "assist": 1,
            "medkit": 0,
        },
        "options": [
            {"key": "durationSeconds", "label": "Maximum Game Time", "choices": [300, 600, 900, 1200]},
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "lifePoints", "label": "Player Hit Points", "choices": [5, 10, 25, 50, 75, 100]},
            {"key": "ammunitions", "label": "Shots per Clip", "choices": [6, 9, 12, 20]},
            {"key": "clips", "label": "Clips (magazines)", "choices": [1, 2, 3, 4, 5]},
            {"key": "swap", "label": "Swap Boxes", "choices": [0, 1]},
        ],
    }

    finite_ammo = True
    respawn_audio = False
    no_respawn = True

    def on_status_report(
        self, ctx: ArenaEngine, player: PlayerState, status: DecodedStatus, ammo_dropped: bool
    ) -> None:
        self.on_life_status_report(ctx, player, status)

    def record_life_hits(
        self, ctx: ArenaEngine, shooters: list[int], victim: PlayerState, curr_life: int
    ) -> None:
        """Score LastManStanding hits (+1 pt/hit, +3 bonus per elimination)."""
        for sgid in shooters:
            shooter = ctx.players.get(sgid)
            if not shooter:
                continue
            ctx.last_action_ts = time.time()
            shooter.miss_streak = 0
            shooter.hits_landed += 1
            shooter.score += 1
            shooter.streak += 1
            shooter.best_streak = max(shooter.best_streak, shooter.streak)
            shooter.last_tagged_target = victim.display_name
            victim.last_tagged_by = shooter.display_name
            victim.last_tagged_by_time = time.time()
            shooter.hits_dealt_to[victim.gun_num] = shooter.hits_dealt_to.get(victim.gun_num, 0) + 1
            victim.hits_taken_from[shooter.gun_num] = victim.hits_taken_from.get(shooter.gun_num, 0) + 1
            verb = random.choice(KILL_VERBS)
            ctx.add_event(
                "hit",
                f"{shooter.display_name} {verb} {victim.display_name} (+1 pt)",
                actor=shooter,
                target=victim,
            )
            if curr_life > 0:
                if shooter.streak == 5:
                    ctx.audio.trigger_pool("streak_5", custom_subtitle=f"{shooter.display_name} is on a 5-hit streak!")
                elif shooter.streak == 3:
                    ctx.audio.trigger_pool("streak_3", custom_subtitle=f"{shooter.display_name} — 3-hit streak!")
                else:
                    ctx.audio.trigger_pool("hit_scored")
        if curr_life == 0:
            alive = [p for p in ctx.players.values() if p.life_points > 0]
            killer = ctx.players.get(shooters[0]) if shooters else None
            if killer is not None:
                killer.frags += 1
                killer.score += 3
                fverb = random.choice(FRAG_VERBS)
                ctx.add_event(
                    "frag",
                    f"ELIMINATED! {killer.display_name} {fverb} {victim.display_name} (+3 bonus)",
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
            key=lambda p: (p.life_points > 0, p.score, p.life_points, p.frags, p.hits_landed, -p.deaths),
            reverse=True,
        )

    def gameover_outcomes(self, ctx: ArenaEngine) -> dict[int, bool]:
        # Survival decides: alive players beat eliminated ones; if time expires
        # with multiple survivors, only an un-tied top-scoring (then highest-HP)
        # survivor wins green LEDs.
        alive = [p for p in ctx.players.values() if p.life_points > 0]
        if alive:
            top_key = max((p.score, p.life_points) for p in alive)
            winners = {p.gun_id for p in alive if (p.score, p.life_points) == top_key}
            return {gid: (len(winners) == 1 and gid in winners) for gid in ctx.players}
        return super().gameover_outcomes(ctx)

    def check_end_condition(self, ctx: ArenaEngine) -> bool:
        if len(ctx.players) >= 2:
            alive = [p for p in ctx.players.values() if p.life_points > 0]
            if len(alive) <= 1:
                return True
        return False


MODE_CLASS = LastManStandingMode
