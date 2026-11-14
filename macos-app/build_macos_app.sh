#!/bin/bash
# build_macos_app.sh — build "BattlePew.app" from a BattleTag-macOS checkout.
#
# This script is the SINGLE SOURCE OF TRUTH for the .app build. The GitHub
# Actions workflow (.github/workflows/build-macos-app.yml) calls this script
# and adds no build logic of its own. Local builds and CI builds differ only
# in inputs (assets dir, output dir), never in steps.
#
# What it builds (see Docs/DESIGN_DECISIONS.md for why):
#   Swift AppKit + WKWebView shell (compiled with swiftc, no Xcode project)
#   + embedded python-build-standalone CPython
#   + pip-installed runtime deps (requirements-app.txt) in server/.deps
#   + the battletag server tree, copied verbatim (no freezing)
#   + original-game audio assets, ONLY when an assets dir is supplied
#
# Requirements: Apple Silicon Mac, macOS 13+, Xcode Command Line Tools
# (for swiftc/sips/iconutil/ditto), curl, ~2 GB free. Full Xcode is NOT
# required. The build is UNSIGNED (Aaron's decision, Nov 13 2026): no
# signing identity, no secrets, no notarization. See README.md for the
# first-launch Gatekeeper behavior that follows from that choice.
#
# Usage:
#   macos-app/build_macos_app.sh [--assets-dir DIR] [--output-dir DIR]
#                                [--package] [--smoke-test] [--clean]
#
#   --assets-dir DIR   Research-tree root containing game/, music-wav/,
#                      gun-sounds/ (the Antigravity handoff layout). When
#                      omitted the .app is built WITHOUT original audio:
#                      it runs fine, but announcer/music/UI sounds are
#                      silently absent. The Actions build always omits it
#                      (the audio is not in the repo and must not be
#                      committed there). Env override: BATTLETAG_ASSETS_SRC.
#   --output-dir DIR   Where to put the .app (default: macos-app/build).
#   --package          Also produce BattlePew-macOS-<ver>-<sha>.zip via ditto.
#   --smoke-test       After building, boot the bundled server on a scratch
#                      port and check GET /api/state. No hardware touched.
#   --clean            Delete the output dir first.
#
# Environment overrides (advanced):
#   PBS_URL / PBS_SHA256   Pin a different python-build-standalone asset.
#                          The checksum default below is secondhand (see
#                          the pin block) and MUST be re-verified against
#                          the release page before a trusted build; the
#                          script fails loudly on any mismatch.
#
# macOS /bin/bash is bash 3.2: this script avoids associative arrays,
# mapfile, &>>, and other bash-4-only constructs on purpose.
set -euo pipefail

# ---------------------------------------------------------------- pins ---

# Embedded Python: python-build-standalone, Apple Silicon, install_only
# (stripped: no debug symbols/docs). Immutable GitHub release asset.
#   Release page: github.com/astral-sh/python-build-standalone/releases
#   SHA256 below was taken from a third-party provenance listing for this
#   exact asset and has NOT been independently verified from Linux. The
#   script verifies the download against it and aborts on mismatch; if it
#   aborts, fetch the correct hash from the release page and either fix
#   PBS_SHA256 here or pass PBS_SHA256=<hash> in the environment.
PBS_VERSION="3.13.15"
PBS_RELEASE="20260807"
PBS_VARIANT="install_only_stripped"
PBS_FILE="cpython-${PBS_VERSION}+${PBS_RELEASE}-aarch64-apple-darwin-${PBS_VARIANT}.tar.gz"
PBS_URL_DEFAULT="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/cpython-${PBS_VERSION}%2B${PBS_RELEASE}-aarch64-apple-darwin-${PBS_VARIANT}.tar.gz"
PBS_SHA256_DEFAULT="dbadb0ffe46f8bace50daaf8a0c5fc6903c003690776da9eb5269e33c856bb53"

PBS_URL="${PBS_URL:-$PBS_URL_DEFAULT}"
PBS_SHA256="${PBS_SHA256:-$PBS_SHA256_DEFAULT}"

# Deployment floor for the Swift shell. LSMinimumSystemVersion in
# Info.plist must match this.
MACOS_DEPLOYMENT_TARGET="13.0"

# ------------------------------------------------------------ arguments ---

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_PROJECT_DIR="$SCRIPT_DIR"

ASSETS_DIR="${BATTLETAG_ASSETS_SRC:-}"
OUTPUT_DIR="$APP_PROJECT_DIR/build"
DO_PACKAGE=0
DO_SMOKE_TEST=0
DO_CLEAN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --assets-dir) ASSETS_DIR="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --package) DO_PACKAGE=1; shift ;;
        --smoke-test) DO_SMOKE_TEST=1; shift ;;
        --clean) DO_CLEAN=1; shift ;;
        --help|-h)
            sed -n '2,40p' "$0"
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

CACHE_DIR="$APP_PROJECT_DIR/.cache"
APP_PATH="$OUTPUT_DIR/BattlePew.app"
CONTENTS="$APP_PATH/Contents"
RESOURCES="$CONTENTS/Resources"
SERVER_DIR="$RESOURCES/server"
PYTHON_DIR="$RESOURCES/python"

log() { echo "[build] $*"; }
warn() { echo "[build] WARNING: $*" >&2; }
fail() { echo "[build] ERROR: $*" >&2; exit 1; }

# ------------------------------------------------------------ preflight ---

[ "$(uname -s)" = "Darwin" ] || fail "This script only runs on macOS (uname: $(uname -s))."
[ "$(uname -m)" = "arm64" ] || fail "Apple Silicon (arm64) only in v1; this Mac reports '$(uname -m)'. Intel builds are out of scope — see README.md."
for tool in swiftc curl tar shasum ditto iconutil sips rsync; do
    command -v "$tool" >/dev/null 2>&1 || fail "Required tool missing: $tool (install the Xcode Command Line Tools: xcode-select --install)"
done
[ -d "$REPO_ROOT/battletag" ] || fail "battletag/ not found under $REPO_ROOT — run from a BattleTag-macOS checkout."
[ -f "$REPO_ROOT/run_battletag.py" ] || fail "run_battletag.py not found under $REPO_ROOT."

# Version: single source is battletag/__init__.py __version__.
VERSION="$(sed -n 's/^__version__ *= *"\(.*\)".*/\1/p' "$REPO_ROOT/battletag/__init__.py" | head -1)"
[ -n "$VERSION" ] || fail "Could not read __version__ from battletag/__init__.py."
if [ -d "$REPO_ROOT/.git" ] && command -v git >/dev/null 2>&1; then
    BUILD_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "nogit")"
else
    BUILD_SHA="nogit-$(date +%Y%m%d%H%M%S)"
fi

if [ "$DO_CLEAN" -eq 1 ]; then
    log "Cleaning $OUTPUT_DIR"
    rm -rf "$OUTPUT_DIR"
fi
mkdir -p "$OUTPUT_DIR" "$CACHE_DIR"

log "Building BattlePew $VERSION ($BUILD_SHA) for macOS $MACOS_DEPLOYMENT_TARGET+ (arm64)"

# ------------------------------------------------- embedded Python fetch ---

TARBALL="$CACHE_DIR/$PBS_FILE"
if [ ! -f "$TARBALL" ]; then
    log "Downloading embedded Python $PBS_VERSION ($PBS_RELEASE)…"
    curl -fsSL -o "$TARBALL.tmp" "$PBS_URL" || fail "Download failed: $PBS_URL"
    mv "$TARBALL.tmp" "$TARBALL"
else
    log "Reusing cached $PBS_FILE"
fi

log "Verifying checksum…"
ACTUAL_SHA="$(shasum -a 256 "$TARBALL" | awk '{print $1}')"
if [ "$ACTUAL_SHA" != "$PBS_SHA256" ]; then
    echo "ERROR: checksum mismatch for $PBS_FILE" >&2
    echo "  expected: $PBS_SHA256" >&2
    echo "  actual:   $ACTUAL_SHA" >&2
    echo "The pinned hash may be stale or the download corrupt. Fetch the" >&2
    echo "correct SHA-256 from the python-build-standalone release page" >&2
    echo "($PBS_RELEASE) and re-run with PBS_SHA256=<hash>, or fix the pin." >&2
    rm -f "$TARBALL"
    exit 1
fi

EXTRACT_DIR="$CACHE_DIR/pbs-$PBS_VERSION-$PBS_RELEASE"
if [ ! -x "$EXTRACT_DIR/python/bin/python3" ]; then
    log "Extracting embedded Python…"
    rm -rf "$EXTRACT_DIR"
    mkdir -p "$EXTRACT_DIR"
    tar -xzf "$TARBALL" -C "$EXTRACT_DIR" || fail "Extraction failed."
fi
if [ -x "$EXTRACT_DIR/python/bin/python3" ]; then
    PBS_PYTHON_SRC="$EXTRACT_DIR/python"
else
    fail "Unexpected tarball layout: $EXTRACT_DIR/python/bin/python3 not found."
fi

# ---------------------------------------------------------- bundle shell ---

log "Assembling bundle at $APP_PATH"
rm -rf "$APP_PATH"
mkdir -p "$CONTENTS/MacOS" "$RESOURCES" "$SERVER_DIR"

# Embedded Python (ditto preserves symlinks/permissions).
ditto "$PBS_PYTHON_SRC" "$PYTHON_DIR"
PYTHON_BIN="$PYTHON_DIR/bin/python3"
[ -x "$PYTHON_BIN" ] || fail "Embedded interpreter not executable: $PYTHON_BIN"
"$PYTHON_BIN" --version || fail "Embedded interpreter does not run."

# Server tree, verbatim (no freezing: __file__-based paths keep working).
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
    "$REPO_ROOT/battletag/" "$SERVER_DIR/battletag/"
cp "$REPO_ROOT/run_battletag.py" "$SERVER_DIR/run_battletag.py"
mkdir -p "$SERVER_DIR/logs"

# ------------------------------------------------------- runtime deps ---

log "Installing runtime dependencies into server/.deps …"
DEPS_DIR="$SERVER_DIR/.deps"
mkdir -p "$DEPS_DIR"
if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
    log "Bootstrapping pip via ensurepip…"
    "$PYTHON_BIN" -m ensurepip --upgrade >/dev/null || fail "ensurepip failed."
fi
PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1 \
    "$PYTHON_BIN" -m pip install --prefer-binary --no-warn-script-location \
    --target "$DEPS_DIR" -r "$APP_PROJECT_DIR/requirements-app.txt" \
    || fail "pip install failed. If hidapi has no wheel for this Python, see README.md troubleshooting (Python pin)."
find "$DEPS_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# Sanity: the imports the server needs must resolve inside the bundle.
PYTHONPATH="$DEPS_DIR" "$PYTHON_BIN" -c "import fastapi, uvicorn, hid, qrcode, PIL; print('deps ok')" \
    || fail "Dependency import check failed inside the bundle."

# ------------------------------------------------------------- audio ---

AUDIO_INCLUDED="no"
if [ -n "$ASSETS_DIR" ]; then
    log "Copying audio assets from $ASSETS_DIR"
    [ -d "$ASSETS_DIR" ] || fail "--assets-dir not found: $ASSETS_DIR"
    GAME_ROOT="$ASSETS_DIR/game/Program Files/Ubisoft/BattleTag"
    HANDOFF_DIR="$SERVER_DIR/battle-tag-antigravity-handoff"
    copy_asset_dir() {
        # $1 = source dir, $2 = destination dir
        if [ -d "$1" ]; then
            mkdir -p "$2"
            ditto "$1" "$2"
            log "  copied $(basename "$1")"
        else
            warn "  missing asset dir (audio for this category will be silent): $1"
        fi
    }
    copy_asset_dir "$GAME_ROOT/System/Locale/En/Audio/GameMaster" \
        "$HANDOFF_DIR/game/Program Files/Ubisoft/BattleTag/System/Locale/En/Audio/GameMaster"
    copy_asset_dir "$GAME_ROOT/Data/Audio/Ui" \
        "$HANDOFF_DIR/game/Program Files/Ubisoft/BattleTag/Data/Audio/Ui"
    copy_asset_dir "$ASSETS_DIR/gun-sounds" "$HANDOFF_DIR/gun-sounds"
    # Music: copy ONLY the three tracks the server references. The full
    # music-wav dir is ~89 MB; musicupdating.wav is never referenced.
    if [ -d "$ASSETS_DIR/music-wav" ]; then
        mkdir -p "$HANDOFF_DIR/music-wav"
        for track in musicmenu.wav musicingame.wav musicintroduction.wav; do
            if [ -f "$ASSETS_DIR/music-wav/$track" ]; then
                cp "$ASSETS_DIR/music-wav/$track" "$HANDOFF_DIR/music-wav/$track"
            else
                warn "  missing music track: $track"
            fi
        done
        log "  copied music tracks (3 referenced)"
    else
        warn "  missing asset dir (music will be silent): $ASSETS_DIR/music-wav"
    fi
    AUDIO_INCLUDED="yes (from $ASSETS_DIR)"
else
    warn "No --assets-dir supplied: building WITHOUT original-game audio."
    warn "The app will run, but announcer/music/UI sounds will be silent."
    warn "See README.md 'Audio assets' for the local build with sound."
fi

# --------------------------------------------------------------- icon ---

ICON_INCLUDED="no"
ICNS_TMP="$OUTPUT_DIR/AppIcon.icns"
if [ -f "$APP_PROJECT_DIR/Resources/AppIcon-1024.png" ]; then
    log "Building icon from Resources/AppIcon-1024.png"
    bash "$APP_PROJECT_DIR/Scripts/make_icns.sh" \
        "$APP_PROJECT_DIR/Resources/AppIcon-1024.png" \
        "$OUTPUT_DIR/AppIcon.iconset" "$ICNS_TMP" || warn "Icon build failed; continuing without a custom icon."
elif [ -d "$APP_PROJECT_DIR/Resources/AppIcon.iconset" ]; then
    log "Building icon from shipped placeholder iconset"
    bash "$APP_PROJECT_DIR/Scripts/make_icns.sh" "" \
        "$APP_PROJECT_DIR/Resources/AppIcon.iconset" "$ICNS_TMP" || warn "Icon build failed; continuing without a custom icon."
fi
if [ -f "$ICNS_TMP" ]; then
    cp "$ICNS_TMP" "$RESOURCES/AppIcon.icns"
    ICON_INCLUDED="yes"
fi

# ---------------------------------------------------------- Swift shell ---

log "Compiling Swift shell (swiftc, macOS $MACOS_DEPLOYMENT_TARGET+)…"
export MACOSX_DEPLOYMENT_TARGET
swiftc -O -parse-as-library -o "$CONTENTS/MacOS/BattleTag" "$APP_PROJECT_DIR"/Sources/BattleTag/*.swift \
    || fail "swiftc failed."
file "$CONTENTS/MacOS/BattleTag" | grep -q "arm64" || fail "Built executable is not arm64."

# Info.plist (version/build substituted; bundle ID is fixed in the file).
sed -e "s/__VERSION__/$VERSION/g" -e "s/__BUILD__/$BUILD_SHA/g" \
    "$APP_PROJECT_DIR/Resources/Info.plist" > "$CONTENTS/Info.plist"
printf 'APPL????' > "$CONTENTS/PkgInfo"
touch "$APP_PATH"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_PATH" >/dev/null 2>&1 || true

# ---------------------------------------------------------- validation ---

for required in \
    "$CONTENTS/MacOS/BattleTag" \
    "$PYTHON_BIN" \
    "$SERVER_DIR/run_battletag.py" \
    "$SERVER_DIR/battletag/app.py" \
    "$SERVER_DIR/battletag/data/mode_sequences.json" \
    "$SERVER_DIR/battletag/static/index.html" \
    "$SERVER_DIR/battletag/static/hud.html" \
    "$SERVER_DIR/battletag/static/join.html" \
    "$CONTENTS/Info.plist"; do
    [ -e "$required" ] || fail "Bundle validation failed: missing $required"
done
log "Bundle validation passed."

# Manifest: what went into this build (for bug reports).
{
    echo "BattlePew $VERSION ($BUILD_SHA)"
    echo "Built: $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(hostname) ($(sw_vers -productVersion), $(uname -m))"
    echo "Embedded Python: $PBS_VERSION ($PBS_RELEASE, $PBS_VARIANT)"
    echo "Audio assets: $AUDIO_INCLUDED"
    echo "Custom icon: $ICON_INCLUDED"
    echo "Signing: UNSIGNED (no signature, no notarization — see README.md)"
} > "$OUTPUT_DIR/BUILD_MANIFEST.txt"

# --------------------------------------------------------- smoke test ---

if [ "$DO_SMOKE_TEST" -eq 1 ]; then
    log "Smoke test: booting bundled server on scratch port 18080…"
    SMOKE_LOG="$OUTPUT_DIR/smoke-test.log"
    SMOKE_PORT=18080
    (
        cd "$SERVER_DIR"
        PYTHONPATH="$DEPS_DIR" BATTLETAG_PORT="$SMOKE_PORT" \
            "$PYTHON_BIN" run_battletag.py --host 127.0.0.1 --port "$SMOKE_PORT" \
            >"$SMOKE_LOG" 2>&1 &
        echo $! >"$OUTPUT_DIR/.smoke.pid"
    )
    SMOKE_PID="$(cat "$OUTPUT_DIR/.smoke.pid")"
    SMOKE_OK=0
    for i in $(seq 1 60); do
        if curl -fsS --max-time 2 "http://127.0.0.1:$SMOKE_PORT/api/state" 2>/dev/null | grep -q '"phase"'; then
            SMOKE_OK=1
            break
        fi
        kill -0 "$SMOKE_PID" 2>/dev/null || break
        sleep 0.5
    done
    kill "$SMOKE_PID" 2>/dev/null || true
    sleep 1
    kill -9 "$SMOKE_PID" 2>/dev/null || true
    rm -f "$OUTPUT_DIR/.smoke.pid"
    if [ "$SMOKE_OK" -eq 1 ]; then
        log "Smoke test PASSED (/api/state answered with a snapshot)."
    else
        echo "---- smoke-test.log ----" >&2
        sed -n '1,60p' "$SMOKE_LOG" >&2
        fail "Smoke test FAILED: bundled server did not answer /api/state. See $SMOKE_LOG"
    fi
fi

# ------------------------------------------------------------ package ---

ZIP_PATH=""
if [ "$DO_PACKAGE" -eq 1 ]; then
    ZIP_PATH="$OUTPUT_DIR/BattlePew-macOS-${VERSION}-${BUILD_SHA}.zip"
    log "Packaging $ZIP_PATH"
    rm -f "$ZIP_PATH"
    (cd "$OUTPUT_DIR" && ditto -c -k --sequesterRsrc --keepParent "BattlePew.app" "$(basename "$ZIP_PATH")") \
        || fail "ditto packaging failed."
fi

# -------------------------------------------------------------- done ---

echo ""
log "Done: $APP_PATH"
log "Audio assets: $AUDIO_INCLUDED"
log "Manifest: $OUTPUT_DIR/BUILD_MANIFEST.txt"
if [ -n "$ZIP_PATH" ]; then
    log "Package: $ZIP_PATH"
fi
echo ""
echo "This build is UNSIGNED. First launch on a Mac that downloaded it will"
echo "show a Gatekeeper warning — see macos-app/README.md 'First launch' for"
echo "the exact steps (right-click > Open, or remove the quarantine flag)."
