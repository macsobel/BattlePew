"""OldFashionDuel mode: 2-player Wild West showdown across rounds.

Purely round-driven with no timer: a standoff (WAIT), the DRAW signal,
one hit deciding the round, and round-win announcements. Player 1
pulls the trigger to start the match (the app loop's WAIT_TRIGGER
wait); rounds then follow automatically. Firing before FIRE awards
the round point to the opponent.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, Any

from battletag.modes.base import GameMode
from battletag.protocol import DecodedStatus, count_new_shooters

if TYPE_CHECKING:
    from battletag.modes.engine import ArenaEngine, PlayerState

# Duel: seconds after the "Fire!" cue starts before shots count as legal
# (covers browser audio start latency and the gun's poll interval).
DUEL_FIRE_GRACE_S = 0.35

ORDER = 5


class OldFashionDuelMode(GameMode):
    """Wild West Duel: 1v1 quickdraw, 6 bullets per round."""

    METADATA: dict[str, Any] = {
        "id": "OldFashionDuel",
        "title": "Wild West Duel",
        "subtitle": "2-Player Showdown · 5 Rounds · 6 Bullets per Round",
        "tier": 1,
        "badge": "Quickdraw",
        # The original runs purely on rounds with no timer at all.
        "timed": False,
        "min_players": 2,
        "max_players": 2,
        "teams": False,
        "needs_accessories": False,
        "accessories": [],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/OldFashionDuel.png",
        "banner": "/static/assets/textures/Packages/OldFashionDuel/Ui/Loading_OldFashionDuel.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_OldFashionDuel.png",
        "description": "Classic 2-player showdown across 3, 5, or 7 tense rounds! Player 1 pulls the trigger to start the match. Point your T-Blaster down during the 3–20s standoff; when the announcer yells FIRE, shoot first — you have 6 bullets per round. Firing before the signal gives the round point to your opponent!",
        "bullets": [
            "1v1 quickdraw · Player 1 pulls trigger to start",
            "6 bullets per round · First hit after FIRE wins +1 pt",
            "Firing before FIRE awards the point to your opponent",
        ],
        "defaults": {
            "countdownSeconds": 5,
            "rounds": 5,
        },
        "options": [
            {"key": "rounds", "label": "Number of Rounds", "choices": [3, 5, 7]},
        ],
    }

    ammo_card_events = False
    idle_banter_enabled = False
    disconnect_policy = "mode_critical"

    def ammo_caps(self, ctx: ArenaEngine) -> tuple[int, int]:
        return (6, 0)

    def on_match_start(self, ctx: ArenaEngine) -> None:
        ctx._duel_round_triggered = False
        ctx.duel_state = {
            "round": 0,
            "max_rounds": int(ctx.settings.get("rounds", 5)),
            "phase_code": 2,
            "subphase": "INTRO",
            "can_shoot": False,
            "next_transition_at": time.time() + 3.0,
            "round_scores": {gid: 0 for gid in ctx.players},
            "round_results": [],
            "last_result_text": "",
            "last_winner_gun_num": None,
            "round_started_at": 0.0,
            "round_ended_at": 0.0,
            "trigger_armed": False,
        }
        ctx.race_state = {}

    def on_lead_check(self, ctx: ArenaEngine) -> None:
        # Duel has no lead concept: rounds are announced by the
        # round-win callout + GLOBAL_119, and a generic lead-change
        # line would interrupt that sequence.
        return None

    def on_timer_tick(self, ctx: ArenaEngine) -> None:
        """Advance duel round subphases (`INTRO` -> `WAIT` -> `DRAW` -> `ROUND_END` -> next round `WAIT`)."""
        if ctx.phase != "PLAYING":
            return
        now = time.time()
        sub = ctx.duel_state.get("subphase", "INTRO")
        max_rnd = int(ctx.duel_state.get("max_rounds", 5))
        curr_rnd = int(ctx.duel_state.get("round", 0))
        if now >= ctx.duel_state.get("next_transition_at", 0.0):
            if sub in ("INTRO", "ROUND_END"):
                # The match itself starts on a manual trigger pull
                # (WAIT_TRIGGER phase); rounds then follow one another
                # automatically with fresh ammo.
                if curr_rnd + 1 > max_rnd:
                    ctx.duel_state["subphase"] = "COMPLETE"
                    return
                self._begin_duel_round(ctx, curr_rnd + 1, max_rnd, now)
            elif sub == "WAIT":
                ctx.duel_state["subphase"] = "DRAW"
                ctx.duel_state["phase_code"] = 1
                # The clear "Fire!" clip (GLOBAL_83, 0.87 s) is the
                # signal. Shooting opens a short grace after the cue
                # starts (browser playback latency) instead of at the
                # end of the line, so a player reacting to the word is
                # never penalized as early.
                ctx.duel_state["can_shoot"] = False
                ctx.duel_state["next_transition_at"] = now + 8.0
                ctx.audio.trigger_pool("duel_draw", force=True)
                ctx._schedule_audio_followup(DUEL_FIRE_GRACE_S, "duel_can_shoot")
            elif sub == "DRAW":
                # Timeout without a hit -> round draw
                ctx.duel_state["subphase"] = "ROUND_END"
                ctx.duel_state["phase_code"] = 2
                ctx.duel_state["can_shoot"] = False
                ctx.duel_state["round_ended_at"] = now
                ctx.duel_state["next_transition_at"] = now + 7.0
                ctx.duel_state["last_winner_gun_num"] = None
                ctx.duel_state["last_result_text"] = (
                    f"ROUND {ctx.duel_state['round']}: DRAW — NO POINT AWARDED"
                )
                self._record_duel_round_result(ctx, None)
                ctx.add_event("duel", f"Round {ctx.duel_state['round']} ended in a draw!")
                ctx.audio.trigger_pool("duel_drawn", force=True)

    def on_followup(self, ctx: ArenaEngine, entry: dict[str, Any]) -> bool:
        if entry["kind"] == "duel_can_shoot":
            # Only arm shooting while the round is still in DRAW;
            # a hit that already resolved the round must not
            # re-arm it.
            if ctx.duel_state.get("subphase") == "DRAW":
                ctx.duel_state["can_shoot"] = True
            return True
        return False

    def check_end_condition(self, ctx: ArenaEngine) -> bool:
        return ctx.duel_state.get("subphase") == "COMPLETE"

    def display_state(self, ctx: ArenaEngine) -> dict[str, Any]:
        return {"duel_state": ctx.duel_state, "race_state": None, "race_session_best": None}

    def _record_duel_round_result(self, ctx: ArenaEngine, winner: PlayerState | None) -> None:
        """Record one finished duel round for the round display.

        `winner` is the round-winning player, or None for a draw.
        Totals in round_scores and the per-round history in
        round_results stay in step.
        """
        rnd = int(ctx.duel_state.get("round", 0))
        if winner is not None:
            scores = ctx.duel_state.setdefault("round_scores", {})
            scores[winner.gun_id] = scores.get(winner.gun_id, 0) + 1
        ctx.duel_state.setdefault("round_results", []).append(
            {"round": rnd, "winner_gun_num": winner.gun_num if winner else None}
        )

    def _reset_duel_ammo(self, ctx: ArenaEngine) -> None:
        """Reset all duelists' ammo to 6 bullets for the next round."""
        for p in ctx.players.values():
            p.ammo = 6
            p.prev_ammo = 6
        if ctx.use_simulator:
            for sg in ctx.sim.guns.values():
                sg.ammo = 6

    def _begin_duel_round(self, ctx: ArenaEngine, next_rnd: int, max_rnd: int, now: float) -> None:
        """Enter WAIT for round `next_rnd` with fresh ammo and round audio."""
        ctx.duel_state["round"] = next_rnd
        ctx.duel_state["phase_code"] = 1
        ctx.duel_state["subphase"] = "WAIT"
        ctx.duel_state["can_shoot"] = False
        ctx.duel_state["round_started_at"] = now
        ctx.duel_state["round_ended_at"] = 0.0
        ctx.duel_state["last_result_text"] = ""
        ctx.duel_state["last_winner_gun_num"] = None
        # Round 1 carries the setup intro after the round announcement, so
        # its standoff floor covers both lines (6 s) and the DRAW cue never
        # talks over the intro; later rounds use a weighted 3–20 s range
        # (15% quick 3–5 s, 10% tense 15–20 s, 75% standard 5–15 s).
        roll = random.random()
        if next_rnd == 1:
            wait_s = random.uniform(15.0, 20.0) if roll < 0.10 else random.uniform(6.0, 15.0)
        elif roll < 0.15:
            wait_s = random.uniform(3.0, 5.0)
        elif roll < 0.25:
            wait_s = random.uniform(15.0, 20.0)
        else:
            wait_s = random.uniform(5.0, 15.0)
        ctx.duel_state["next_transition_at"] = now + wait_s
        self._reset_duel_ammo(ctx)
        if next_rnd == 1:
            round_file, round_sub, round_dur = ("DLG_GM_GLOBAL_115.wav", "First round!", 1.2)
        elif next_rnd >= max_rnd:
            round_file, round_sub, round_dur = ("DLG_GM_GLOBAL_116.wav", "Final round", 1.4)
        else:
            round_file, round_sub, round_dur = ("DLG_GM_GLOBAL_114.wav", "Next round!", 1.1)
        ctx.audio.play_ui("Validation.wav")
        ctx.add_event(
            "duel",
            f"ROUND {next_rnd} OF {max_rnd} — Hold fire and wait for the FIRE! signal!",
        )
        cue = ctx.audio.play_announcer_file(round_file, round_sub, priority=-1, duration=round_dur)
        delay = (max(0.1, ctx.audio.active_until - now) + 0.25) if cue is not None else 0.1
        ctx._schedule_audio_followup(
            delay,
            "pool",
            pool_key="duel_setup" if next_rnd == 1 else "duel_prepare",
            force=True,
        )

    def on_status_report(
        self, ctx: ArenaEngine, player: PlayerState, status: DecodedStatus, ammo_dropped: bool
    ) -> None:
        curr_hits = status.hits_received or 0
        rnd = int(ctx.duel_state.get("round", 1))
        already_resolved = any(
            r.get("round") == rnd for r in ctx.duel_state.get("round_results", [])
        )
        if (
            ctx.duel_state.get("phase_code", 2) <= 1
            and ctx.duel_state.get("subphase") in ("WAIT", "DRAW")
            and not already_resolved
        ):
            can_shoot = ctx.duel_state.get("can_shoot", False)
            if not can_shoot and (curr_hits > player.prev_hits_rec or ammo_dropped):
                # Firing (or landing a hit) before the DRAW signal:
                # the opponent gets the round point and the round ends.
                early_shooter: PlayerState | None = None
                if curr_hits > player.prev_hits_rec:
                    delta = curr_hits - player.prev_hits_rec
                    player.hits_received += delta
                    shooters = count_new_shooters(player.prev_slots, status.hit_ids, delta)
                    early_shooter = ctx.players.get(shooters[0]) if shooters else None
                else:
                    early_shooter = player
                opponent = (
                    next((p for p in ctx.players.values() if p.gun_id != early_shooter.gun_id), None)
                    if early_shooter is not None
                    else None
                )
                if early_shooter is not None and opponent is not None:
                    opponent.score += 1
                    self._record_duel_round_result(ctx, opponent)
                    ctx.duel_state["last_winner_gun_num"] = opponent.gun_num
                    ctx.duel_state["last_result_text"] = (
                        f"EARLY FIRE BY P{early_shooter.gun_num} ({early_shooter.display_name})! "
                        f"+1 PT TO PLAYER {opponent.gun_num} ({opponent.display_name})"
                    )
                    ctx.add_event(
                        "duel",
                        f"Round {rnd}: {early_shooter.display_name} fired before FIRE! Point awarded to Player {opponent.gun_num} ({opponent.display_name})! (+1 pt)",
                        actor=opponent,
                        target=early_shooter,
                    )
                    cheat_sub = (
                        f"Early shot by {early_shooter.display_name}! "
                        f"Player {opponent.gun_num} ({opponent.display_name}) gets the point!"
                    )
                    ctx.audio.trigger_pool("duel_cheat", custom_subtitle=cheat_sub, force=True)
                    cheat_wait = max(0.5, ctx.audio.active_until - time.time()) + 0.25
                    self._announce_duel_round_win(ctx, opponent, initial_delay=cheat_wait)
                    next_wait = 9.5
                else:
                    self._record_duel_round_result(ctx, None)
                    ctx.duel_state["last_winner_gun_num"] = None
                    ctx.duel_state["last_result_text"] = f"ROUND {rnd}: DRAW — NO POINT"
                    ctx.audio.trigger_pool("duel_drawn", force=True)
                    next_wait = 7.0
                ctx.duel_state["subphase"] = "ROUND_END"
                ctx.duel_state["phase_code"] = 2
                ctx.duel_state["can_shoot"] = False
                now = time.time()
                ctx.duel_state["round_ended_at"] = now
                ctx.duel_state["next_transition_at"] = now + next_wait
                ctx._check_lead_change()
            elif can_shoot and curr_hits > player.prev_hits_rec:
                delta = curr_hits - player.prev_hits_rec
                player.hits_received += delta
                shooters = count_new_shooters(player.prev_slots, status.hit_ids, delta)
                shooter = ctx.players.get(shooters[0]) if shooters else None
                if shooter:
                    shooter.score += 1
                    shooter.hits_landed += 1
                    shooter.frags += 1
                    ctx.duel_state["last_winner_gun_num"] = shooter.gun_num
                    ctx.duel_state["last_result_text"] = (
                        f"PLAYER {shooter.gun_num} ({shooter.display_name}) GETS THE POINT! (+1 PT)"
                    )
                    ctx.add_event(
                        "frag",
                        f"Round {rnd}: {shooter.display_name} outdrew {player.display_name}! (+1 pt)",
                        actor=shooter,
                        target=player,
                    )
                    self._record_duel_round_result(ctx, shooter)
                    self._announce_duel_round_win(ctx, shooter)
                else:
                    self._record_duel_round_result(ctx, None)
                    ctx.duel_state["last_winner_gun_num"] = None
                    ctx.duel_state["last_result_text"] = f"ROUND {rnd}: DRAW — NO POINT"
                    ctx.audio.trigger_pool("duel_drawn", force=True)
                ctx.duel_state["subphase"] = "ROUND_END"
                ctx.duel_state["phase_code"] = 2
                ctx.duel_state["can_shoot"] = False
                now = time.time()
                ctx.duel_state["round_ended_at"] = now
                ctx.duel_state["next_transition_at"] = now + 7.0
                ctx._check_lead_change()
            elif (
                can_shoot
                and len(ctx.players) >= 2
                and all(p.ammo == 0 for p in ctx.players.values())
            ):
                ctx.duel_state["subphase"] = "ROUND_END"
                ctx.duel_state["phase_code"] = 2
                ctx.duel_state["can_shoot"] = False
                now = time.time()
                ctx.duel_state["round_ended_at"] = now
                ctx.duel_state["next_transition_at"] = now + 7.0
                ctx.duel_state["last_winner_gun_num"] = None
                ctx.duel_state["last_result_text"] = (
                    f"ROUND {rnd}: BOTH OUT OF AMMO — DRAW (NO POINT)"
                )
                self._record_duel_round_result(ctx, None)
                ctx.add_event("duel", f"Round {rnd}: Both duelists missed — Draw!")
                ctx.audio.trigger_pool("duel_drawn", force=True)
        player.prev_hits_rec = curr_hits

    def _announce_duel_round_win(
        self, ctx: ArenaEngine, shooter: PlayerState, initial_delay: float = 0.0
    ) -> None:
        """Announce a duel round win: 'Player X!' (GLOBAL_54+gun_num) then
        'Wins the round!' (GLOBAL_119) once the callout finishes.

        Uses priority=-1 so the player-number callout preempts 'Fire!'
        (priority=0) both on the server and in the browser. When
        `initial_delay > 0` (e.g. after an early-fire foul line), both
        clips are scheduled sequentially via `_pending_audio`."""
        rnd = ctx.duel_state.get("round", 1)
        subtitle = f"Player {shooter.gun_num} ({shooter.display_name}) wins Round {rnd}! (+1 pt)"
        if 1 <= shooter.gun_num <= 16:
            player_file = f"DLG_GM_GLOBAL_{54 + shooter.gun_num:02d}.wav"
            player_sub = f"Player {shooter.gun_num} ({shooter.display_name})!"
            if initial_delay <= 0.0:
                ctx.audio.active_until = 0.0
                cue = ctx.audio.play_announcer_file(
                    player_file,
                    player_sub,
                    priority=-1,
                    duration=1.35,
                )
                if cue is not None:
                    delay = max(0.1, ctx.audio.active_until - time.time()) + 0.2
                else:
                    delay = 0.1
                ctx._schedule_audio_followup(
                    delay,
                    "pool",
                    pool_key="duel_round_win",
                    subtitle=subtitle,
                    force=True,
                )
            else:
                ctx._schedule_audio_followup(
                    initial_delay,
                    "file",
                    filename=player_file,
                    subtitle=player_sub,
                    priority=-1,
                    duration=1.35,
                )
                ctx._schedule_audio_followup(
                    initial_delay + 1.55,
                    "pool",
                    pool_key="duel_round_win",
                    subtitle=subtitle,
                    force=True,
                )
        else:
            if initial_delay <= 0.0:
                ctx.audio.trigger_pool(
                    "duel_round_win", custom_subtitle=subtitle, force=True
                )
            else:
                ctx._schedule_audio_followup(
                    initial_delay,
                    "pool",
                    pool_key="duel_round_win",
                    subtitle=subtitle,
                    force=True,
                )


MODE_CLASS = OldFashionDuelMode
