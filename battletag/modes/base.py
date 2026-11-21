"""Game mode interface and shared combat behavior.

Every BattlePew mode is one module in :mod:`battletag.modes` defining a
:class:`GameMode` subclass. The engine (:class:`battletag.modes.engine.ArenaEngine`)
owns all shared state — players, audio, transport, the match clock, the
deferred-audio queue — and delegates every mode-specific decision to the
active mode object through the hooks below. Mode objects are stateless:
they operate on the engine passed as ``ctx`` and keep no per-match state
of their own, so the engine remains the single source of truth for the
snapshot, the app loop, and the tests.

The default implementations here are the standard combat rules shared by
FreeFrag, StarterFrag, TeamFrag, and FreeForAll (hit scoring, streaks,
lead changes, score sorting, top-score game-over outcomes). Modes with
different rules override only what differs; duel and race override
almost everything.
"""

from __future__ import annotations

import random
import time
from abc import ABC
from typing import TYPE_CHECKING, Any

from battletag.protocol import (
    RFID_CARDS,
    TEAM_INFO,
    DecodedStatus,
    count_new_shooters,
)

if TYPE_CHECKING:
    from battletag.modes.engine import ArenaEngine, PlayerState

# Original Main.loc kill-feed templates (ingame001-021)
KILL_VERBS = [
    "tagged",
    "blasted",
    "zapped",
    "ambushed",
    "outgunned",
    "sniped",
    "locked onto",
]

FRAG_VERBS = [
    "eliminated",
    "knocked out",
    "fragged",
    "neutralized",
    "dominated",
]


class GameMode(ABC):
    """One game mode: metadata, policy flags, and lifecycle hooks.

    Subclasses MUST define ``METADATA`` (the catalog entry, moved
    verbatim from the old ``MODE_CATALOG``) and live in their own module
    with a module-level ``MODE_CLASS`` + ``ORDER`` so the registry can
    discover them. All hooks receive the engine as ``ctx``.
    """

    METADATA: dict[str, Any]
    ORDER: int = 99

    # --- Policy flags: small mode predicates the shared engine code
    # reads instead of branching on mode names. ---

    # Modes with limited ammo get the low-ammo / empty-ammo warnings.
    finite_ammo: bool = False
    # An ammo rise mid-match fires the "scanned an Ammo Card" event.
    # The duel's ammo is reset per round, not reloaded by cards.
    ammo_card_events: bool = True
    # The blind/aim miss taunt after 5 unanswered shots. Tower shots
    # are not misses, so the race disables it.
    miss_taunt_enabled: bool = True
    # Idle banter after 8 s without action. The duel's standoff
    # silence is the game, and race tags do not count as action.
    idle_banter_enabled: bool = True
    # Whether the match clock ends the match. Timerless modes never
    # reach the clock check; the race owns its per-run cap instead.
    match_clock_ends_match: bool = True
    # Disconnect policy: "last_gun" prompts once at most one gun is
    # still answering; "mode_critical" prompts as soon as any scoped
    # player goes silent.
    disconnect_policy: str = "last_gun"
    # Life-based respawn flavor: TeamFrag respawns read the team
    # T-Base text; other life modes use neutral wording.
    respawn_uses_team_base: bool = False
    # Life-based respawn audio. LastManStanding has no respawns, so a
    # restore there is a medkit edge case and stays silent.
    respawn_audio: bool = True
    # Life-based modes with no respawn mark a 0-HP player ELIMINATED
    # instead of SCAN T-BASE. Only LastManStanding sets this.
    no_respawn: bool = False

    def count_shots_fired(self, ctx: ArenaEngine) -> bool:
        """Whether status ammo/shot deltas should increment `shots_fired`."""
        return True

    # --- Metadata conveniences (read from METADATA, same values the
    # old MODE_CATALOG entries carried). ---

    @property
    def mode_id(self) -> str:
        return str(self.METADATA["id"])

    @property
    def timed(self) -> bool:
        return bool(self.METADATA.get("timed", True))

    @property
    def uses_teams(self) -> bool:
        return bool(self.METADATA.get("teams", False))

    @property
    def min_players(self) -> int:
        return int(self.METADATA.get("min_players", 2))

    @property
    def max_players(self) -> int:
        return int(self.METADATA.get("max_players", 8))

    # --- Lifecycle ---

    def ammo_caps(self, ctx: ArenaEngine) -> tuple[int, int]:
        """(max_ammo, max_clips) for a fresh match, from settings."""
        return (
            int(ctx.settings.get("ammunitions", 9)),
            int(ctx.settings.get("clips", 3)),
        )

    def initial_score(
        self, ctx: ArenaEngine, player: PlayerState, max_life: int, num_players: int
    ) -> int:
        """A player's score at match start. Most modes start at 0."""
        return 0

    def on_match_start(self, ctx: ArenaEngine) -> None:
        """Per-mode match setup, after the shared stat reset.

        The default clears any stale race state: only the race builds
        a turn machine, and a previous race must never leak into the
        next match. Duel state is deliberately left alone (the old
        code never cleared it here either).
        """
        ctx.race_state = {}

    def on_timer_tick(self, ctx: ArenaEngine) -> None:
        """Advance a round/timer state machine once per match-loop cycle.

        Only the duel implements this. The race uses on_turn_tick.
        """
        return None

    def on_turn_tick(self, ctx: ArenaEngine, now: float | None = None) -> None:
        """Advance a turn state machine once per match-loop cycle.

        Only the race implements this. ``now`` is injectable so tests
        can drive the machine without waiting on wall time.
        """
        return None

    def on_match_end(self, ctx: ArenaEngine, now: float | None = None) -> bool:
        """Finalize mode state when the match is stopped. Returns True
        when a partial result was recorded. Only the race records a
        mid-run snapshot; other modes have nothing to finalize."""
        return False

    # --- Ingestion ---

    def on_status_report(
        self, ctx: ArenaEngine, player: PlayerState, status: DecodedStatus, ammo_dropped: bool
    ) -> None:
        """Resolve one gun's 0x95 status report into hits and scores.

        The engine runs the shared prologue first (baseline, shots,
        ammo/clips readout) and the shared epilogue after (shift
        register update); this hook owns only the mode-specific
        resolution. The default is the hits-received branch shared by
        FreeFrag and StarterFrag.
        """
        curr_hits = status.hits_received or 0
        delta = curr_hits - player.prev_hits_rec
        if delta > 0:
            player.hits_received += delta
            player.streak = 0
            shooters = count_new_shooters(player.prev_slots, status.hit_ids, delta)
            for sgid in shooters:
                shooter = ctx.players.get(sgid)
                if shooter:
                    self.record_hit(ctx, shooter, player)
        player.prev_hits_rec = curr_hits

    def on_life_status_report(
        self, ctx: ArenaEngine, player: PlayerState, status: DecodedStatus
    ) -> None:
        """Resolve a 0x95 report for life-based modes (TeamFrag,
        FreeForAll, LastManStanding): life deltas drive hits, frags,
        respawns, and medkits. Hit recording goes through
        :meth:`record_life_hits` so LastManStanding can substitute
        its survival formula; respawn flavor comes from the
        ``respawn_*`` flags."""
        curr_life = status.life_points if status.life_points is not None else player.life_points
        if curr_life < player.prev_life:
            delta = player.prev_life - curr_life
            player.hits_received += delta
            player.life_points = curr_life
            player.streak = 0
            shooters = count_new_shooters(player.prev_slots, status.hit_ids, delta)
            # "Medic!" when life drops to exactly 1.
            if curr_life == 1 and player.prev_life > 1:
                ctx.audio.trigger_pool("low_life")
            self.record_life_hits(ctx, shooters, player, curr_life)
            if curr_life == 0:
                player.deaths += 1
                player.status_badge = "ELIMINATED" if self.no_respawn else "SCAN T-BASE"
        elif curr_life > player.prev_life:
            was_zero = player.prev_life == 0
            player.life_points = curr_life
            player.status_badge = "ACTIVE"
            if was_zero:
                if self.respawn_uses_team_base:
                    team_rfid = TEAM_INFO.get(player.team_id, TEAM_INFO[1])["rfid_code"]
                    player.last_rfid = team_rfid
                    ctx.add_event(
                        "respawn",
                        f"{player.display_name} scanned {TEAM_INFO.get(player.team_id, TEAM_INFO[1])['name']} T-Base & respawned!",
                        actor=player,
                        icon_url=TEAM_INFO.get(player.team_id, TEAM_INFO[1])["rfid_tex"],
                    )
                else:
                    # FreeForAll respawns are timer-only (no scan is
                    # involved); LastManStanding has no respawn at
                    # all, so a restore there is only ever a medkit
                    # edge case — neutral wording for both.
                    ctx.add_event(
                        "respawn",
                        f"{player.display_name} is back in the game!",
                        actor=player,
                    )
                if self.respawn_audio:
                    ctx.audio.trigger_pool("respawn")
            else:
                player.last_rfid = "RF07"
                ctx.add_event(
                    "medkit",
                    f"{player.display_name} scanned a Medkit (+HP)!",
                    actor=player,
                    icon_url=RFID_CARDS["RF07"]["icon"],
                )
                ctx.audio.trigger_pool("medkit_used")
        player.prev_life = curr_life

    def on_tower_hits(self, ctx: ArenaEngine, hits_by_gid: dict[int, int]) -> None:
        """Process 0xC3 tower hits. Only the race scores them."""
        return None

    def record_hit(
        self, ctx: ArenaEngine, shooter: PlayerState, victim: PlayerState, is_frag: bool = False
    ) -> None:
        """Score one landed hit: +1, streaks, kill-feed, frag bonus.

        This is the shared combat scoring used by FreeFrag,
        StarterFrag, TeamFrag, and FreeForAll. TeamFrag overrides it
        to check friendly fire first.
        """
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

        if is_frag:
            shooter.frags += 1
            shooter.score += 3
            fverb = random.choice(FRAG_VERBS)
            ctx.add_event(
                "frag",
                f"FRAG! {shooter.display_name} {fverb} {victim.display_name} (+3 bonus)",
                actor=shooter,
                target=victim,
            )
            ctx.audio.trigger_pool("frag_scored", custom_subtitle=f"{shooter.display_name} fragged {victim.display_name}!")
        elif shooter.streak == 5:
            ctx.audio.trigger_pool("streak_5", custom_subtitle=f"{shooter.display_name} is on a 5-hit streak!")
        elif shooter.streak == 3:
            ctx.audio.trigger_pool("streak_3", custom_subtitle=f"{shooter.display_name} — 3-hit streak!")
        else:
            ctx.audio.trigger_pool("hit_scored")

        ctx._check_lead_change()

    def record_life_hits(
        self, ctx: ArenaEngine, shooters: list[int], victim: PlayerState, curr_life: int
    ) -> None:
        """Score the shooters behind one life drop. The default
        records each hit through :meth:`record_hit`, with the
        killing blow (shooters[0], most-recent-first) counting as
        the frag. LastManStanding overrides this with the original's
        survival formula."""
        for i, sgid in enumerate(shooters):
            shooter = ctx.players.get(sgid)
            if shooter:
                # The shift register is most-recent-first,
                # so shooters[0] landed the killing blow.
                is_frag = (curr_life == 0) and (i == 0)
                self.record_hit(ctx, shooter, victim, is_frag=is_frag)

    # --- Scoring / ranking ---

    def on_lead_check(self, ctx: ArenaEngine) -> None:
        """Fire a lead-change line when the leader changes. Team
        modes compare team standings; solo modes compare players.
        Duel and race have no lead concept and override to no-op."""
        if self.uses_teams:
            standings = ctx.get_team_standings()
            if len(standings) >= 2 and standings[0]["score"] > standings[1]["score"]:
                lead_key = f"team_{standings[0]['team_id']}"
                if lead_key != ctx.current_leader_key:
                    ctx.current_leader_key = lead_key
                    t_id = standings[0]["team_id"]
                    pool_map = {1: "lead_red", 2: "lead_blue", 3: "lead_yellow", 4: "lead_green"}
                    ctx.audio.trigger_pool(pool_map.get(t_id, "lead_red"))
        else:
            ranked = ctx.get_sorted_players()
            if len(ranked) >= 2 and ranked[0].score > ranked[1].score:
                lead_key = f"player_{ranked[0].gun_id}"
                if lead_key != ctx.current_leader_key:
                    ctx.current_leader_key = lead_key
                    ctx.audio.trigger_pool("lead_change_ffa", custom_subtitle=f"{ranked[0].display_name} takes the lead!")

    def sort_players(self, ctx: ArenaEngine, players: list[PlayerState]) -> list[PlayerState]:
        """Rank players for the scoreboard: score, hits landed, fewest
        hits received, accuracy. LastManStanding and the race
        override."""
        return sorted(
            players,
            key=lambda p: (p.score, p.hits_landed, -p.hits_received, p.accuracy),
            reverse=True,
        )

    def gameover_outcomes(self, ctx: ArenaEngine) -> dict[int, bool]:
        """Per-gun ``won: bool`` for 0x94 game-over packets. The
        default: an outright top scorer wins (green LED); ties at
        the top or losers lose (red LED). TeamFrag, LastManStanding,
        and the race override."""
        ranked = ctx.get_sorted_players()
        top_score = ranked[0].score
        winners = [p.gun_id for p in ranked if p.score == top_score]
        is_tie = len(winners) > 1
        return {
            gid: (not is_tie and gid == winners[0])
            for gid in ctx.players
        }

    # --- End conditions / disconnect / clock ---

    def check_end_condition(self, ctx: ArenaEngine) -> bool:
        """Mode-specific auto-end, checked after the shared timer and
        score-limit checks. Only duel, race, and LastManStanding
        implement one."""
        return False

    def disconnect_players(self, ctx: ArenaEngine) -> list[PlayerState]:
        """Players whose silence can raise the disconnect prompt. The
        race scopes this to rostered racers; everyone else watches
        the whole roster."""
        return list(ctx.players.values())

    def clock_remaining(self, ctx: ArenaEngine) -> tuple[bool, float | None]:
        """(handled, seconds) for the match clock. The default
        returns (False, None) and the engine runs its shared clock;
        the race handles its own per-run cap clock."""
        return (False, None)

    # --- Deferred audio ---

    def followup_still_live(self, ctx: ArenaEngine, entry: dict[str, Any]) -> bool:
        """True when a run-guarded deferred audio entry may still
        fire. Only the race queues run-guarded entries; every other
        mode drops them."""
        return False

    def on_followup(self, ctx: ArenaEngine, entry: dict[str, Any]) -> bool:
        """Handle a deferred-audio entry kind the shared dispatcher
        does not know (it handles "pool" and "file" itself). Returns
        True when handled. Only the duel implements one
        ("duel_can_shoot")."""
        return False

    # --- Display contract ---

    def display_state(self, ctx: ArenaEngine) -> dict[str, Any]:
        """The state slice the TV/HUD screens need for this mode.

        The snapshot merges this verbatim, so the screens render
        from contract data instead of the engine branching on mode
        names. Keys are always present: ``duel_state``,
        ``race_state``, ``race_session_best`` (None when the mode
        has no such slice).
        """
        return {"duel_state": None, "race_state": None, "race_session_best": None}

    # --- Race roster API (race only; other modes reject) ---

    def set_roster(
        self, ctx: ArenaEngine, order: list[int], bench: list[int]
    ) -> tuple[bool, str]:
        """Replace the full race roster. Only the race implements it."""
        return False, "Race roster only applies to Tag'n Shoot."

    def toggle_roster(
        self, ctx: ArenaEngine, gun_num: int, in_race: bool
    ) -> tuple[bool, str]:
        """Opt one player in or out of the race. Only the race
        implements it."""
        return False, "Race roster only applies to Tag'n Shoot."

    # --- Race queries (race only; other modes return empty) ---

    def race_winners(self, ctx: ArenaEngine) -> list[PlayerState]:
        """Race winners (shared-win rules). Empty for non-race modes."""
        return []

    def race_ranked_players(self, ctx: ArenaEngine) -> list[PlayerState]:
        """Racers in race rank order. Empty for non-race modes."""
        return []

    def race_next_gun_id(self, ctx: ArenaEngine) -> int | None:
        """The following racer's gun id (UP NEXT tag). None for
        non-race modes."""
        return None

    def skip_run(self, ctx: ArenaEngine, now: float | None = None) -> tuple[bool, str]:
        """Host skip of the current race run. Only the race
        implements it."""
        return False, "No race is running."
