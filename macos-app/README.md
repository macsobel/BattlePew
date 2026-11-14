# BattlePew — macOS app

Double-clickable, self-contained, fullscreen Mac app for the BattlePew
Arena Master. No Python to install, no terminal, no browser window: the
app starts its own bundled game server and shows the Arena Master UI in a
native window that looks and behaves like any other Mac app. Phones still
join over Wi-Fi by scanning the QR code, exactly as before.

> **Built on Linux, never compiled or run.** This scaffolding was written
> on a Linux VM, which cannot build or test a macOS binary. Every file
> here is *buildable-intent*: correct to the best of the author's review,
> but **the first build on a real Mac is unverified**. The
> [first-build checklist](#first-build-checklist-unverified-on-linux)
> at the bottom lists exactly what must be confirmed on a Mac before this
> is treated as working. Do not skip it.

> **Unsigned build.** Aaron chose an UNSIGNED build (Nov 13, 2026): no
> Developer ID certificate, no notarization, no signing secrets. The app
> works, but macOS Gatekeeper will warn on first launch. See
> [First launch](#first-launch-gatekeeper--unsigned-build) for the exact
> behavior and workaround.

## What you get

- `BattlePew.app` — Apple Silicon only, macOS 13+. Contains everything:
  a Swift AppKit shell, an embedded Python 3.13 runtime, the FastAPI
  server, the web UI, textures, and (local builds only) the original-game
  audio. Nothing is downloaded or installed at launch.
- Fullscreen on launch, with a normal menu bar (BattlePew / Edit / View /
  Window / Help). No address bar, no tabs, no browser chrome of any kind.
  Exit fullscreen any time with Esc, the green traffic light, or
  View > Toggle Full Screen.
- Closing the window or pressing Cmd-Q quits the app **and stops the
  server**. There are no orphaned background servers.
- The phone HUD over LAN keeps working: the bundled server binds all
  interfaces, and the QR code on the main screen is unchanged.
- A plain launch always starts in hardware mode — no dialog. Hold the
  Option (⌥) key while launching to run in simulator mode instead,
  without touching any hardware.

## Quick start (download a built app)

1. Go to the repo's **Actions** tab → **Build macOS App** → latest run on
   `main` → download the `battlepew-macos-…` artifact (a zip), **or**
   download the zip from the latest **Release** (tagged `vX.Y.Z`).
2. Double-click the zip, drag `BattlePew.app` to Applications.
3. Follow [First launch](#first-launch-gatekeeper--unsigned-build)
   (right-click > Open, once).
4. Plug in the UbiConnect *before* launching if you want hardware mode.

Note: Actions/Release builds ship **without** the original game's audio
(announcer/music/UI sounds will be silent). That is deliberate — the
audio files are not in the repo and must not be committed there. For
sound, build locally with `--assets-dir` (below).

## First launch (Gatekeeper / unsigned build)

Because the app is unsigned, macOS treats a downloaded copy as
untrusted. Expect **one** of these, depending on macOS version and on
whether the app still carries its download quarantine flag:

1. **"BattlePew can't be opened because Apple cannot check it for
   malicious software"** (most common for unsigned downloads), or
2. **"BattlePew is damaged and can't be opened"** (can appear when a
   quarantined unsigned app is moved or modified), or
3. The app opens with no warning (if the quarantine flag was already
   removed, e.g. by unzipping with certain tools).

**Workaround (do once; macOS remembers the approval):**

1. In Finder, **right-click (Ctrl-click) `BattlePew.app` → Open**.
2. In the dialog, click **Open**. (The plain double-click dialog has no
   Open button — right-click is what adds it.)
3. If step 2 still refuses, or you see the "damaged" message, remove the
   quarantine flag in Terminal, then right-click > Open again:

   ```sh
   xattr -dr com.apple.quarantine "/Applications/BattlePew.app"
   ```

4. If macOS ever shows a **Local Network** permission prompt
   ("BattlePew would like to find and communicate with devices on your
   local network"), click **Allow** — that is the phone-HUD server. If
   you clicked Don't Allow by mistake, fix it in
   System Settings > Privacy & Security > Local Network.

Do **not** run the app from inside the zip, from Downloads while it is
still quarantined, or from a disk image: macOS may run it
*translocated* (from a hidden read-only random path), which breaks
hardware logging and can make the app behave strangely. Always move it
to Applications (or another permanent folder) first.

## Using the app

- **Fullscreen:** enters fullscreen shortly after launch. Esc, the green
  button, or View > Toggle Full Screen exits to a normal window.
- **Quit:** Cmd-Q, BattlePew > Quit, or close the window. All three stop
  the server. Quitting mid-match ends the match (same as stopping it in
  the UI); guns keep whatever state the server last sent them.
- **Simulator mode:** hold the Option (⌥) key while launching to run
  without touching any hardware. A plain launch always starts in
  hardware mode; if no UbiConnect is plugged in, the Arena Master UI
  shows its "Connect Your UbiConnect" modal and hot-plug detection
  activates as soon as one is plugged in.
- **UbiConnect unplugged mid-game:** the app does not hot-plug. The
  Arena Master UI will show guns going stale (existing server behavior).
  Quit, plug the UbiConnect back in, relaunch, and re-pair. Launching
  with no UbiConnect attached starts in hardware mode with the
  "Connect Your UbiConnect" modal; plug it in and hot-plug detection
  picks it up.
- **Port busy:** the app prefers port 3466 (keeps the QR URL stable). If
  3466 is taken, it tries 3467–3475 automatically. If another copy of
  BattlePew is already running, the new copy shows an "already running"
  message and quits instead of fighting over the port.
- **Server crash:** a native error screen appears with the last server
  output, plus **Relaunch Server**, **Show Server Log**, and **Quit**.
  Relaunching is always your choice — the app never restart-loops.
- **Reload:** View > Reload (Cmd-R) reloads the Arena Master page without
  restarting the server.
- **Logs:** server output goes to `~/Library/Logs/BattlePew/battletag-server.log`
  (use Show Server Log on any error screen to reveal it in Finder).
  Hardware packet logs currently go to a `logs/` folder next to the
  server code — see the known limitation in
  [Docs/REPO_PATCHES_FOLLOWUP.md](Docs/REPO_PATCHES_FOLLOWUP.md#1-log-directory).

## Building locally on a Mac

Prerequisites: Apple Silicon Mac, macOS 13+, Xcode Command Line Tools
(`xcode-select --install` if `swiftc` is missing), ~2 GB free disk, and a
checkout of the repo. Full Xcode is **not** required. Intel Macs are not
supported by this build (the embedded Python is arm64-only).

```sh
cd BattleTag-macOS

# Minimal build (no original-game audio — silent app, fine for testing):
bash macos-app/build_macos_app.sh --package --smoke-test

# Full build with sound (needs the research/handoff tree on this Mac):
bash macos-app/build_macos_app.sh --assets-dir ~/battle-tag-research \
    --package --smoke-test
```

Outputs go to `macos-app/build/`:

- `BattlePew.app` — the app. Double-click to run (see First launch).
- `BattlePew-macOS-<version>-<git-sha>.zip` — the distributable package.
- `BUILD_MANIFEST.txt` — what went into this build (version, Python pin,
  whether audio was included). Attach it to bug reports.

The script is safe to re-run: it reuses the cached Python download in
`macos-app/.cache/` and rebuilds the bundle from scratch. Add `--clean`
to wipe the output dir first. Run with `--help` for all options.

### Audio assets

The server resolves its audio from a `battle-tag-antigravity-handoff/`
layout next to the server code (`battletag/audio.py`). The build script
recreates that layout inside the bundle **from an assets dir you point
it at** — no server code changes needed. The expected layout is the
research tree root:

```
<assets-dir>/
  game/Program Files/Ubisoft/BattleTag/
    System/Locale/En/Audio/GameMaster/   (announcer, ~34 MB)
    Data/Audio/Ui/                       (UI sounds, ~2 MB)
  music-wav/                             (only the 3 referenced tracks are copied)
  gun-sounds/                            (~1 MB)
```

Only the referenced subset is copied (~125 MB total, mostly the three
music tracks). If a category is missing, the build warns and that
category is silent — the app still runs.

**Never commit these audio files to the repo.** They are extracted
original-game assets for private use. That is why the Actions build
cannot include them: the runner only sees the repo checkout.

### Replacing the placeholder icon

The shipped icon is a **placeholder** (orange target glyph, generated
during scaffolding — not final artwork). To replace it:

1. Create a 1024×1024 PNG master (full-bleed square; macOS applies the
   squircle mask). Keep the Battle Tag orange / target motif.
2. Save it as `macos-app/Resources/AppIcon-1024.png` (overwriting the
   placeholder master).
3. Rebuild. The build regenerates all icon sizes with `sips` and builds
   the `.icns` with `iconutil` automatically.

## GitHub Actions build

Workflow: `.github/workflows/build-macos-app.yml`. It calls
`build_macos_app.sh` — it contains no build logic of its own, so local
and CI builds cannot drift apart. The only difference is inputs: CI
never passes `--assets-dir`.

| Trigger | Result |
|---|---|
| Push to `main` | Build + smoke test; zip uploaded as a run artifact (30-day retention). No release. |
| Pull request to `main` | Build + smoke test only (paths-filtered to app/server/workflow changes). |
| Tag push `vX.Y.Z` | Build + smoke test + **GitHub Release** with the zip attached. Tag convention: match `battletag/__init__.py` `__version__` (e.g. version `2.0.0` → tag `v2.0.0`). |
| Manual dispatch | Build + smoke test; artifact uploaded. Trigger from Actions > Build macOS App > Run workflow. |

Details worth knowing:

- **Runner:** pinned `macos-15` (Apple Silicon). `macos-14` retired
  Nov 2, 2026; `macos-latest` floats to macOS 26 and would silently
  change the toolchain. The workflow records the exact `swiftc`/Xcode
  versions in each run's log.
- **No secrets.** The workflow needs none: unsigned build, and the
  release job uses the automatic `GITHUB_TOKEN`. There is no signing
  step to configure.
- **Cost:** GitHub-hosted macOS runners bill at a 10× minute multiplier
  vs Linux (see current GitHub billing docs). The workflow is kept lean
  on purpose: one small job, cached Python tarball + pip wheels, and
  the release job runs on `ubuntu-latest` (it only calls the API).
- **Staying in sync:** if you change the build, change
  `build_macos_app.sh` (and this README) — never add steps to the YAML.
  The YAML's job is triggers, caching, and publishing only.
- **First run after merge:** merging these files to `main` triggers an
  immediate build (push trigger). Expect an artifact within ~10 minutes
  if the build is green; check the Actions tab.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "Cannot check for malicious software" / "damaged" | Unsigned build + quarantine. See [First launch](#first-launch-gatekeeper--unsigned-build). |
| App bounces then nothing / error screen on launch | Open Show Server Log. Common: the bundle is incomplete (re-download/rebuild), or no free port (quit other BattlePew copies). |
| No sound at all | CI build (no audio assets), or a local build without `--assets-dir`. Rebuild with assets. Also check the Mac is not muted — the shell enables autoplay, but system volume still applies. |
| "UbiConnect: Disconnected" / simulator mode unexpectedly | UbiConnect not plugged in, or the app was launched while holding Option (simulator mode). Quit, plug in, relaunch without holding Option. Check the USB cable; try another port. |
| Phones can't reach the HUD / QR doesn't load | Mac and phones must be on the same Wi-Fi. Check the Local Network permission (System Settings > Privacy & Security > Local Network > BattlePew allowed). VPNs and client-isolation Wi-Fi (hotels, some mesh guest networks) block LAN access. |
| Port 3466 busy / "already running" | Another BattlePew (or another server) owns 3466. The app falls back to 3467–3475 automatically, except when another BattlePew answers — then it quits deliberately. Quit the other copy. |
| App won't quit / server keeps running after quit | Should not happen (SIGTERM + SIGKILL grace). If it does: `pkill -f run_battletag.py`, then report it with the server log. |
| Build fails downloading Python / checksum mismatch | The pinned python-build-standalone hash may be stale. Follow the error message: fetch the hash from the release page and re-run with `PBS_SHA256=<hash>`, or update the pin in the script. |
| Build fails at `pip install` (hidapi) | The pinned Python (3.13) may lack a prebuilt hidapi wheel and the source build failed. See the Python-pin note in [Docs/DESIGN_DECISIONS.md](Docs/DESIGN_DECISIONS.md#python-pin). Workaround: pin an older CPython in the script constants. |
| `swiftc: command not found` | Install the Xcode Command Line Tools: `xcode-select --install`. |
| Build fails on an Intel Mac | Expected: v1 is Apple Silicon only. The script refuses with a clear message. |

## Signing later (not built now)

If Aaron ever wants a signed + notarized build (no Gatekeeper warning,
safer distribution), it needs: a paid Apple Developer Program
membership, a Developer ID Application certificate, and notarization via
`notarytool` — plus signing the embedded Python + every `.so`/dylib
inside-out, a hardened-runtime configuration, and secrets in GitHub
Actions for CI signing. None of that is scaffolded here, deliberately:
the unsigned decision stands until Aaron reopens it. When/if he does,
budget a separate pass — do not bolt signing onto this workflow
incrementally without re-testing the HID + LAN behavior under a signed,
hardened-runtime binary.

## First-build checklist (unverified on Linux)

The person running the first Mac build must confirm each of these and
report back before this is called working:

- [ ] `bash macos-app/build_macos_app.sh --smoke-test` completes; the
      smoke test passes (`/api/state` answers from the bundled server).
- [ ] The .app launches, enters fullscreen, and shows the Arena Master
      UI with no browser chrome.
- [ ] Announcer/music/UI audio plays in the shell (autoplay) on a
      build WITH assets; a no-assets build runs silently without errors.
- [ ] A plain launch with a UbiConnect plugged in enables
      pairing/uploads with no dialog; holding Option at launch runs
      the simulator instead.
- [ ] A phone on the same Wi-Fi loads the HUD from the QR code; the
      Local Network prompt behaves as documented.
- [ ] Cmd-Q, closing the window, and the error-screen Quit all stop the
      server (verify: no `run_battletag.py` process remains, port 3466
      is free afterwards).
- [ ] Relaunch-after-crash works (kill the server process manually and
      use Relaunch Server).
- [ ] The Actions build on `macos-15` goes green; the artifact zip
      unzips to a working .app; a `v*` tag creates a Release.
- [ ] The python-build-standalone checksum in the script matches the
      release page (or the pin is corrected and documented).

## File map

```
macos-app/
  README.md                  This file.
  build_macos_app.sh         The build (single source of truth).
  requirements-app.txt       Runtime deps, pinned (subset of repo requirements).
  .gitignore                 Build outputs / caches (never committed).
  Sources/BattleTag/
    main.swift               Entry point (@main, no storyboard/XIB).
    AppDelegate.swift        Window, menus, views, WKWebView, launch flow.
    ServerManager.swift      Server child process, ports, health, logs.
  Resources/
    Info.plist               Bundle metadata (__VERSION__/__BUILD__ substituted).
    AppIcon-1024.png         PLACEHOLDER icon master (replace with real art).
    AppIcon.iconset/         PLACEHOLDER icon sizes for iconutil.
  Scripts/
    make_icns.sh             Master PNG -> .icns (sips + iconutil).
  Docs/
    DESIGN_DECISIONS.md      Approach evaluation, architecture, lifecycle spec.
    REPO_PATCHES_FOLLOWUP.md Small server patches for later (NOT in this merge).
.github/workflows/
  build-macos-app.yml        CI: build + smoke test + artifact + releases.
```

Design rationale lives in
[Docs/DESIGN_DECISIONS.md](Docs/DESIGN_DECISIONS.md). The three small
server-side follow-up patches (asset dir, log dir, port env) are
specified — but explicitly not applied — in
[Docs/REPO_PATCHES_FOLLOWUP.md](Docs/REPO_PATCHES_FOLLOWUP.md).
