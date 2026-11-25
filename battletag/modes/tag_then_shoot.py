"""TagThenShoot mode: solo timed turns racing a T-Base course.

Each racer runs the course in turn — tag the T-Bases in order, shoot
the UbiConnect tower after each one — against a 60 s cap. Runs loop
round-major over a frozen roster order; best finished time wins
(shared win on exact ties), points decide unfinished runs. Spoken
prompts (Player callout before every run, next-T-Base names during
the run) are sequenced so they never overlap the countdown numbers
or the gun's 0x93 countdown.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from battletag.modes.base import GameMode
from battletag.protocol import DecodedStatus

if TYPE_CHECKING:
    from battletag.modes.engine import ArenaEngine, PlayerState

ORDER = 7


class TagThenShootMode(GameMode):
    """Tag'n Shoot: timed turns, best finished time wins."""

    METADATA: dict[str, Any] = {
        "id": "TagThenShoot",
        "title": "Tag'n Shoot",
        "subtitle": "Timed turns: run the course, beat the clock",
        "tier": 1,
        "badge": "Solo Course",
        "timed": True,
        "min_players": 1,
        "max_players": 8,
        "teams": False,
        "needs_accessories": True,
        "accessories": ["tbase"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/TagNShoot.png",
        "banner": "/static/assets/textures/Packages/TagThenShoot/Ui/Loading_TagNShoot.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_TagNShoot.png",
        "description": "Solo timed turns! Each player runs the course in turn — pull the trigger to start (Manual) or wait for the countdown (Auto) — scans the T-Bases in order, and shoots the UbiConnect tower after each one. Best finished time wins across the rounds; points still count if the 60-second cap runs out!",
        "bullets": [
            "Solo timed turns · 2 or 4 T-Base course",
            "Best finished time wins (60 s cap per run)",
            "Auto or per-racer trigger launch · 1+ rounds",
        ],
        "defaults": {
            "durationSeconds": 60,
            "countdownSeconds": 3,
            "gameLaunch": 1,
            "numberOfBase": 4,
            "rounds": 1,
            "assist": 1,
        },
        "options": [
            # Fixed 60 s in the original: no duration option. Bases are
            # 2, or 4 with the medkit pack. Rounds are full cycles: every
            # racer runs once per round; best finished time wins.
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "numberOfBase", "label": "T-Bases Required", "choices": [2, 4]},
            {"key": "rounds", "label": "Number of Rounds", "choices": [1, 2, 3]},
        ],
    }

    miss_taunt_enabled = False
    idle_banter_enabled = False
    match_clock_ends_match = False
    disconnect_policy = "mode_critical"

    # Round announcement lines, shared with the duel (PR #15): the same
    # GLOBAL_114/115/116 files, with the duel's modeled durations.
    # GLOBAL_113 "NEXT GAME!" stays unwired everywhere.
    _RACE_ROUND_LINES: dict[str, tuple[str, str, float]] = {
        "first": ("DLG_GM_GLOBAL_115.wav", "First round!", 1.2),
        "next": ("DLG_GM_GLOBAL_114.wav", "Next round!", 1.1),
        "final": ("DLG_GM_GLOBAL_116.wav", "Final round", 1.4),
    }

    # Player-number callout duration: the duel's GLOBAL_54+gun_num
    # files ("Player 1!".."Player 16!"). Modeled duration covers the
    # longest line in the set (GLOBAL_67 "Player 13", 1.83 s) so the
    # callout window never overlaps the countdown numbers.
    _RACE_PLAYER_CALLOUT_DURATION = 1.9

    def on_match_start(self, ctx: ArenaEngine) -> None:
        # The roster freezes at match start: turn_order is the
        # snapshot, in race order, repeated once per round. Benched
        # players never appear in it (they still poll and stay in
        # state.players, but the game displays hide them entirely).
        order = ctx.race_rosters_gun_ids()
        first = order[0] if order else None
        max_rounds = max(1, min(3, int(ctx.settings.get("rounds", 1))))
        launch_mode = "auto" if int(ctx.settings.get("gameLaunch", 1)) == 0 else "manual"
        ctx.race_state = {
            "turn_order": order,
            "turn_index": 0,
            "round_index": 1,
            "max_rounds": max_rounds,
            "launch_mode": launch_mode,
            "turn_phase": "ROUND_INTRO" if order else "DONE",
            "active_gun_id": first,
            # False until a trigger poll observes the trigger
            # released, so a trigger held over from the previous
            # turn cannot auto-release the next wait.
            "trigger_armed": False,
            "countdown_seconds": 0,
            "countdown_remaining": 0,
            "countdown_ends_at": 0.0,
            "countdown_sent": True,
            "next_number": 0,
            "next_number_at": 0.0,
            # ROUND_INTRO: the round announcement plays on the
            # first tick, then the intro ends at intro_ends_at
            # (modeled line length + 0.25 s) so the line never
            # overlaps the countdown numbers.
            "intro_played": False,
            "intro_ends_at": 0.0,
            # PLAYER_INTRO: the "Player N!" callout plays on the
            # first tick, then the callout ends at callout_ends_at
            # (modeled line length + 0.25 s) so it never overlaps
            # the countdown numbers or the gun's 0x93 countdown.
            "callout_played": False,
            "callout_ends_at": 0.0,
            "turn_started_at": 0.0,
            "turn_ends_at": 0.0,
            "beat_ends_at": 0.0,
            "elapsed": 0.0,
            "turn_cap_s": int(ctx.settings.get("durationSeconds", 60)),
            "bases": max(1, int(ctx.settings.get("numberOfBase", 4))),
            # The following racer (UP NEXT tag), derived by
            # race_next_gun_id; None on the final run, in solo
            # races, and once the race is DONE.
            "next_gun_id": None,
        }
        self._refresh_race_next_gun_id(ctx)
        if first is not None:
            fp = ctx.players[first]
            if launch_mode == "auto":
                ctx.add_event(
                    "system",
                    f"Round 1 of {max_rounds} — {fp.display_name} is up first, countdown starts in a moment!",
                )
            else:
                ctx.add_event(
                    "system",
                    f"Round 1 of {max_rounds} — {fp.display_name} is up first — pull the trigger to start the run!",
                )

    def on_lead_check(self, ctx: ArenaEngine) -> None:
        # Timed TagThenShoot turns have no lead concept: players
        # run solo, one at a time, and a points lead mid-run says
        # nothing about who is fastest. Run results are announced at
        # completion instead.
        return None

    def disconnect_players(self, ctx: ArenaEngine) -> list[PlayerState]:
        # Only rostered racers: a benched gun going silent must
        # never prompt (it is not racing).
        return self.race_racers(ctx)

    def check_end_condition(self, ctx: ArenaEngine) -> bool:
        return ctx.race_state.get("turn_phase") == "DONE"

    def clock_remaining(self, ctx: ArenaEngine) -> tuple[bool, float | None]:
        # Timed turns: the clock is the active run's cap, not the
        # match. Outside a live run it shows the full cap, so the
        # 60 s warning stays skipped and the match never ends on
        # this clock — the turn machine owns the cap.
        rs = ctx.race_state
        if ctx.phase == "PLAYING" and rs and rs.get("turn_phase") == "RUNNING":
            return (True, max(0.0, rs.get("turn_ends_at", 0.0) - time.time()))
        if ctx.phase == "GAME_OVER":
            return (True, 0.0)
        return (True, float(ctx.match_duration_s))

    def followup_still_live(self, ctx: ArenaEngine, entry: dict[str, Any]) -> bool:
        """True when a run-guarded race followup may still fire.

        Next-target prompts are queued during a run but fire ~1-2 s
        later; if the run has since capped, been skipped, or ended,
        the prompt belongs to a dead run and must not sound into the
        next run's sequence. The guard keys on the run's turn_index,
        which advances on every turn change, plus the RUNNING phase.
        """
        if ctx.mode_name != "TagThenShoot":
            return False
        rs = ctx.race_state
        return bool(rs) and rs.get("turn_phase") == "RUNNING" and rs.get("turn_index") == entry.get("race_run")

    def display_state(self, ctx: ArenaEngine) -> dict[str, Any]:
        return {
            "duel_state": None,
            "race_state": ctx.race_state,
            "race_session_best": [
                {"gun_id": gid, "bases": bases, **dict(v)}
                for (gid, bases), v in sorted(ctx.race_session_best.items())
            ],
        }

    def sort_players(self, ctx: ArenaEngine, players: list[PlayerState]) -> list[PlayerState]:
        # Timed turns: rostered racers by race_rank_key (best
        # time, then points), with benched players appended in
        # gun-number order. Benched players stay in state.players
        # (setup screens and the roster API still need them); the
        # game displays hide them entirely, and they are excluded
        # from ranking/winners by race_ranked_players and
        # race_winners, not by disappearing here.
        racers = self.race_ranked_players(ctx)
        racer_ids = {p.gun_id for p in racers}
        benched = sorted(
            (p for p in players if p.gun_id not in racer_ids),
            key=lambda p: p.gun_num,
        )
        return racers + benched

    def gameover_outcomes(self, ctx: ArenaEngine) -> dict[int, bool]:
        # Timed turns, shared win: every racer tied on the best
        # finished time gets a winner LED; with no finishes,
        # every racer tied on top total points does. Benched
        # players get NO outcome entry at all — no win/loss
        # packet is sent to their guns.
        winners = {p.gun_id for p in self.race_winners(ctx)}
        return {p.gun_id: p.gun_id in winners for p in self.race_racers(ctx)}

    # --- Roster ---

    def _roster_edit_allowed(self, ctx: ArenaEngine) -> str | None:
        """None when a roster edit is allowed, else the rejection reason."""
        if ctx.mode_name != "TagThenShoot":
            return "Race roster only applies to Tag'n Shoot."
        if ctx.phase != "LOBBY":
            return "Race roster can only change before the match starts."
        return None

    def set_roster(
        self, ctx: ArenaEngine, order: list[int], bench: list[int]
    ) -> tuple[bool, str]:
        """Replace the full roster (TV drag-and-drop / X / re-add).

        `order` + `bench` must partition the paired gun numbers with
        no duplicates, and at least one racer must remain.
        """
        reason = self._roster_edit_allowed(ctx)
        if reason is not None:
            return False, reason
        try:
            order_nums = [int(n) for n in order]
            bench_nums = [int(n) for n in bench]
        except (TypeError, ValueError):
            return False, "Roster entries must be player numbers."
        paired = sorted(p.gun_num for p in ctx.players.values())
        if sorted(order_nums + bench_nums) != paired:
            return False, "Roster must list every paired player exactly once, in the race or on the bench."
        if len(set(order_nums)) != len(order_nums) or len(set(bench_nums)) != len(bench_nums):
            return False, "Roster must not repeat a player."
        if not order_nums:
            return False, "At least one player must race."
        ctx.race_roster = {"order": order_nums, "bench": bench_nums}
        return True, ""

    def toggle_roster(self, ctx: ArenaEngine, gun_num: int, in_race: bool) -> tuple[bool, str]:
        """Opt one player in or out (phone HUD). Opting in appends to
        the END of the race order and never reorders anyone else;
        opting out moves the player to the bench. Cannot bench the
        last racer."""
        reason = self._roster_edit_allowed(ctx)
        if reason is not None:
            return False, reason
        ctx.sync_race_roster()
        if not any(p.gun_num == gun_num for p in ctx.players.values()):
            return False, f"Player {gun_num} is not paired."
        order = list(ctx.race_roster["order"])
        bench = list(ctx.race_roster["bench"])
        if in_race:
            if gun_num in order:
                return True, ""
            bench = [n for n in bench if n != gun_num]
            order.append(gun_num)
        else:
            if gun_num in bench:
                return True, ""
            if len(order) <= 1:
                return False, "At least one player must race."
            order = [n for n in order if n != gun_num]
            bench.append(gun_num)
        ctx.race_roster = {"order": order, "bench": bench}
        return True, ""

    # --- Turn machine ---

    def _race_run_active(self, ctx: ArenaEngine, player: PlayerState) -> bool:
        """True when `player` is the runner of a live timed turn."""
        if ctx.mode_name != "TagThenShoot":
            return False
        rs = ctx.race_state
        return bool(rs) and rs.get("turn_phase") == "RUNNING" and rs.get("active_gun_id") == player.gun_id

    def _race_countdown_seconds(self, ctx: ArenaEngine) -> int:
        """Per-run countdown from the launch setting: 10 s in Auto,
        5 s in Manual (after the trigger pull). The old halved-3 s
        countdown is gone; the gun's 0x93 packet takes any value."""
        return 10 if ctx.race_state.get("launch_mode") == "auto" else 5

    def _enter_race_countdown(self, ctx: ArenaEngine, now: float) -> None:
        """Open COUNTDOWN for the active run. No countdown_intro pool
        here: the intro line would overlap the first spoken number,
        and delaying the numbers would desync them from the gun's
        own 0x93 countdown. The round line and Player callout play
        in their own intro phases before this opens, so the first
        spoken number and the app loop's 0x93 send (countdown_sent)
        start together. Numbers + round line + callout + go only."""
        rs = ctx.race_state
        cd = self._race_countdown_seconds(ctx)
        rs["turn_phase"] = "COUNTDOWN"
        rs["countdown_seconds"] = cd
        rs["countdown_remaining"] = cd
        rs["countdown_ends_at"] = now + cd
        rs["countdown_sent"] = False
        rs["next_number"] = cd
        rs["next_number_at"] = now
        ctx.countdown_remaining = cd

    def _enter_run_start_phase(self, ctx: ArenaEngine, now: float) -> None:
        """Move the active run to its start phase: a trigger wait in
        Manual, straight into the countdown in Auto."""
        if ctx.race_state.get("launch_mode") == "auto":
            self._enter_race_countdown(ctx, now)
        else:
            rs = ctx.race_state
            rs["turn_phase"] = "WAIT_TRIGGER"
            rs["trigger_armed"] = False
            ctx._turn_triggered = False

    def _play_round_announcement(self, ctx: ArenaEngine, now: float) -> None:
        """Play the round line for the current round cycle and set the
        intro window. Round 1 always announces "First round!" (even
        in a 1-round match, as in the duel); the last round of a
        multi-round match announces "Final round"; the rest "Next
        round!". The countdown cannot start until the window elapses,
        so the line never overlaps the countdown numbers."""
        rs = ctx.race_state
        rnd = int(rs.get("round_index", 1))
        max_rnd = int(rs.get("max_rounds", 1))
        if rnd <= 1:
            key = "first"
        elif rnd >= max_rnd:
            key = "final"
        else:
            key = "next"
        wav, sub, dur = self._RACE_ROUND_LINES[key]
        cue = ctx.audio.play_announcer_file(wav, sub, priority=-1, duration=dur)
        delay = (max(0.1, ctx.audio.active_until - now) + 0.25) if cue is not None else 0.1
        rs["intro_played"] = True
        rs["intro_ends_at"] = now + delay

    def _enter_player_intro(self, ctx: ArenaEngine) -> None:
        """Open PLAYER_INTRO for the active run: the Player callout
        plays on the first tick, then the run's start phase opens
        once the callout window ends. Trigger pulls and the 0x93
        countdown both wait: the app loop only polls/sends in
        WAIT_TRIGGER/COUNTDOWN, so nothing can start early."""
        rs = ctx.race_state
        rs["turn_phase"] = "PLAYER_INTRO"
        rs["callout_played"] = False
        rs["callout_ends_at"] = 0.0

    def _play_player_callout(self, ctx: ArenaEngine, now: float) -> None:
        """Play the active runner's "Player N!" callout and set the
        callout window. The spoken line says only the number (custom
        names cannot be spoken from pre-recorded audio); the subtitle
        carries the display name. Same file formula + guard as the
        duel's round-win callout; out-of-range gun numbers proceed
        silently (the roster caps at 8 guns in practice). The
        countdown cannot start until the window elapses, so the
        callout never overlaps the countdown numbers."""
        rs = ctx.race_state
        gid = rs.get("active_gun_id")
        player = ctx.players.get(gid) if gid is not None else None
        if player is not None and 1 <= player.gun_num <= 16:
            wav = f"DLG_GM_GLOBAL_{54 + player.gun_num:02d}.wav"
            sub = f"Player {player.gun_num} ({player.display_name}) is up!"
            cue = ctx.audio.play_announcer_file(
                wav, sub, priority=-1, duration=self._RACE_PLAYER_CALLOUT_DURATION
            )
            delay = (max(0.1, ctx.audio.active_until - now) + 0.25) if cue is not None else 0.1
        else:
            delay = 0.1
        rs["callout_played"] = True
        rs["callout_ends_at"] = now + delay

    def _schedule_next_base_prompt(self, ctx: ArenaEngine, now: float, next_base: int, cue: Any) -> None:
        """Queue a "Next: T-Base N!" prompt after the cue just played.

        Direct file play (guaranteed, not a proba pool), sequenced
        after the preceding line via the modeled active_until window
        — the same pattern as the tag handler's confirmation. The
        followup carries the run's turn_index: tick_pending_audio
        drops it if the run has ended before it fires. A prompt is
        also never due before a Next prompt already queued for the
        same run, so prompts always sound in base order even at
        sim speed (on hardware the earlier prompt has long since
        fired, so that clamp is a no-op). No spoken shoot-the-tower
        line exists in the catalog, so that step stays visual plus
        the tower-hit confirmation.
        """
        if cue is not None:
            delay = max(0.1, ctx.audio.active_until - now) + 0.25
        else:
            delay = 0.1
        run = int(ctx.race_state.get("turn_index", 0))
        queued = [
            float(entry.get("fire_at", 0.0))
            for entry in ctx._pending_audio
            if entry.get("kind") == "file"
            and entry.get("race_run") == run
            and str(entry.get("subtitle", "")).startswith("Next:")
        ]
        if queued:
            delay = max(delay, max(queued) + 0.05 - time.time())
        ctx._schedule_audio_followup(
            delay,
            "file",
            filename=f"DLG_GM_GLOBAL_{72 + next_base - 1:02d}.wav",
            subtitle=f"Next: T-Base {next_base}!",
            priority=2,
            duration=1.5,
            race_run=int(ctx.race_state.get("turn_index", 0)),
        )

    def on_turn_tick(self, ctx: ArenaEngine, now: float | None = None) -> None:
        """Advance the TagThenShoot timed-turn machine, looped per round.

        ROUND_INTRO (round announcement, no overlap with the
        countdown) -> PLAYER_INTRO ("Player N!" callout, no overlap
        with the countdown) -> WAIT_TRIGGER (Manual only: the active
        player pulls the trigger; mandatory, one pull per run) or
        straight to COUNTDOWN (Auto: 10 s before EVERY run; Manual:
        5 s after the pull) -> RUNNING (one course; the cap ends an
        unfinished run) -> TURN_END (3 s beat) -> the next run, or
        the next round's ROUND_INTRO, or DONE once every racer has
        run every round. Called once per match-loop cycle, next to
        the duel timer tick; the hardware trigger poll and the per-turn
        countdown command live in the loop, because they need
        blocking HID calls this sync tick cannot make.
        """
        if ctx.phase != "PLAYING":
            return
        rs = ctx.race_state
        if not rs:
            return
        now = time.time() if now is None else now
        phase = rs.get("turn_phase")
        if phase == "ROUND_INTRO":
            if not rs.get("intro_played"):
                self._play_round_announcement(ctx, now)
            elif now >= rs.get("intro_ends_at", 0.0):
                self._enter_player_intro(ctx)
        elif phase == "PLAYER_INTRO":
            if not rs.get("callout_played"):
                self._play_player_callout(ctx, now)
            elif now >= rs.get("callout_ends_at", 0.0):
                self._enter_run_start_phase(ctx, now)
        elif phase == "WAIT_TRIGGER":
            if ctx._turn_triggered:
                ctx._turn_triggered = False
                self._enter_race_countdown(ctx, now)
        elif phase == "COUNTDOWN":
            if rs.get("next_number", 0) >= 1 and now >= rs.get("next_number_at", 0.0):
                ctx.audio.play_countdown_number(rs["next_number"])
                ctx.countdown_remaining = rs["next_number"]
                rs["countdown_remaining"] = rs["next_number"]
                rs["next_number"] -= 1
                rs["next_number_at"] = now + 1.0
            if now >= rs.get("countdown_ends_at", 0.0):
                self._start_race_run(ctx, now)
        elif phase == "RUNNING":
            rs["elapsed"] = max(0.0, now - rs.get("turn_started_at", now))
            if now >= rs.get("turn_ends_at", 0.0):
                self._complete_race_turn(ctx, completed=False, now=now)
        elif phase == "TURN_END":
            if now >= rs.get("beat_ends_at", 0.0):
                self._advance_race_turn(ctx, now)

    def _start_race_run(self, ctx: ArenaEngine, now: float) -> None:
        """Enter RUNNING for the active player's run: fresh per-run
        counters, a reset course, re-armed time warnings. `score`
        is NOT reset: it is the total across all runs this match."""
        rs = ctx.race_state
        gid = rs.get("active_gun_id")
        player = ctx.players.get(gid) if gid is not None else None
        rs["turn_phase"] = "RUNNING"
        rs["turn_started_at"] = now
        rs["turn_ends_at"] = now + float(rs.get("turn_cap_s", 60))
        rs["elapsed"] = 0.0
        ctx.countdown_remaining = 0
        rs["countdown_remaining"] = 0
        if player is not None:
            player.tags = 0
            player.tower_hits = 0
            player.miss_streak = 0
        if ctx.use_simulator and gid is not None:
            ctx.sim.reset_race_course(gid)
        ctx.audio.reset_match_timers()
        # The go signal is the single clear "Go!" (GLOBAL_13, 0.56 s).
        # The game_start pool's other variants do not say go clearly
        # ("GO! If, like, you want to!", "Go stop. No, I was kidding
        # go") — a joke line is the wrong start signal for a timed
        # run. Other modes keep the pool.
        cue = ctx.audio.play_announcer_file("DLG_GM_GLOBAL_13.wav", "Go!", priority=0, duration=0.6)
        # First-target prompt: "T-Base 1" sequenced after Go, so the
        # runner hears their first target instead of only the screens
        # showing it. Run-guarded: a skip/cap before it fires drops it.
        self._schedule_next_base_prompt(ctx, now, 1, cue)
        if player is not None:
            ctx.add_event("system", f"{player.display_name} started the run — go!")

    def _record_run_result(
        self,
        ctx: ArenaEngine,
        player: PlayerState,
        completed: bool,
        elapsed: float | None,
        points: int | None = None,
        tags: int | None = None,
        tower_hits: int | None = None,
    ) -> dict[str, Any]:
        """Append one run to the player's history for the current round.

        Defaults take the live counters (points = tags + tower hits),
        which is exactly the run's score since both counters reset at
        run start. Skip-before-start passes explicit zeros so stale
        counters from a previous run never leak into a forfeit.
        """
        entry: dict[str, Any] = {
            "round": int(ctx.race_state.get("round_index", 1)),
            "time": elapsed if completed else None,
            "completed": completed,
            "points": (player.tags + player.tower_hits) if points is None else points,
            "tags": player.tags if tags is None else tags,
            "tower_hits": player.tower_hits if tower_hits is None else tower_hits,
        }
        player.run_history.append(entry)
        return entry

    def _complete_race_turn(
        self, ctx: ArenaEngine, completed: bool, now: float | None = None, reason: str = "cap"
    ) -> None:
        """Record the active run's result and open the TURN_END beat.

        Called from the ingest paths when the runner finishes the
        course, from the tick when the cap expires, and from skip-run
        (`reason="skip"`). No-op unless a run is live, so a late
        tower report cannot re-complete a turn.
        """
        rs = ctx.race_state
        if not rs or rs.get("turn_phase") != "RUNNING":
            return
        now = time.time() if now is None else now
        gid = rs.get("active_gun_id")
        player = ctx.players.get(gid) if gid is not None else None
        elapsed = max(0.0, now - rs.get("turn_started_at", now))
        rs["turn_phase"] = "TURN_END"
        rs["beat_ends_at"] = now + 3.0
        rs["elapsed"] = elapsed
        if player is None:
            return
        # Match-best compares against every finish recorded BEFORE
        # this run (all players, all earlier rounds), so compute it
        # before appending this run to the history.
        prev_best: float | None = None
        for p in ctx.players.values():
            best = p.best_run_time
            if best is not None and (prev_best is None or best < prev_best):
                prev_best = best
        self._record_run_result(ctx, player, completed, elapsed if completed else None)
        if not completed:
            if reason == "skip":
                ctx.add_event(
                    "system",
                    f"{player.display_name}'s run was skipped — {player.tags + player.tower_hits} points kept.",
                )
            else:
                ctx.add_event(
                    "system",
                    f"{player.display_name} ran out of time — {player.tags + player.tower_hits} points.",
                )
            return
        is_match_best = prev_best is None or elapsed < prev_best
        bases = int(rs.get("bases", ctx.settings.get("numberOfBase", 4)))
        prev_session = ctx.race_session_best.get((gid, bases))
        is_session_best = prev_session is None or elapsed < prev_session["time"]
        if is_session_best:
            ctx.race_session_best[(gid, bases)] = {"time": elapsed, "name": player.display_name}
        if is_match_best and is_session_best:
            tail = " — fastest so far, and a new session best!"
        elif is_match_best:
            tail = " — fastest so far!"
        elif is_session_best:
            tail = " — a new session best!"
        else:
            tail = ""
        ctx.add_event(
            "system",
            f"{player.display_name} finished the course in {elapsed:.1f}s!{tail}",
        )
        cue = ctx.audio.trigger_pool(
            "race_finish",
            custom_subtitle=f"{player.display_name} finished in {elapsed:.1f}s!",
            force=True,
        )
        # Vocal new-best only for a new MATCH best; personal/session
        # bests show on screen, they are not announced.
        if is_match_best:
            if cue is not None:
                delay = max(0.1, ctx.audio.active_until - now) + 0.25
            else:
                delay = 0.1
            ctx._schedule_audio_followup(
                delay,
                "pool",
                pool_key="race_new_best",
                subtitle=f"{player.display_name} — new best time!",
                force=True,
            )

    def _advance_race_turn(self, ctx: ArenaEngine, now: float) -> None:
        """Move to the next run: same round's next racer, the next
        round's ROUND_INTRO, or DONE after the last run of the last
        round. Round-major, same roster order every round."""
        rs = ctx.race_state
        order = rs.get("turn_order", [])
        max_rounds = int(rs.get("max_rounds", 1))
        rs["turn_index"] = int(rs.get("turn_index", 0)) + 1
        if not order or rs["turn_index"] >= len(order) * max_rounds:
            rs["turn_phase"] = "DONE"
            rs["active_gun_id"] = None
            self._refresh_race_next_gun_id(ctx)
            return
        gid = order[rs["turn_index"] % len(order)]
        rs["round_index"] = rs["turn_index"] // len(order) + 1
        rs["active_gun_id"] = gid
        rs["elapsed"] = 0.0
        ctx._turn_triggered = False
        player = ctx.players.get(gid)
        new_round = rs["turn_index"] % len(order) == 0
        if new_round:
            rs["turn_phase"] = "ROUND_INTRO"
            rs["intro_played"] = False
            rs["intro_ends_at"] = 0.0
            if player is not None:
                ctx.add_event(
                    "system",
                    f"Round {rs['round_index']} of {max_rounds} — {player.display_name} is up first!",
                )
            self._refresh_race_next_gun_id(ctx)
            return
        self._enter_player_intro(ctx)
        if player is not None:
            if rs.get("launch_mode") == "auto":
                ctx.add_event(
                    "system",
                    f"{player.display_name} is up — countdown starting!",
                )
            else:
                ctx.add_event(
                    "system",
                    f"{player.display_name} is up — pull the trigger to start the run!",
                )
        self._refresh_race_next_gun_id(ctx)

    def skip_run(self, ctx: ArenaEngine, now: float | None = None) -> tuple[bool, str]:
        """Host skip: forfeit the current run and move on.

        RUNNING: the partial run is recorded unfinished with the
        points scored so far, then the beat is skipped. PLAYER_INTRO
        / WAIT_TRIGGER / COUNTDOWN: the unstarted run is recorded as
        an unfinished 0-point run and the machine advances (the next
        run gets its own Player callout; callouts are played
        synchronously, never queued, so a skipped run's callout
        cannot bleed into the next run). TURN_END: the beat is
        skipped. ROUND_INTRO: the announcement wait is skipped and
        the Player callout still plays. Any other state: rejected.
        """
        if ctx.phase != "PLAYING" or ctx.mode_name != "TagThenShoot":
            return False, "No race is running."
        rs = ctx.race_state
        if not rs:
            return False, "No race is running."
        now = time.time() if now is None else now
        phase = rs.get("turn_phase")
        if phase == "RUNNING":
            self._complete_race_turn(ctx, completed=False, now=now, reason="skip")
            rs["beat_ends_at"] = now
            return True, ""
        if phase in ("PLAYER_INTRO", "WAIT_TRIGGER", "COUNTDOWN"):
            gid = rs.get("active_gun_id")
            player = ctx.players.get(gid) if gid is not None else None
            if player is not None:
                self._record_run_result(ctx, player, False, None, points=0, tags=0, tower_hits=0)
                ctx.add_event("system", f"{player.display_name}'s run was skipped before it started.")
            self._advance_race_turn(ctx, now)
            return True, ""
        if phase == "TURN_END":
            self._advance_race_turn(ctx, now)
            return True, ""
        if phase == "ROUND_INTRO":
            rs["intro_played"] = True
            self._enter_player_intro(ctx)
            return True, ""
        return False, "Nothing to skip right now."

    def on_match_end(self, ctx: ArenaEngine, now: float | None = None) -> bool:
        """Snapshot a live run when the match is stopped mid-run, so
        its points are not lost from the history/totals. Returns True
        when a partial run was recorded. Unstarted runs are NOT
        fabricated: they simply never ran."""
        if ctx.mode_name != "TagThenShoot":
            return False
        rs = ctx.race_state
        if not rs:
            return False
        now = time.time() if now is None else now
        recorded = False
        if rs.get("turn_phase") == "RUNNING":
            gid = rs.get("active_gun_id")
            player = ctx.players.get(gid) if gid is not None else None
            if player is not None:
                elapsed = max(0.0, now - rs.get("turn_started_at", now))
                rs["elapsed"] = elapsed
                self._record_run_result(ctx, player, False, None)
                recorded = True
        rs["turn_phase"] = "DONE"
        rs["active_gun_id"] = None
        self._refresh_race_next_gun_id(ctx)
        return recorded

    # --- Ranking / winners / UP NEXT ---

    def race_next_gun_id(self, ctx: ArenaEngine) -> int | None:
        """The following racer's gun id (UP NEXT tag), or None.

        One derivation, shared by both displays via the
        ``race_state.next_gun_id`` snapshot field: the next run in the
        frozen order, wrapping to the first racer of the next round.
        None when the race is DONE, when the current run is the final
        run of the final round, and when the next run would be the
        same player (a solo race: the card must never carry both UP
        NOW and UP NEXT). Phase-independent on purpose: the tag shows
        consistently across ROUND_INTRO / WAIT_TRIGGER / COUNTDOWN /
        RUNNING / TURN_END, and skip-run advances it through
        _advance_race_turn like any other turn change.
        """
        rs = ctx.race_state
        if not rs or rs.get("turn_phase") == "DONE":
            return None
        order = rs.get("turn_order", [])
        if len(order) < 1:
            return None
        nxt_index = int(rs.get("turn_index", 0)) + 1
        if nxt_index >= len(order) * int(rs.get("max_rounds", 1)):
            return None
        nxt = order[nxt_index % len(order)]
        if nxt == rs.get("active_gun_id"):
            return None
        return nxt

    def _refresh_race_next_gun_id(self, ctx: ArenaEngine) -> None:
        """Store the derived next racer in race_state (snapshot field)."""
        if ctx.race_state:
            ctx.race_state["next_gun_id"] = self.race_next_gun_id(ctx)

    def race_racers(self, ctx: ArenaEngine) -> list[PlayerState]:
        """Rostered racers in frozen race order: the match snapshot
        turn_order while a match exists, else the setup roster."""
        rs = ctx.race_state
        order = rs.get("turn_order") if rs else None
        if order:
            return [ctx.players[gid] for gid in order if gid in ctx.players]
        return ctx.race_roster_players()

    def _race_best_single_points(self, ctx: ArenaEngine, player: PlayerState) -> int:
        """Best single-run points, including the live run if this
        player is running right now (its points are not in history
        yet)."""
        best = player.best_single_run_points
        rs = ctx.race_state
        if (
            rs
            and rs.get("turn_phase") == "RUNNING"
            and rs.get("active_gun_id") == player.gun_id
        ):
            best = max(best, player.tags + player.tower_hits)
        return best

    def race_rank_key(self, ctx: ArenaEngine, player: PlayerState) -> tuple[int, float, int, int, int]:
        """The ONE race ranking key, shared by sorting, LEDs, winner
        announcement, and both displays' mirrors: finishers by best
        time ascending; any finisher above any non-finisher;
        non-finishers by total points, then best single-run points,
        then gun number. Exact best-time ties share the win — there
        is no tiebreak chain."""
        best = player.best_run_time
        if best is not None:
            return (0, best, 0, 0, player.gun_num)
        return (1, 0.0, -player.score, -self._race_best_single_points(ctx, player), player.gun_num)

    def race_ranked_players(self, ctx: ArenaEngine) -> list[PlayerState]:
        """Rostered racers in race rank order (benched excluded)."""
        return sorted(self.race_racers(ctx), key=lambda p: self.race_rank_key(ctx, p))

    def race_winners(self, ctx: ArenaEngine) -> list[PlayerState]:
        """Shared-win winners: every racer tied on the best finished
        time, or (no finishes) every racer tied on top total points
        AND best single-round points — i.e. the players at place 1
        under race_rank_key, ignoring the gun-number ordering
        tiebreak. Benched players can never win."""
        racers = self.race_racers(ctx)
        if not racers:
            return []
        finished = [p for p in racers if p.best_run_time is not None]
        if finished:
            best = min(p.best_run_time for p in finished)
            return [p for p in finished if p.best_run_time == best]
        top = max(p.score for p in racers)
        leaders = [p for p in racers if p.score == top]
        best_single = max(p.best_single_run_points for p in leaders)
        return [p for p in leaders if p.best_single_run_points == best_single]

    # --- Ingestion ---

    def on_tower_hits(self, ctx: ArenaEngine, hits_by_gid: dict[int, int]) -> None:
        """Process 0xC3 IR shots received directly by the UbiConnect tower in TagThenShoot.

        Tower hits are one of the mode's two scoring channels (+1 each);
        the other is T-Base tags from status word 0. `player.frags` is
        kept in step with the tower-hit total because the poll downlink
        sends the running tower-hit count from it.
        """
        for gid, total_hits in hits_by_gid.items():
            player = ctx.players.get(gid)
            if not player:
                continue
            if total_hits > player.frags:
                delta = total_hits - player.frags
                player.frags = total_hits
                # Only the active runner scores during a timed turn.
                # The frags mirror above still advances for every gun,
                # so a stray tower shot during someone else's turn
                # never leaks into a later turn as phantom credit.
                if not self._race_run_active(ctx, player):
                    continue
                player.tower_hits += delta
                player.score += delta
                player.hits_landed += delta
                player.streak += delta
                player.best_streak = max(player.best_streak, player.streak)
                ctx.add_event(
                    "frag",
                    f"{player.display_name} shot the UbiConnect tower! (+{delta} pt)",
                    actor=player,
                    icon_url="/static/assets/textures/Ui/Pictograms/64x/RF08.png",
                )
                cue = ctx.audio.trigger_pool("tower_hit", custom_subtitle=f"{player.display_name} hit the UbiConnect tower!")
                ctx._check_lead_change()
                # The final tower shot finishes the run: all bases
                # tagged and every tower shot landed. Otherwise the
                # next base name plays as an advance prompt once the
                # tower-hit confirmation finishes (never on the final
                # hit: the finish line plays instead).
                bases = max(1, int(ctx.settings.get("numberOfBase", 4)))
                if player.tags >= bases and player.tower_hits >= bases:
                    self._complete_race_turn(ctx, completed=True)
                elif player.tags < bases:
                    self._schedule_next_base_prompt(ctx, time.time(), player.tags + 1, cue)

    def on_status_report(
        self, ctx: ArenaEngine, player: PlayerState, status: DecodedStatus, ammo_dropped: bool
    ) -> None:
        # Status word 0 is the T-Base tag count (the gun's only SEND
        # var); tower hits arrive separately via 0xC3. Both score
        # +1, tracked and displayed as separate stats. The second
        # status word is not read: the original's RoundLoop never
        # looks at it and its meaning on real hardware is unknown.
        curr_tags = status.hits_received or 0
        if curr_tags > player.prev_hits_rec:
            delta = curr_tags - player.prev_hits_rec
            # Only the active runner scores during a timed turn;
            # the baseline below still advances for every gun, so
            # a stray scan during someone else's turn never counts
            # toward the stray gun's own run.
            if self._race_run_active(ctx, player):
                player.tags += delta
                player.score += delta
                bases = max(1, int(ctx.settings.get("numberOfBase", 4)))
                base_num = ((player.tags - 1) % bases) + 1
                if delta == 1:
                    text = f"{player.display_name} tagged T-Base {base_num}! (+1 pt)"
                else:
                    text = f"{player.display_name} tagged {delta} T-Bases! (+{delta} pt)"
                ctx.add_event(
                    "hit",
                    text,
                    actor=player,
                    icon_url="/static/assets/textures/Ui/Pictograms/64x/RF08.png",
                )
                # Base-name callout (T-Base 1-4), then a confirmation
                # line once the callout finishes. The gun enforces scan
                # order, so the tag count modulo the base count is the
                # stage of the base just tagged.
                cue = ctx.audio.play_announcer_file(
                    f"DLG_GM_GLOBAL_{72 + base_num - 1:02d}.wav",
                    f"T-Base {base_num}!",
                    priority=2,
                    duration=1.5,
                )
                if cue is not None:
                    delay = max(0.1, ctx.audio.active_until - time.time()) + 0.25
                else:
                    delay = 0.1
                ctx._schedule_audio_followup(
                    delay,
                    "pool",
                    pool_key="tagnshoot_base",
                    subtitle=f"{player.display_name} tagged T-Base {base_num}!",
                    force=False,
                )
                ctx._check_lead_change()
                # Safety net: a batched report can carry the last
                # tag together with an already-landed final tower
                # shot. Normally the tower path completes the run.
                if player.tags >= bases and player.tower_hits >= bases:
                    self._complete_race_turn(ctx, completed=True)
        player.prev_hits_rec = curr_tags
        player.prev_phase_flag = status.phase_flag if status.phase_flag is not None else 1


MODE_CLASS = TagThenShootMode
