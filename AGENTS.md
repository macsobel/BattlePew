# BattlePew Project Guidelines

## Keep the Mobile HUD in Sync
- Whenever you change game-mode rules, scoring, clocks/countdowns, player stats, or main-screen UI (`battletag/static/index.html`), always check and apply the corresponding updates to the mobile HUD (`battletag/static/hud.html`) as well.
- Specifically verify:
  - Mode-specific score labels, vitals/HP bars (including proportional scaling when `max_life > 10`), and round/turn banners.
  - Pre-round or match countdowns, clock colors, and post-game summary stats (`gameOverCard` / `gameOverStats`).
  - Results-reveal gating (`results_revealed`) so winner/standings spoilers stay hidden on phones until **See Results** is pressed.
