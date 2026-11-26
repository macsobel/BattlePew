"""OldFashionDuel mode: 2–8 player Wild West 1v1 showdown (round-robin for 3+).

Purely round-driven with no timer: for 2 players, a single head-to-head
duel across 1/3/5/7 rounds (Player 1 pulls trigger to start). For 3–8
players, an interleaved 1v1 round-robin tournament where each pairing
waits for either active duelist to pull their trigger once in position,
runs a 5 s countdown on those two guns, plays the configured rounds, and
accumulates round-win points across all pairings. Firing before FIRE
awards the round point to the opponent.
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

ORDER = 6


def _build_round_robin_matches(gun_ids: list[int]) -> list[list[int]]:
    """Return an interleaved 1v1 round-robin schedule for `gun_ids`.

    Produces every unique unordered pair once (`N * (N - 1) // 2` matches)
    and orders pairings so consecutive duels balance play counts and avoid
    back-to-back repeats (guaranteed 0 consecutive overlap for N >= 5).
    """
    ordered = list(gun_ids)
    n = len(ordered)
    if n < 2:
        return []
    if n == 2:
        return [[ordered[0], ordered[1]]]

    circle: list[int | None] = list(ordered)
    if len(circle) % 2 == 1:
        circle.append(None)
    m = len(circle)
    base_pairs: list[tuple[int, int, int]] = []
    for rnd in range(m - 1):
        for idx in range(m // 2):
            a = circle[idx]
            b = circle[m - 1 - idx]
            if a is not None and b is not None:
                base_pairs.append((rnd, a, b))
        circle = [circle[0], circle[-1], *circle[1:-1]]

    remaining = list(base_pairs)
    played_count = {gid: 0 for gid in ordered}
    schedule: list[list[int]] = []
    last_pair: set[int] = set()
    prev_pair: set[int] = set()

    while remaining:
        rem_deg = {gid: 0 for gid in ordered}
        for _, a, b in remaining:
            rem_deg[a] += 1
            rem_deg[b] += 1

        def _score_candidate(cand: tuple[int, int, int]) -> tuple[int, int, int, int, int, int]:
            rnd, a, b = cand
            ov_last = len({a, b} & last_pair)
            ov_prev = len({a, b} & prev_pair)
            total_played = played_count[a] + played_count[b]
            max_played = max(played_count[a], played_count[b])
            return (ov_last, total_played, max_played, ov_prev, -(rem_deg[a] + rem_deg[b]), rnd)

        best = min(remaining, key=_score_candidate)
        remaining.remove(best)
        _, a, b = best
        schedule.append([a, b])
        played_count[a] += 1
        played_count[b] += 1
        prev_pair = last_pair
        last_pair = {a, b}

    return schedule


class OldFashionDuelMode(GameMode):
    """Wild West Duel: 2–8 players, 1v1 quickdraw, 6 bullets per round."""

    METADATA: dict[str, Any] = {
        "id": "OldFashionDuel",
        "title": "Wild West Duel",
        "subtitle": "2–8 Players · 1v1 Quickdraw · 6 Bullets per Round",
        "tier": 1,
        "badge": "Quickdraw",
        # The original runs purely on rounds with no timer at all.
        "timed": False,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": False,
        "accessories": [],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/OldFashionDuel.png",
        "banner": "/static/assets/textures/Packages/OldFashionDuel/Ui/Loading_OldFashionDuel.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_OldFashionDuel.png",
        "description": "Quickdraw 1v1 showdown across 1, 3, 5, or 7 rounds per duel! With 3–8 players, everyone faces each other in round-robin 1v1 duels; either duelist pulls their trigger once in position to start each duel. Point your T-Blaster down during the standoff; when the announcer yells FIRE, shoot first — firing early awards +1 pt to your opponent!",
        "bullets": [
            "2–8 players · 1v1 round-robin duels",
            "1, 3, 5, or 7 rounds · First hit after FIRE wins",
            "Firing before FIRE awards opponent +1 pt",
        ],
        "defaults": {
            "countdownSeconds": 5,
            "rounds": 3,
            "earlyClinch": 1,
        },
        "options": [
            {"key": "rounds", "label": "Rounds per Duel", "choices": [1, 3, 5, 7]},
            {"key": "earlyClinch", "label": "Early Clinch (Best-of)", "choices": [0, 1]},
        ],
    }

    ammo_card_events = False
    idle_banter_enabled = False
    disconnect_policy = "mode_critical"

    def ammo_caps(self, ctx: ArenaEngine) -> tuple[int, int]:
        return (6, 0)

    def count_shots_fired(self, ctx: ArenaEngine) -> bool:
        # Early fires before FIRE! and post-round shots are fouls/stray
        # shots, not legal attempts, so they never penalize accuracy.
        return bool(ctx.duel_state.get("can_shoot", False))

    def on_match_start(self, ctx: ArenaEngine) -> None:
        ordered_gids = [
            p.gun_id for p in sorted(ctx.players.values(), key=lambda p: p.gun_num)
        ]
        matches = _build_round_robin_matches(ordered_gids)
        is_tournament = len(matches) > 1
        active_pair = list(matches[0]) if matches else list(ordered_gids[:2])
        next_pair = list(matches[1]) if len(matches) > 1 else []
        upcoming_matches = [list(m) for m in matches[1:4]]
        match_records = {
            str(p.gun_num): {"wins": 0, "losses": 0, "draws": 0}
            for p in ctx.players.values()
        }
        max_rnd = int(ctx.settings.get("rounds", 3))
        early_clinch = int(ctx.settings.get("earlyClinch", 1)) != 0 and max_rnd > 1
        ctx._duel_round_triggered = False
        ctx.duel_state = {
            "round": 0,
            "max_rounds": max_rnd,
            "early_clinch": early_clinch,
            "clinched": False,
            "phase_code": 2,
            "subphase": "MATCH_INTRO" if is_tournament else "INTRO",
            "can_shoot": False,
            "next_transition_at": 0.0 if is_tournament else (time.time() + 4.0),
            "countdown_remaining": 0,
            "round_scores": {gid: 0 for gid in active_pair},
            "round_results": [],
            "last_result_text": "",
            "last_winner_gun_num": None,
            "round_started_at": 0.0,
            "round_ended_at": 0.0,
            "trigger_armed": {gid: False for gid in active_pair} if is_tournament else False,
            # Tournament round-robin state (2-player keeps is_tournament=False)
            "is_tournament": is_tournament,
            "matches": matches,
            "match_index": 0,
            "total_matches": len(matches),
            "active_pair": active_pair,
            "next_pair": next_pair,
            "upcoming_matches": upcoming_matches,
            "completed_matches": [],
            "match_records": match_records,
            "last_match_result_text": "",
            "match_intro_played": not is_tournament,
            "countdown_seconds": 5,
            "countdown_ends_at": 0.0,
            "countdown_sent": True,
            "next_number": 0,
            "next_number_at": 0.0,
            "pairing_gameover_pending": None,
        }
        ctx.race_state = {}

    def on_lead_check(self, ctx: ArenaEngine) -> None:
        # Duel has no lead concept: rounds are announced by the
        # round-win callout + GLOBAL_119, and a generic lead-change
        # line would interrupt that sequence.
        return None

    def disconnect_players(self, ctx: ArenaEngine) -> list[PlayerState]:
        ds = ctx.duel_state
        if ds.get("is_tournament") and ds.get("subphase") not in ("INTRO", "WAIT", "DRAW", "ROUND_END"):
            return []
        active_pair = ds.get("active_pair") or []
        if active_pair:
            return [ctx.players[gid] for gid in active_pair if gid in ctx.players]
        return list(ctx.players.values())

    def followup_still_live(self, ctx: ArenaEngine, entry: dict[str, Any]) -> bool:
        """True when a duel-guarded deferred audio/timer entry belongs to the active pairing."""
        if ctx.mode_name != "OldFashionDuel":
            return False
        ds = ctx.duel_state
        if ds.get("match_index") != entry.get("duel_match"):
            return False
        if "duel_round" in entry:
            return ds.get("round") == entry.get("duel_round") and ds.get("subphase") in ("WAIT", "DRAW")
        return True

    def on_timer_tick(self, ctx: ArenaEngine) -> None:
        """Advance duel round & tournament subphases."""
        if ctx.phase != "PLAYING":
            return
        now = time.time()
        ds = ctx.duel_state
        sub = ds.get("subphase", "INTRO")
        max_rnd = int(ds.get("max_rounds", 3))
        curr_rnd = int(ds.get("round", 0))
        active_pair: list[int] = list(ds.get("active_pair") or [])

        if sub == "MATCH_INTRO":
            if not ds.get("match_intro_played"):
                ds["match_intro_played"] = True
                self._play_match_intro(ctx, now)
            elif now >= ds.get("next_transition_at", 0.0):
                ds["subphase"] = "WAIT_TRIGGER"
                ds["phase_code"] = 2
                ds["can_shoot"] = False
                ds["countdown_remaining"] = 0
                ds["trigger_armed"] = {gid: False for gid in active_pair}
                ctx._duel_round_triggered = False
                ctx.audio.play_gun_voice(
                    "en/DSN4_SD17_Press-the-trigger-to-start-the-game.wav",
                    "Press the trigger to start the game!",
                    priority=0,
                    duration=3.0,
                )
            return

        if sub == "WAIT_TRIGGER":
            if ctx._duel_round_triggered:
                ctx._duel_round_triggered = False
                cd = 5
                ds["subphase"] = "COUNTDOWN"
                ds["phase_code"] = 2
                ds["can_shoot"] = False
                ds["countdown_seconds"] = cd
                ds["countdown_remaining"] = cd
                ds["countdown_ends_at"] = now + cd
                ds["countdown_sent"] = False
                ds["next_number"] = cd
                ds["next_number_at"] = now
                ds["round_started_at"] = 0.0
                ds["round_ended_at"] = 0.0
                ctx.countdown_remaining = cd
            return

        if sub == "COUNTDOWN":
            if ds.get("next_number", 0) >= 1 and now >= ds.get("next_number_at", 0.0):
                num = int(ds["next_number"])
                ctx.audio.play_countdown_number(num)
                ctx.countdown_remaining = num
                ds["countdown_remaining"] = num
                ds["next_number"] = num - 1
                ds["next_number_at"] = now + 1.0
            if now >= ds.get("countdown_ends_at", 0.0):
                ctx.countdown_remaining = 0
                ds["countdown_remaining"] = 0
                for gid in active_pair:
                    p = ctx.players.get(gid)
                    if p is not None:
                        p.ammo = 6
                        p.prev_ammo = 6
                        p.prev_slots = [0, 0, 0, 0, 0]
                        p.baseline_set = False
                        p.last_status_ts = now
                    if ctx.use_simulator and gid in ctx.sim.guns:
                        sg = ctx.sim.guns[gid]
                        sg.hits_received = 0
                        sg.hit_ids = [0, 0, 0, 0, 0]
                        sg.ammo = 6
                self._begin_duel_round(ctx, 1, max_rnd, now)
            return

        if sub == "MATCH_END":
            if now >= ds.get("next_transition_at", 0.0):
                self._advance_to_next_pairing(ctx, now)
            return

        if now >= ds.get("next_transition_at", 0.0):
            ds["countdown_remaining"] = 0
            if sub in ("INTRO", "ROUND_END"):
                if curr_rnd + 1 > max_rnd or self._is_pairing_clinched(ctx):
                    self._finish_duel_pairing(ctx, now)
                    return
                self._begin_duel_round(ctx, curr_rnd + 1, max_rnd, now)
            elif sub == "WAIT":
                ds["subphase"] = "DRAW"
                ds["phase_code"] = 1
                # The clear "Fire!" clip (GLOBAL_83, 0.87 s) is the
                # signal. Shooting opens a short grace after the cue
                # starts (browser playback latency) instead of at the
                # end of the line, so a player reacting to the word is
                # never penalized as early.
                ds["can_shoot"] = False
                ds["next_transition_at"] = now + 8.0
                ctx.audio.trigger_pool("duel_draw", force=True)
                ctx._schedule_audio_followup(
                    DUEL_FIRE_GRACE_S,
                    "duel_can_shoot",
                    duel_match=int(ds.get("match_index", 0)),
                    duel_round=curr_rnd,
                )
            elif sub == "DRAW":
                # Timeout without a hit -> round draw
                ds["subphase"] = "ROUND_END"
                ds["phase_code"] = 2
                ds["can_shoot"] = False
                ds["countdown_remaining"] = 0
                ds["round_ended_at"] = now
                end_wait = 6.0 if (ds.get("is_tournament") and curr_rnd >= max_rnd) else 8.5
                ds["next_transition_at"] = now + end_wait
                ds["last_winner_gun_num"] = None
                ds["last_result_text"] = (
                    f"ROUND {ds['round']}: DRAW — NO POINT AWARDED"
                )
                self._record_duel_round_result(ctx, None)
                ctx.add_event("duel", f"Round {ds['round']} ended in a draw!")
                ctx.audio.trigger_pool("duel_drawn", force=True)
        elif sub in ("INTRO", "ROUND_END") and curr_rnd < max_rnd and not self._is_pairing_clinched(ctx):
            rem_s = ds.get("next_transition_at", 0.0) - now
            if 0.0 < rem_s <= 3.0:
                cd = max(1, min(3, int(rem_s) + 1))
                if cd != int(ds.get("countdown_remaining", 0) or 0):
                    ds["countdown_remaining"] = cd
                    ctx.audio.play_countdown_number(cd)

    def _play_match_intro(self, ctx: ArenaEngine, now: float) -> None:
        """Announce the upcoming 1v1 pairing in tournament mode."""
        ds = ctx.duel_state
        match_idx = int(ds.get("match_index", 0))
        total_matches = int(ds.get("total_matches", 1))
        active_pair = list(ds.get("active_pair") or [])
        if len(active_pair) < 2:
            ds["next_transition_at"] = now
            return
        pa = ctx.players.get(active_pair[0])
        pb = ctx.players.get(active_pair[1])
        if pa is None or pb is None:
            ds["next_transition_at"] = now
            return

        ctx.add_event(
            "duel",
            f"Duel {match_idx + 1} of {total_matches}: {pa.display_name} vs {pb.display_name} — step up and pull your trigger when ready!",
            actor=pa,
            target=pb,
        )
        ctx.audio.play_ui("Validation.wav")
        if match_idx == 0:
            delay = 0.0
            if 1 <= pa.gun_num <= 16:
                ctx.audio.play_announcer_file(
                    f"DLG_GM_GLOBAL_{54 + pa.gun_num:02d}.wav",
                    f"Player {pa.gun_num} ({pa.display_name})!",
                    priority=-1,
                    duration=1.35,
                )
                delay = 1.55
            if 1 <= pb.gun_num <= 16:
                ctx._schedule_audio_followup(
                    delay,
                    "file",
                    filename=f"DLG_GM_GLOBAL_{54 + pb.gun_num:02d}.wav",
                    subtitle=f"Player {pb.gun_num} ({pb.display_name})!",
                    priority=-1,
                    duration=1.35,
                    duel_match=match_idx,
                )
                delay += 1.55
            ctx._schedule_audio_followup(
                delay,
                "pool",
                pool_key="duel_setup",
                subtitle=f"Duel 1 of {total_matches}: {pa.display_name} vs {pb.display_name} — pull your trigger when ready!",
                force=True,
                duel_match=match_idx,
            )
            ds["next_transition_at"] = now + max(1.0, delay + 0.1)
        else:
            ctx.audio.play_announcer_file(
                "DLG_GM_GLOBAL_113.wav",
                f"Next duel! {pa.display_name} vs {pb.display_name}!",
                priority=-1,
                duration=1.15,
            )
            delay = 1.35
            if 1 <= pa.gun_num <= 16:
                ctx._schedule_audio_followup(
                    delay,
                    "file",
                    filename=f"DLG_GM_GLOBAL_{54 + pa.gun_num:02d}.wav",
                    subtitle=f"Player {pa.gun_num} ({pa.display_name})!",
                    priority=-1,
                    duration=1.35,
                    duel_match=match_idx,
                )
                delay += 1.55
            if 1 <= pb.gun_num <= 16:
                ctx._schedule_audio_followup(
                    delay,
                    "file",
                    filename=f"DLG_GM_GLOBAL_{54 + pb.gun_num:02d}.wav",
                    subtitle=f"Player {pb.gun_num} ({pb.display_name})!",
                    priority=-1,
                    duration=1.35,
                    duel_match=match_idx,
                )
                delay += 1.5
            ds["next_transition_at"] = now + delay

    def _finish_duel_pairing(self, ctx: ArenaEngine, now: float, skipped: bool = False) -> None:
        """Conclude the current 1v1 pairing and either transition to MATCH_END or COMPLETE."""
        ds = ctx.duel_state
        match_idx = int(ds.get("match_index", 0))
        total_matches = int(ds.get("total_matches", 1))
        active_pair = list(ds.get("active_pair") or [])
        round_scores = ds.get("round_scores") or {}

        if len(active_pair) >= 2 and active_pair[0] in ctx.players and active_pair[1] in ctx.players:
            pa = ctx.players[active_pair[0]]
            pb = ctx.players[active_pair[1]]
            score_a = int(round_scores.get(pa.gun_id, 0))
            score_b = int(round_scores.get(pb.gun_id, 0))
            if score_a > score_b:
                winner: PlayerState | None = pa
            elif score_b > score_a:
                winner = pb
            else:
                winner = None

            ds.setdefault("completed_matches", []).append(
                {
                    "match_index": match_idx,
                    "pair": list(active_pair),
                    "gun_nums": [pa.gun_num, pb.gun_num],
                    "scores": [score_a, score_b],
                    "winner_gun_num": winner.gun_num if winner else None,
                    "skipped": skipped,
                }
            )
            records = ds.setdefault("match_records", {})
            rec_a = records.setdefault(str(pa.gun_num), {"wins": 0, "losses": 0, "draws": 0})
            rec_b = records.setdefault(str(pb.gun_num), {"wins": 0, "losses": 0, "draws": 0})
            if not (skipped and score_a == 0 and score_b == 0):
                if winner is pa:
                    rec_a["wins"] += 1
                    rec_b["losses"] += 1
                elif winner is pb:
                    rec_b["wins"] += 1
                    rec_a["losses"] += 1
                else:
                    rec_a["draws"] += 1
                    rec_b["draws"] += 1

            if skipped and score_a == 0 and score_b == 0:
                res_text = f"DUEL {match_idx + 1} OF {total_matches}: SKIPPED"
            elif winner is not None:
                hi, lo = max(score_a, score_b), min(score_a, score_b)
                res_text = (
                    f"DUEL {match_idx + 1} OF {total_matches}: "
                    f"PLAYER {winner.gun_num} ({winner.display_name}) WINS {hi}–{lo}!"
                )
            else:
                res_text = (
                    f"DUEL {match_idx + 1} OF {total_matches}: "
                    f"DRAW {score_a}–{score_b}"
                )
        else:
            score_a = score_b = 0
            res_text = "DUEL COMPLETE"

        ds["can_shoot"] = False
        ds["phase_code"] = 2
        ds["countdown_remaining"] = 0
        ds["last_match_result_text"] = res_text

        if ds.get("is_tournament") and match_idx + 1 < total_matches:
            ds["subphase"] = "MATCH_END"
            ds["next_transition_at"] = now + (1.5 if skipped else 4.0)
            if len(active_pair) >= 2:
                ds["pairing_gameover_pending"] = {
                    active_pair[0]: score_a > score_b,
                    active_pair[1]: score_b > score_a,
                }
            ctx.add_event("duel", res_text)
            if not skipped:
                ctx.audio.play_ui("Ranking.wav")
        else:
            ds["pairing_gameover_pending"] = None
            ds["subphase"] = "COMPLETE"

    def _advance_to_next_pairing(self, ctx: ArenaEngine, now: float) -> None:
        """Advance tournament state to the next scheduled 1v1 pairing."""
        ds = ctx.duel_state
        matches: list[list[int]] = list(ds.get("matches") or [])
        next_idx = int(ds.get("match_index", 0)) + 1
        if next_idx >= len(matches):
            ds["pairing_gameover_pending"] = None
            ds["subphase"] = "COMPLETE"
            ds["phase_code"] = 2
            ds["can_shoot"] = False
            return

        active_pair = list(matches[next_idx])
        next_pair = list(matches[next_idx + 1]) if next_idx + 1 < len(matches) else []
        upcoming = [list(m) for m in matches[next_idx + 1 : next_idx + 4]]

        ds["match_index"] = next_idx
        ds["active_pair"] = active_pair
        ds["next_pair"] = next_pair
        ds["upcoming_matches"] = upcoming
        ds["round"] = 0
        ds["clinched"] = False
        ds["round_scores"] = {gid: 0 for gid in active_pair}
        ds["round_results"] = []
        ds["last_result_text"] = ""
        ds["last_winner_gun_num"] = None
        ds["last_match_result_text"] = ""
        ds["round_started_at"] = 0.0
        ds["round_ended_at"] = 0.0
        ds["subphase"] = "MATCH_INTRO"
        ds["phase_code"] = 2
        ds["can_shoot"] = False
        ds["countdown_remaining"] = 0
        ds["next_transition_at"] = 0.0
        ds["match_intro_played"] = False
        ds["countdown_sent"] = True
        ds["pairing_gameover_pending"] = None
        ds["trigger_armed"] = {gid: False for gid in active_pair}
        ctx._duel_round_triggered = False

    def skip_pairing(self, ctx: ArenaEngine, now: float | None = None) -> tuple[bool, str]:
        """Host skip: finish or bypass the current tournament duel pairing."""
        if ctx.phase != "PLAYING" or ctx.mode_name != "OldFashionDuel":
            return False, "No duel match is active."
        ds = ctx.duel_state
        if not ds.get("is_tournament"):
            return False, "Skip Duel is only available in 3+ player tournaments."
        sub = ds.get("subphase")
        if sub == "COMPLETE":
            return False, "All tournament duels are already complete."
        now = time.time() if now is None else now
        if sub == "MATCH_END":
            self._advance_to_next_pairing(ctx, now)
            return True, ""

        was_live = sub in ("COUNTDOWN", "INTRO", "WAIT", "DRAW", "ROUND_END")
        self._finish_duel_pairing(ctx, now, skipped=True)
        if not was_live:
            ds["pairing_gameover_pending"] = None
            if ds.get("subphase") == "MATCH_END":
                self._advance_to_next_pairing(ctx, now)
        return True, ""

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

    def _is_pairing_clinched(self, ctx: ArenaEngine) -> bool:
        """True when Early Clinch is active and a duelist holds a majority of rounds."""
        ds = ctx.duel_state
        if not ds.get("early_clinch"):
            return False
        max_rnd = int(ds.get("max_rounds", 3))
        if max_rnd <= 1:
            return False
        needed = (max_rnd // 2) + 1
        scores = ds.get("round_scores") or {}
        active_pair = ds.get("active_pair") or list(ctx.players.keys())
        return any(int(scores.get(gid, 0)) >= needed for gid in active_pair)

    def _duel_wins(self, ctx: ArenaEngine, player: PlayerState) -> int:
        records = ctx.duel_state.get("match_records") or {}
        return int((records.get(str(player.gun_num)) or {}).get("wins", 0))

    def sort_players(self, ctx: ArenaEngine, players: list[PlayerState]) -> list[PlayerState]:
        ds = ctx.duel_state
        if ds.get("is_tournament") and ds.get("early_clinch"):
            return sorted(
                players,
                key=lambda p: (
                    self._duel_wins(ctx, p),
                    p.score,
                    p.hits_landed,
                    -p.hits_received,
                    p.accuracy,
                ),
                reverse=True,
            )
        return super().sort_players(ctx, players)

    def gameover_outcomes(self, ctx: ArenaEngine) -> dict[int, bool]:
        ds = ctx.duel_state
        if ds.get("is_tournament") and ds.get("early_clinch"):
            ranked = self.sort_players(ctx, list(ctx.players.values()))
            if not ranked:
                return {}
            top_key = (self._duel_wins(ctx, ranked[0]), ranked[0].score)
            winners = [p.gun_id for p in ranked if (self._duel_wins(ctx, p), p.score) == top_key]
            is_tie = len(winners) > 1
            return {gid: (not is_tie and gid == winners[0]) for gid in ctx.players}
        return super().gameover_outcomes(ctx)

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
        ctx.duel_state["clinched"] = self._is_pairing_clinched(ctx)

    def _reset_duel_ammo(self, ctx: ArenaEngine) -> None:
        """Reset active duelists' ammo to 6 bullets for the next round."""
        active_pair = ctx.duel_state.get("active_pair") or list(ctx.players.keys())
        for gid in active_pair:
            p = ctx.players.get(gid)
            if p is not None:
                p.ammo = 6
                p.prev_ammo = 6
            if ctx.use_simulator and gid in ctx.sim.guns:
                ctx.sim.guns[gid].ammo = 6

    def _begin_duel_round(self, ctx: ArenaEngine, next_rnd: int, max_rnd: int, now: float) -> None:
        """Enter WAIT for round `next_rnd` with fresh ammo and round audio."""
        ds = ctx.duel_state
        match_idx = int(ds.get("match_index", 0))
        is_tournament = bool(ds.get("is_tournament"))
        ds["round"] = next_rnd
        ds["phase_code"] = 1
        ds["subphase"] = "WAIT"
        ds["can_shoot"] = False
        ds["countdown_remaining"] = 0
        ds["round_started_at"] = now
        ds["round_ended_at"] = 0.0
        ds["last_result_text"] = ""
        ds["last_winner_gun_num"] = None
        # Round 1 carries the setup intro AND "T-blaster along the leg" in 2-player
        # (so its floor is 8.5 s); tournament pairings already played duel_setup
        # at MATCH_INTRO, so all their rounds use the standard 3–20 s range.
        roll = random.random()
        if next_rnd == 1 and not is_tournament:
            wait_s = random.uniform(15.0, 20.0) if roll < 0.10 else random.uniform(8.5, 15.0)
        elif roll < 0.15:
            wait_s = random.uniform(3.0, 5.0)
        elif roll < 0.25:
            wait_s = random.uniform(15.0, 20.0)
        else:
            wait_s = random.uniform(5.0, 15.0)
        ds["next_transition_at"] = now + wait_s
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
        if next_rnd == 1 and not is_tournament:
            ctx._schedule_audio_followup(
                delay,
                "pool",
                pool_key="duel_setup",
                force=True,
                duel_match=match_idx,
            )
            ctx._schedule_audio_followup(
                delay + 4.5,
                "file",
                filename="DLG_GM_OW_DUEL_03.wav",
                subtitle="T-blaster along the leg!",
                priority=0,
                duration=2.5,
                duel_match=match_idx,
            )
        else:
            ctx._schedule_audio_followup(
                delay,
                "pool",
                pool_key="duel_prepare",
                force=True,
                duel_match=match_idx,
            )

    def on_status_report(
        self, ctx: ArenaEngine, player: PlayerState, status: DecodedStatus, ammo_dropped: bool
    ) -> None:
        curr_hits = status.hits_received or 0
        ds = ctx.duel_state
        active_pair = list(ds.get("active_pair") or ctx.players.keys())
        if player.gun_id not in active_pair:
            player.prev_hits_rec = curr_hits
            return

        rnd = int(ds.get("round", 1))
        max_rnd = int(ds.get("max_rounds", 3))
        match_idx = int(ds.get("match_index", 0))
        already_resolved = any(
            r.get("round") == rnd for r in ds.get("round_results", [])
        )
        if (
            ds.get("phase_code", 2) <= 1
            and ds.get("subphase") in ("WAIT", "DRAW")
            and not already_resolved
        ):
            can_shoot = ds.get("can_shoot", False)
            if not can_shoot and (curr_hits > player.prev_hits_rec or ammo_dropped):
                # Firing (or landing a hit) before the DRAW signal:
                # the opponent gets the round point and the round ends.
                early_shooter: PlayerState | None = None
                if curr_hits > player.prev_hits_rec:
                    delta = curr_hits - player.prev_hits_rec
                    shooters = count_new_shooters(player.prev_slots, status.hit_ids, delta)
                    valid_shooters = [gid for gid in shooters if gid in active_pair and gid != player.gun_id]
                    early_shooter = ctx.players.get(valid_shooters[0]) if valid_shooters else None
                else:
                    early_shooter = player
                if early_shooter is None:
                    player.prev_hits_rec = curr_hits
                    return
                opponent = next(
                    (ctx.players[gid] for gid in active_pair if gid != early_shooter.gun_id and gid in ctx.players),
                    None,
                )
                if opponent is not None:
                    early_shooter.early_fires += 1
                    opponent.score += 1
                    opponent.streak += 1
                    opponent.best_streak = max(opponent.best_streak, opponent.streak)
                    early_shooter.streak = 0
                    self._record_duel_round_result(ctx, opponent)
                    ds["last_winner_gun_num"] = opponent.gun_num
                    ds["last_result_text"] = (
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
                    next_wait = 8.5 if (ds.get("is_tournament") and (rnd >= max_rnd or self._is_pairing_clinched(ctx))) else 10.5
                else:
                    self._record_duel_round_result(ctx, None)
                    ds["last_winner_gun_num"] = None
                    ds["last_result_text"] = f"ROUND {rnd}: DRAW — NO POINT"
                    ctx.audio.trigger_pool("duel_drawn", force=True)
                    next_wait = 6.0 if (ds.get("is_tournament") and (rnd >= max_rnd or self._is_pairing_clinched(ctx))) else 8.5
                ds["subphase"] = "ROUND_END"
                ds["phase_code"] = 2
                ds["can_shoot"] = False
                ds["countdown_remaining"] = 0
                now = time.time()
                ds["round_ended_at"] = now
                ds["next_transition_at"] = now + next_wait
                ctx._check_lead_change()
            elif can_shoot and curr_hits > player.prev_hits_rec:
                delta = curr_hits - player.prev_hits_rec
                player.hits_received += delta
                shooters = count_new_shooters(player.prev_slots, status.hit_ids, delta)
                valid_shooters = [gid for gid in shooters if gid in active_pair and gid != player.gun_id]
                shooter = ctx.players.get(valid_shooters[0]) if valid_shooters else None
                if shooter:
                    shooter.score += 1
                    shooter.hits_landed += 1
                    shooter.frags += 1
                    shooter.streak += 1
                    shooter.best_streak = max(shooter.best_streak, shooter.streak)
                    shooter.hits_dealt_to[player.gun_num] = (
                        shooter.hits_dealt_to.get(player.gun_num, 0) + 1
                    )
                    player.streak = 0
                    player.hits_taken_from[shooter.gun_num] = (
                        player.hits_taken_from.get(shooter.gun_num, 0) + 1
                    )
                    ds["last_winner_gun_num"] = shooter.gun_num
                    ds["last_result_text"] = (
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
                    ds["last_winner_gun_num"] = None
                    ds["last_result_text"] = f"ROUND {rnd}: DRAW — NO POINT"
                    ctx.audio.trigger_pool("duel_drawn", force=True)
                ds["subphase"] = "ROUND_END"
                ds["phase_code"] = 2
                ds["can_shoot"] = False
                ds["countdown_remaining"] = 0
                now = time.time()
                ds["round_ended_at"] = now
                end_wait = 6.0 if (ds.get("is_tournament") and (rnd >= max_rnd or self._is_pairing_clinched(ctx))) else 8.5
                ds["next_transition_at"] = now + end_wait
                ctx._check_lead_change()
            elif (
                can_shoot
                and len(active_pair) >= 2
                and all(gid in ctx.players and ctx.players[gid].ammo == 0 for gid in active_pair)
            ):
                ds["subphase"] = "ROUND_END"
                ds["phase_code"] = 2
                ds["can_shoot"] = False
                ds["countdown_remaining"] = 0
                now = time.time()
                ds["round_ended_at"] = now
                end_wait = 6.0 if (ds.get("is_tournament") and (rnd >= max_rnd or self._is_pairing_clinched(ctx))) else 8.5
                ds["next_transition_at"] = now + end_wait
                ds["last_winner_gun_num"] = None
                ds["last_result_text"] = (
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
        ds = ctx.duel_state
        rnd = ds.get("round", 1)
        match_idx = int(ds.get("match_index", 0))
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
                    duel_match=match_idx,
                )
            else:
                ctx._schedule_audio_followup(
                    initial_delay,
                    "file",
                    filename=player_file,
                    subtitle=player_sub,
                    priority=-1,
                    duration=1.35,
                    duel_match=match_idx,
                )
                ctx._schedule_audio_followup(
                    initial_delay + 1.55,
                    "pool",
                    pool_key="duel_round_win",
                    subtitle=subtitle,
                    force=True,
                    duel_match=match_idx,
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
                    duel_match=match_idx,
                )


MODE_CLASS = OldFashionDuelMode
