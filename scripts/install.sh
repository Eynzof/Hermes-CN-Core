#!/usr/bin/env bash
# Hermes Agent bootstrap: clone, acquire uv/Python, then hand the checkout to
# the same completion an update runs -- command publication, product builds and
# post-build maintenance -- so a fresh install and a finished update land in one
# state. Heavy dependencies (tool binaries, browsers, node) are pm's job:
# `hermes pm install`.
#
# Stage protocol kept for Hermes-Setup:
#   --manifest            print the stage list as JSON
#   --stage NAME [--json] run one stage
#   --non-interactive     skip stages that need input
#   --include-desktop     build the desktop app too (products stage)
#   --verbose             stream every child command's output (the default
#                         off a terminal and in CI)
set -u

# Prevent uv from discovering config files (uv.toml, pyproject.toml) from the
# wrong user's home directory when running under sudo -u <user>.  See #21269.
# pm's own venv sync re-isolates (pm/environment.py), so this bootstrap
# hygiene can't break the locked sync the way it used to before pm owned it.
export UV_NO_CONFIG=1

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Configuration
REPO_URL_SSH="git@github.com:NousResearch/hermes-agent.git"
REPO_URL_HTTPS="https://github.com/NousResearch/hermes-agent.git"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_HOME_ARG_EXPLICIT=false
# INSTALL_DIR is resolved AFTER arg parsing and OS detection so we can pick an
# FHS-style layout for root installs.  Track whether the user gave us an
# explicit directory — if so we never override it.
if [ -n "${HERMES_INSTALL_DIR:-}" ]; then
    INSTALL_DIR="$HERMES_INSTALL_DIR"
    INSTALL_DIR_EXPLICIT=true
else
    INSTALL_DIR=""
    INSTALL_DIR_EXPLICIT=false
fi
PYTHON_VERSION="3.14"
PYTHON_SUPPORTED_RANGE=">=3.14"  # pyproject requires-python; keep in sync
NODE_VERSION="26"

# FHS-style root install layout (set by resolve_install_layout when applicable):
#   code at /usr/local/lib/hermes-agent, command at /usr/local/bin/hermes,
#   data still at /root/.hermes (HERMES_HOME).  Matches Claude Code / Codex CLI
#   and keeps Docker bind-mounted /root/ volumes lean.
ROOT_FHS_LAYOUT=false
DETECTED_BROWSER_EXECUTABLE=""
ISOLATED_LAYOUT=false

# Options
USE_VENV=true
RUN_SETUP=true
SKIP_BROWSER=false
SKIP_COMPUTER_USE=false
NO_SKILLS=false
BRANCH="main"
INSTALL_COMMIT=""
INSTALL_DIR="${HERMES_INSTALL_DIR:-}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
STAGE=""
WANT_MANIFEST=false
JSON=false
NON_INTERACTIVE=false
INCLUDE_DESKTOP=false
VERBOSE=false
SKIP_BROWSER=false
SKIP_COMPUTER_USE=false
INSTALL_LADDER=false
INSTALL_STARTED=0
INSTALL_RUN_ID=""
FAILURE_CLASS=""
INSTALL_SIGNAL=""
INSTALL_IN_STAGE=false

while [ $# -gt 0 ]; do
    case "$1" in
        --branch|-Branch|--commit|-Commit|--dir|--hermes-home|-HermesHome|--stage|-Stage)
            option="$1"
            if [ $# -lt 2 ] || [ -z "$2" ] || [[ "$2" == -* ]]; then
                printf '%s needs a value\n' "$option" >&2
                exit 2
            fi
            case "$option" in
                --branch|-Branch) BRANCH="$2" ;;
                --commit|-Commit) INSTALL_COMMIT="$2" ;;
                --dir) INSTALL_DIR="$2" ;;
                --hermes-home|-HermesHome) HERMES_HOME="$2" ;;
                --stage|-Stage) STAGE="$2" ;;
            esac
            shift 2 ;;
        --manifest|-Manifest) WANT_MANIFEST=true; shift ;;
        --json|-Json) JSON=true; shift ;;
        --non-interactive|-NonInteractive) NON_INTERACTIVE=true; shift ;;
        --skip-setup) NON_INTERACTIVE=true; shift ;;
        --skip-browser|--no-playwright|-SkipBrowser) SKIP_BROWSER=true; shift ;;
        --skip-computer-use|-SkipComputerUse) SKIP_COMPUTER_USE=true; shift ;;
        --include-desktop|-IncludeDesktop) INCLUDE_DESKTOP=true; shift ;;
        --verbose|-Verbose) VERBOSE=true; shift ;;
        -h|--help)
            echo "Usage: install.sh [--branch NAME] [--commit SHA] [--dir PATH]"
            echo "                  [--hermes-home PATH]"
            echo "                  [--manifest] [--stage NAME] [--json]"
            echo "                  [--non-interactive] [--include-desktop] [--verbose]"
            echo "                  [--skip-browser] [--skip-computer-use]"
            echo
            echo "  --skip-browser  Do not install the browser tools (agent-browser + Chromium)."
            echo "                  Alias: --no-playwright. Remembered by later"
            echo "                  installs and 'hermes update'; undo with 'hermes pm install agent-browser'."
            echo "  --skip-computer-use"
            echo "                  Do not install the computer-use driver (cua-driver). Remembered"
            echo "                  the same way; undo with 'hermes pm install cua-driver'."
            exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
            echo "Hermes Agent Installer"
            echo ""
            echo "Usage: install.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --no-venv      Don't create virtual environment"
            echo "  --skip-setup   Skip interactive setup wizard"
            echo "  --skip-browser Skip Playwright/Chromium install (browser tools won't work)"
            echo "  --skip-computer-use  Skip the cua-driver (Computer Use) install"
            echo "  --no-skills    Start with a blank slate — seed no bundled skills, and"
            echo "                   write \$HERMES_HOME/.no-bundled-skills so future"
            echo "                   'hermes update' runs never inject bundled skills either"
            echo "  --branch NAME  Git branch to install (default: main)"
            echo "  --commit SHA   Pin checkout to a specific commit after clone/update"
            echo "                   (ignored when it would roll an existing install back)"
            echo "  --force-commit Apply --commit even if it rolls the install backwards"
            echo "  --manifest     Print desktop bootstrap stage manifest as JSON"
            echo "  --stage NAME   Run one desktop bootstrap stage"
            echo "  --json         Print a JSON result frame for --stage"
            echo "  --non-interactive  Skip stages that require user input"
            echo "  --include-desktop  Also build the desktop app (apps/desktop -> Hermes.app)"
            echo "  --dir PATH     Installation directory"
            echo "                   default (non-root):  ~/.hermes/hermes-agent"
            echo "                   default (root, Linux): /usr/local/lib/hermes-agent"
            echo "  --isolated-dir PATH"
            echo "                 Self-contained install directory. Code/venv live at PATH,"
            echo "                 and Hermes data/config/logs/caches live at PATH/data"
            echo "  --hermes-home PATH  Data directory (default: ~/.hermes, or \$HERMES_HOME)"
            echo "  -h, --help     Show this help"
            echo ""
            echo "Notes:"
            echo "  When running as root on Linux, Hermes installs the code under"
            echo "  /usr/local/lib/hermes-agent and links the command into"
            echo "  /usr/local/bin/hermes (FHS layout — matches Claude Code / Codex CLI)."
            echo "  Data, config, sessions, and logs still live in \$HERMES_HOME"
            echo "  (default /root/.hermes).  This keeps Docker bind-mounted volumes"
            echo "  small and ensures the command is on PATH for all shells."
            echo "  Existing installs at \$HERMES_HOME/hermes-agent are preserved in-place."
            echo "  --ensure DEPS  Install only specified deps (comma-separated)"
            echo "                   Supported: node, browser, ripgrep, ffmpeg, coreutils (macOS only), rtk"
            echo "                   Does NOT clone repo or create venv"

            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

INSTALL_DIR="${INSTALL_DIR:-$HERMES_HOME/hermes-agent}"
export HERMES_HOME

INSTALL_LOG="$HERMES_HOME/logs/install.log"

# Same glyphs as the pre-pm installer. Colour only on a terminal, so CI
# transcripts and the Hermes-Setup driver read plain text.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_RED=$'\033[0;31m' C_GREEN=$'\033[0;32m' C_YELLOW=$'\033[0;33m'
    C_CYAN=$'\033[0;36m' C_MAGENTA=$'\033[0;35m' C_BOLD=$'\033[1m'
    C_DIM=$'\033[2m' C_NC=$'\033[0m'
else
    C_RED="" C_GREEN="" C_YELLOW="" C_CYAN="" C_MAGENTA="" C_BOLD="" C_DIM="" C_NC=""
fi

log() { printf '%s→%s %s\n' "$C_CYAN" "$C_NC" "$1"; }
log_success() { printf '%s✓%s %s\n' "$C_GREEN" "$C_NC" "$1"; }
log_warn() { printf '%s⚠%s %s\n' "$C_YELLOW" "$C_NC" "$1"; }
log_error() { printf '%s✗%s %s\n' "$C_RED" "$C_NC" "$1" >&2; }
# $2: closed failure class for the local install receipt (write_install_receipt).
fail() { STAGE_REASON="$1"; FAILURE_CLASS="${2:-other}"; log_error "$1"; exit 1; }

# One local receipt per full-ladder run, under the profile's shared-metrics dir.
# Only closed tokens and two timestamps (never STAGE_REASON, paths or URLs); the
# installer never sends anything. Hermes reports it as hermes.install.run on a
# later start only while shared metrics collection is on, and deletes it
# unreported when collection is off. Best effort: never fails the install.
write_install_receipt() {
    # $1 outcome, $2 failed stage, $3 failure class. One file per run: the first writer wins.
    # A failed write (full disk) removes its partial temp file: the reader never sees dotfiles.
    {
        local dir="$HERMES_HOME/telemetry/shared_metrics/pending_installs" id="$INSTALL_RUN_ID"
        [ -n "$id" ] && [ ! -e "$dir/$id.json" ] && mkdir -p "$dir" && {
            printf '{"id":"%s","installer":"install_sh","outcome":"%s","failed_stage":"%s","failure_class":"%s","started_at":%s,"finished_at":%s}\n' \
                "$id" "$1" "$2" "$3" "$INSTALL_STARTED" "$(date +%s)" > "$dir/.$id.tmp" \
                && mv -f "$dir/.$id.tmp" "$dir/$id.json" \
                || rm -f "$dir/.$id.tmp"
        }
    } >/dev/null 2>&1 || :
}

# Ladder signal handling. The traps only note the signal while a stage owns the
# terminal: bash runs a trap once the foreground stage returns, and that stage's
# result decides. A stage whose child caught Ctrl-C and succeeded (setup's
# provider picker: "Provider setup skipped.") lets the ladder carry on, as bash's
# own wait-and-cooperative-exit did before these traps existed. A stage that
# failed after the signal is recorded as `interrupted`, then the signal is
# re-raised so the exit status is the signal's. A TERM or HUP sent to the
# installer alone (not its process group) takes effect when the running stage
# returns, and the receipt names the stage the run stopped before. The stage
# stays in the foreground because a background stage's children ignore Ctrl-C
# under bash 3.2 (macOS /bin/bash), even after `trap -`.
install_signal() {
    # $1 signal name. Outside a stage (platform check, banner, between stages) stop now.
    INSTALL_SIGNAL="$1"
    [ "$INSTALL_IN_STAGE" = true ] || install_interrupted
}

install_interrupted() {
    # Record the stage the signal cut off, then die by that signal (exit code if it is blocked).
    local stage="${s:-prerequisites}" sig="${INSTALL_SIGNAL:-INT}" code=130
    case "$sig" in HUP) code=129 ;; TERM) code=143 ;; esac
    write_install_receipt failed "${stage//-/_}" interrupted
    trap - INT TERM HUP EXIT
    kill -s "$sig" "$$" 2>/dev/null
    exit "$code"
}

# Inside a ladder stage: a child the signal killed ends the stage like bash's own
# cooperative exit (its status is the signal's, 128+N); a child that caught the
# signal keeps its own result, and the stage's receipt reads `interrupted`.
stage_signal() {
    # $1 status of the command the signal arrived during, $2 this signal's 128+N.
    STAGE_INTERRUPTED=true
    [ "$1" -ne "$2" ] || exit "$2"
}
if [ "$ISOLATED_LAYOUT" = true ]; then
    # The installer later cd's into INSTALL_DIR.  Keep self-contained data paths
    # absolute so `--isolated-dir hermes-agent` always means
    # <original-cwd>/hermes-agent/data, not hermes-agent/hermes-agent/data.
    case "$INSTALL_DIR" in
        /*) ;;
        *) INSTALL_DIR="$PWD/$INSTALL_DIR" ;;
    esac
    if [ "$HERMES_HOME_ARG_EXPLICIT" = false ]; then
        HERMES_HOME="$INSTALL_DIR/data"
    else
        case "$HERMES_HOME" in
            /*) ;;
            *) HERMES_HOME="$PWD/$HERMES_HOME" ;;
        esac
    fi
fi

export HERMES_HOME

# Hermes-managed external tools directory.  Downloaded binaries (ripgrep, rtk,
# coreutils, ...) live here so broken global PATH copies cannot brick runtime.
MANAGED_TOOLS_DIR="${HERMES_HOME}/tools"
export MANAGED_TOOLS_DIR

# Keep Playwright browser binaries under Hermes data by default so local browser
# tooling follows the same profile/custom-root isolation as the rest of Hermes.
if [ -z "${PLAYWRIGHT_BROWSERS_PATH:-}" ]; then
    export PLAYWRIGHT_BROWSERS_PATH="$HERMES_HOME/cache/ms-playwright"
else
    export PLAYWRIGHT_BROWSERS_PATH
fi

# In self-contained installs, keep installer/package-manager caches inside the
# same data directory too.  Normal installs keep their historical cache defaults.
if [ "$ISOLATED_LAYOUT" = true ]; then
    export UV_CACHE_DIR="${UV_CACHE_DIR:-$HERMES_HOME/cache/uv}"
    export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$HERMES_HOME/cache/pip}"
    export npm_config_cache="${npm_config_cache:-$HERMES_HOME/cache/npm}"
fi

# ============================================================================
# Helper functions
# ============================================================================

print_banner() {
    printf '\n%s%s' "$C_MAGENTA" "$C_BOLD"
    printf '%s\n' "┌─────────────────────────────────────────────────────────┐"
    printf '%s\n' "│             ☤ Hermes Agent Installer                    │"
    printf '%s\n' "├─────────────────────────────────────────────────────────┤"
    printf '%s\n' "│  An open source AI agent by Nous Research.              │"
    printf '%s\n' "└─────────────────────────────────────────────────────────┘"
    printf '%s\n' "$C_NC"
}

# Interactive runs collapse child-process output (git, uv, pm, the builds)
# into one status line. CI, --verbose and a non-terminal stdout -- the
# Hermes-Setup --json driver, E2E transcripts -- keep the full stream those
# readers parse.
quiet_output() {
    [ "$VERBOSE" = true ] && return 1
    if [ -n "${CI:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ] || [ -n "${HERMES_INSTALL_VERBOSE:-}" ]; then
        return 1
    fi
    [ -t 1 ]
}

status_line() {
    local text="  $1" width=$(( $2 - 1 ))
    [ "${#text}" -le "$width" ] || text="${text:0:width}"
    printf '\r\033[K%s%s%s' "$C_DIM" "$text" "$C_NC"
}

# run_logged [--may-fail] LABEL CMD...: run CMD. Quiet mode shows LABEL with
# CMD's latest output line rewritten in place, appends everything to
# $INSTALL_LOG and, on failure, prints the tail and the log path; --may-fail
# is for probes whose failure the caller handles (no report). Otherwise LABEL
# is logged and the output streams untouched. Returns CMD's exit status.
run_logged() {
    local may_fail=false
    if [ "$1" = --may-fail ]; then may_fail=true; shift; fi
    local label="$1"; shift
    if ! quiet_output || ! { mkdir -p "${INSTALL_LOG%/*}" && : >> "$INSTALL_LOG"; } 2>/dev/null; then
        log "$label"
        "$@"
        return
    fi
    local start cols rc line shown
    start=$(( $(wc -l < "$INSTALL_LOG") + 1 ))
    cols="$(tput cols 2>/dev/null)" || cols=80
    [ "${cols:-0}" -gt 20 ] 2>/dev/null || cols=80
    printf '==> %s (%s)\n' "$label" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$INSTALL_LOG"
    status_line "$label" "$cols"
    # stdin closed: under `curl | bash` it is the script itself, and nothing
    # run here may prompt behind a status line.
    "$@" </dev/null 2>&1 | {
        while IFS= read -r line || [ -n "$line" ]; do
            line="${line%$'\r'}"
            printf '%s\n' "$line" >&3
            # git and uv redraw progress with bare CRs; show the newest.
            shown="${line##*$'\r'}"
            [ -z "$shown" ] || status_line "$label: $shown" "$cols"
        done
    } 3>>"$INSTALL_LOG"
    rc=${PIPESTATUS[0]}
    printf '\r\033[K'
    if [ "$rc" -ne 0 ] && [ "$may_fail" = false ]; then
        log_error "$label failed (exit $rc). Last output:"
        tail -n +"$(( start + 1 ))" "$INSTALL_LOG" | tail -n 20 | sed 's/^/    /' >&2
        printf '    full log: %s\n' "$INSTALL_LOG" >&2
    fi
    return "$rc"
}

# --- BEGIN GENERATED: bootstrap pins (scripts/gen-bootstrap-pins.py) ---
# Derived from pm/lock.json. DO NOT EDIT BY HAND:
# run scripts/gen-bootstrap-pins.py after a pin bump.
UV_PIN_VERSION="0.12.3"

# Sets UV_PIN_URL + UV_PIN_SHA256 for a <os>-<arch> target key.
uv_bootstrap_pin() {
    case "$1" in
        linux-x64)
            UV_PIN_URL="https://github.com/astral-sh/uv/releases/download/0.12.3/uv-x86_64-unknown-linux-gnu.tar.gz"
            UV_PIN_MIRROR="https://hermes-assets.nousresearch.com/upstream/sha256/600cf9a742aca00d292673b16b5acffaa7b8c269a364ad0c2e79498dcb1fe101"
            UV_PIN_SHA256="600cf9a742aca00d292673b16b5acffaa7b8c269a364ad0c2e79498dcb1fe101"
            ;;
        linux-arm64)
            UV_PIN_URL="https://github.com/astral-sh/uv/releases/download/0.12.3/uv-aarch64-unknown-linux-gnu.tar.gz"
            UV_PIN_MIRROR="https://hermes-assets.nousresearch.com/upstream/sha256/bb66cb52e7b1823aed1183630d8d8e5c958840d584a4c55ec10a4cfc168dcca2"
            UV_PIN_SHA256="bb66cb52e7b1823aed1183630d8d8e5c958840d584a4c55ec10a4cfc168dcca2"
            ;;
        linux-x64-musl)
            UV_PIN_URL="https://github.com/astral-sh/uv/releases/download/0.12.3/uv-x86_64-unknown-linux-musl.tar.gz"
            UV_PIN_MIRROR="https://hermes-assets.nousresearch.com/upstream/sha256/0643b9fb8c9fb27458e709ce6ff939695013c41975ff7b02d3f3b138d8d4bdb3"
            UV_PIN_SHA256="0643b9fb8c9fb27458e709ce6ff939695013c41975ff7b02d3f3b138d8d4bdb3"
            ;;
        linux-arm64-musl)
            UV_PIN_URL="https://github.com/astral-sh/uv/releases/download/0.12.3/uv-aarch64-unknown-linux-musl.tar.gz"
            UV_PIN_MIRROR="https://hermes-assets.nousresearch.com/upstream/sha256/fa513fca1eb2913334c944fe9adbdd410274a1cbe8dd05d03699a9eb85311d4e"
            UV_PIN_SHA256="fa513fca1eb2913334c944fe9adbdd410274a1cbe8dd05d03699a9eb85311d4e"
            ;;
        darwin-x64)
            UV_PIN_URL="https://github.com/astral-sh/uv/releases/download/0.12.3/uv-x86_64-apple-darwin.tar.gz"
            UV_PIN_MIRROR="https://hermes-assets.nousresearch.com/upstream/sha256/4c9f52262a14da336e4a42ed24992d12d0c956acde87619e4611d321dffa602b"
            UV_PIN_SHA256="4c9f52262a14da336e4a42ed24992d12d0c956acde87619e4611d321dffa602b"
            ;;
        darwin-arm64)
            UV_PIN_URL="https://github.com/astral-sh/uv/releases/download/0.12.3/uv-aarch64-apple-darwin.tar.gz"
            UV_PIN_MIRROR="https://hermes-assets.nousresearch.com/upstream/sha256/546f7f8a6c70ff13a3a9d2bc958db3427298cebf3e0cb756f9177133b7068843"
            UV_PIN_SHA256="546f7f8a6c70ff13a3a9d2bc958db3427298cebf3e0cb756f9177133b7068843"
            ;;
        *)
            UV_PIN_URL=""
            UV_PIN_SHA256=""
            return 1
            ;;
    esac
}
# --- END GENERATED: bootstrap pins ---

uv_bootstrap_target() {
    # Map this host to a pm/lock.json target key (<os>-<arch>).
    local _arch
    case "$(uname -m)" in
        arm64|aarch64) _arch="arm64" ;;
        x86_64|amd64)  _arch="x64" ;;
        *) return 1 ;;
    esac
    case "$(uname -s)" in
        Linux)
            # Same precedence as pm/store.py::_is_musl_libc: the native
            # userland's ELF interpreter decides; ldd and a musl loader on
            # disk are fallbacks only (a glibc host may carry musl as a
            # secondary toolchain, and minimal musl roots may lack ldd).
            local _libc="" _probe _head
            for _probe in /bin/sh /bin/ls; do
                _head="$(head -c 8192 "$_probe" 2>/dev/null | LC_ALL=C tr -d '\000')" || continue
                [[ "$_head" == $'\x7f'ELF* ]] || continue
                case "$_head" in
                    *ld-musl-*) _libc="musl"; break ;;
                    *ld-linux*) _libc="glibc"; break ;;
                esac
            done
            if [[ -z "$_libc" ]]; then
                _libc="$(ldd --version 2>&1 || true)"
                _libc="${_libc,,}"
            fi
            if [[ "$_libc" == *musl* ]]; then
                echo "linux-$_arch-musl"
            elif [[ "$_libc" == *glibc* || "$_libc" == *"gnu libc"* || "$_libc" == *"gnu c library"* ]]; then
                echo "linux-$_arch"
            elif compgen -G '/lib/ld-musl-*.so.1' >/dev/null; then
                echo "linux-$_arch-musl"
            else
                echo "linux-$_arch"
            fi
            ;;
        Darwin) echo "darwin-$_arch" ;;
        *) return 1 ;;
    esac
}

# Provision uv for this host from the pinned pm/lock.json artifact. Stages
# the EXACT artifact pm itself uses into the same store slot
# (<store>/uv-<version>-<target>/, the store pm's store_root() resolves),
# sha256-verified, so the byte authority is pm/lock.json - no astral-latest,
# no curl|sh.
UV_CMD=""
ensure_uv() {
    [ -n "$UV_CMD" ] && return 0
    # Always the pinned artifact, never a uv already on PATH: Hermes runs only
    # its own packaged toolchain.
    local _target
    if ! _target="$(uv_bootstrap_target)"; then
        fail "no pinned uv build for this platform ($(uname -s) $(uname -m)); Hermes does not support this host" unsupported_platform
    fi
    if ! uv_bootstrap_pin "$_target"; then
        fail "no pinned uv artifact for $_target; Hermes does not support this host" unsupported_platform
    fi
    local _store="${HERMES_RUNTIME_DIR:-$HERMES_HOME/tools}"
    local _entry="$_store/uv-$UV_PIN_VERSION-$_target"
    UV_CMD="$_entry/uv"
    if [ ! -x "$UV_CMD" ]; then
        log "Downloading uv $UV_PIN_VERSION ($_target)"
        local _tmp
        # no-tmp: ok — last-resort fallback when mktemp itself is missing
        _tmp="$(mktemp -d 2>/dev/null || echo "/tmp/hermes-uv-bootstrap.$$")"
        mkdir -p "$_tmp"
        local _fetched_from="$UV_PIN_URL"
        # Only network availability failures permit trying identical mirrored bytes.
        if curl -LsSf "$UV_PIN_URL" -o "$_tmp/uv.tar.gz"; then
            :
        else
            local _curl_status=$?
            case "$_curl_status" in
                5|6|7|18|22|28|52|55|56) ;;
                *) rm -rf "$_tmp"; fail "failed to download pinned uv from $UV_PIN_URL (curl $_curl_status)" download_failed ;;
            esac
            if [ -n "${UV_PIN_MIRROR:-}" ] && curl -LsSf "$UV_PIN_MIRROR" -o "$_tmp/uv.tar.gz"; then
                _fetched_from="$UV_PIN_MIRROR"
            else
                rm -rf "$_tmp"
                fail "failed to download pinned uv from $UV_PIN_URL or ${UV_PIN_MIRROR:-no mirror}" download_failed
            fi
        fi
        local _digest
        if command -v sha256sum >/dev/null 2>&1; then
            _digest="$(sha256sum "$_tmp/uv.tar.gz" | cut -d' ' -f1)"
        else
            _digest="$(shasum -a 256 "$_tmp/uv.tar.gz" | cut -d' ' -f1)"
        fi
        if [ "$_digest" != "$UV_PIN_SHA256" ]; then
            rm -rf "$_tmp"
            fail "uv download digest mismatch from $_fetched_from (expected $UV_PIN_SHA256, got $_digest)" download_digest_mismatch
        fi
        if ! tar -xzf "$_tmp/uv.tar.gz" -C "$_tmp"; then
            rm -rf "$_tmp"
            fail "failed to extract pinned uv archive" uv_unusable
        fi
        local _unpacked
        _unpacked="$(find "$_tmp" -mindepth 1 -maxdepth 2 -name uv -type f | head -n1)"
        if [ -z "$_unpacked" ]; then
            rm -rf "$_tmp"
            fail "uv binary not found in the downloaded archive" uv_unusable
        fi
        mkdir -p "$_entry"
        mv "$_unpacked" "$UV_CMD"
        [ -f "$(dirname "$_unpacked")/uvx" ] && mv "$(dirname "$_unpacked")/uvx" "$_entry/uvx"
        chmod +x "$UV_CMD"
        chmod +x "$_entry/uvx" 2>/dev/null || true
        rm -rf "$_tmp"
    fi
    # Bootstrap keeps the installer private; only UV_CMD invokes it.
    if ! "$UV_CMD" --version >/dev/null 2>&1; then
        fail "pinned uv staged but does not run on this host" uv_unusable
    fi
    log_success "uv ready ($("$UV_CMD" --version 2>/dev/null))"
}

check_platform() {
    # Termux is Linux by uname, but this installer builds a glibc source
    # install the phone cannot run (no Android wheels in the lock). The
    # signed APT package is the only supported shape there.
    if [ -n "${TERMUX_VERSION:-}" ] || case "${PREFIX:-}" in *com.termux/files/usr*) true ;; *) false ;; esac; then
        fail "Termux is installed from its APT repository, not install.sh: pkg install hermes-agent (setup: https://hermes-agent.nousresearch.com/docs/getting-started/termux)" unsupported_platform
    fi
    case "$(uname -s 2>/dev/null)" in
        Linux*) : ;;
        Darwin*) : ;;
        *) fail "unsupported platform: $(uname -s). On Windows use install.ps1." unsupported_platform ;;
    esac
}

json_string() {
    local value="$1" code char escaped
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    for ((code = 1; code < 32; code++)); do
        printf -v char '\\%03o' "$code"
        printf -v char '%b' "$char"
        printf -v escaped '\\u%04x' "$code"
        value="${value//"$char"/$escaped}"
    done
    printf '"%s"' "$value"
}

json_frame() {
    # $1 ok, $2 stage, $3 skipped, $4 reason
    if [ -n "${4:-}" ]; then
        printf '{"ok":%s,"stage":%s,"skipped":%s,"reason":%s}\n' "$1" "$(json_string "$2")" "$3" "$(json_string "$4")"
    else
        printf '{"ok":%s,"stage":%s,"skipped":%s}\n' "$1" "$(json_string "$2")" "$3"
    fi
}

stage_result() {
    local code="$1" ok=false reason="${STAGE_REASON:-}"
    if [ "$code" -eq 0 ]; then
        ok=true
    else
        reason="${reason:-stage failed (exit $code)}"
    fi
    if [ "$JSON" = true ]; then
        json_frame "$ok" "$STAGE" "${STAGE_SKIPPED:-false}" "$reason"
    fi
    if [ "$ok" = false ] && [ "${INSTALL_LADDER:-false}" = true ]; then
        local class="${FAILURE_CLASS:-other}"
        # A user abort beats the stage's own class: a child that caught Ctrl-C and exited 1
        # (pm, source_completion, `hermes setup`) still reaches the stage's `|| fail ... <class>`.
        case "$code" in 129|130|143) class=interrupted ;; esac
        [ "${STAGE_INTERRUPTED:-false}" = false ] || class=interrupted
        write_install_receipt failed "${STAGE//-/_}" "$class"
    fi
}

# The single authoritative stage list: emit_manifest prints it AND the
# no-flag ladder runs it. `products` is the shared completion tail -- the same
# call `hermes update` makes -- so the manifest and the run cannot disagree.
# `desktop` stays directly dispatchable via --stage for external callers, but
# is never listed: --include-desktop selects the desktop product inside
# `products` instead of adding a second build stage.
stage_names() {
    printf '%s\n' prerequisites repository venv python-deps config products setup gateway complete
}

# "title|category|needs_user_input".
products_record() {
    if [ "$INCLUDE_DESKTOP" = true ]; then
        echo "Install command and app + desktop|runtime|false"
    else
        echo "Install command and app|runtime|false"
    fi
}

# "$1" stage name -> its manifest record fields (title|category|needs_user_input).
stage_record() {
    case "$1" in
        prerequisites) echo "System prerequisites|runtime|false" ;;
        repository)    echo "Download Hermes Agent|runtime|false" ;;
        venv)          echo "Create Python environment|runtime|false" ;;
        python-deps)   echo "Install Python dependencies|runtime|false" ;;
        config)        echo "Prepare config and skills|configuration|false" ;;
        products)      products_record ;;
        setup)         echo "Configure API keys and settings|configuration|true" ;;
        gateway)       echo "Configure gateway service|configuration|true" ;;
        desktop)       echo "Build desktop app|runtime|false" ;;
        complete)      echo "Finish install|runtime|false" ;;
    esac
}

emit_manifest() {
    printf '%s' '{"protocol_version":1,"stages":['
    _sep=""
    for _s in $(stage_names); do
        IFS='|' read -r _title _category _needs <<< "$(stage_record "$_s")"
        printf '%s{"name":"%s","title":"%s","category":"%s","needs_user_input":%s}' \
            "$_sep" "$_s" "$_title" "$_category" "$_needs"
        _sep=","
    done
    printf '%s\n' ']}'
}

stage_prerequisites() {
    command -v git >/dev/null 2>&1 || fail "git is required. Install it with your system package manager." git_missing
    command -v curl >/dev/null 2>&1 || fail "curl is required. Install it with your system package manager." curl_missing
    # PM's Node on musl is the unofficial-builds musl archive, which links the
    # system libstdc++; without it every node/npm stage fails verification.
    local _target
    _target="$(uv_bootstrap_target 2>/dev/null)" || _target=""
    if [[ "$_target" == *-musl ]]; then
        local _libdir _stdcxx=""
        for _libdir in /lib /usr/lib /usr/local/lib; do
            compgen -G "$_libdir/libstdc++.so.6*" >/dev/null && { _stdcxx=yes; break; }
        done
        [ -n "$_stdcxx" ] || fail "musl host: the Node.js runtime needs the system libstdc++. Install it (Alpine: apk add libstdc++, Void: xbps-install libstdc++) and re-run." libstdcxx_missing
    fi
    # glibc Node links libatomic.so.1, absent on minimal Debian/RHEL hosts. PM
    # installs the distro package with `sudo -n` under its install lock, so
    # cache sudo credentials now, while the terminal can answer the prompt.
    if [ "$NON_INTERACTIVE" != true ] && [[ "$_target" == linux-* && "$_target" != *-musl ]] \
        && [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && has_terminal \
        && ! { ldconfig -p 2>/dev/null || /sbin/ldconfig -p 2>/dev/null; } | grep -q 'libatomic\.so\.1'; then
        log "Node.js needs libatomic.so.1; sudo may ask for your password to install it"
        sudo -v </dev/tty || log_warn "sudo failed; if Node.js fails, install libatomic (Debian/Ubuntu: libatomic1) and re-run"
    fi
    log_success "prerequisites ok (git, curl)"
}

stage_repository() {
    local pinned=false
    # An interrupted clone from an older installer can leave a .git with no
    # initial commit, where stash/checkout abort ("You do not have the
    # initial commit yet", #40998). Move it aside -- never delete it, it may
    # hold something the user wants -- and clone fresh below.
    if [ -d "$INSTALL_DIR/.git" ] && ! git -C "$INSTALL_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
        local broken
        broken="${INSTALL_DIR}.broken-$(date -u +%Y%m%d-%H%M%S)"
        log_warn "$INSTALL_DIR has no commits (interrupted clone); moving it aside to $broken"
        mv "$INSTALL_DIR" "$broken" || fail "cannot move $INSTALL_DIR aside" filesystem_error
    fi
    if [ -d "$INSTALL_DIR/.git" ]; then
        log "Updating $INSTALL_DIR ($BRANCH)"
        # An explicit HERMES_REPO_URL names the source for reruns too, not
        # just the first clone.
        if [ -n "${HERMES_REPO_URL:-}" ]; then
            git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL" || fail "cannot point origin at $REPO_URL" git_fetch_failed
        fi
        # Explicit refspec: a tag-pinned --single-branch checkout from an older
        # installer maps only the tag, so a by-name fetch writes FETCH_HEAD and
        # never the origin/$BRANCH everything below resolves (#125112).
        # git 2.53+ aborts fetches into a partial clone whose packs lack a .promisor
        # marker (#124272), and an install stuck there never fetches the updater that
        # heals it. Marking is idempotent and never rewrites objects.
        if [ "$(git -C "$INSTALL_DIR" config --bool --get remote.origin.promisor)" = true ]; then
            local pack
            for pack in "$INSTALL_DIR"/.git/objects/pack/pack-*.pack; do
                if [ -f "$pack" ] && [ ! -e "${pack%.pack}.promisor" ]; then
                    : > "${pack%.pack}.promisor" || log_warn "could not mark $pack as a partial-clone pack"
                fi
            done
            # Existing treeless checkout: same commit-graph lazy-fetch loop guard (#127711).
            git -C "$INSTALL_DIR" config maintenance.commit-graph.enabled false \
                || log_warn "could not disable maintenance.commit-graph.enabled in $INSTALL_DIR"
            git -C "$INSTALL_DIR" config gc.writeCommitGraph false \
                || log_warn "could not disable gc.writeCommitGraph in $INSTALL_DIR"
            git -C "$INSTALL_DIR" config fetch.writeCommitGraph false \
                || log_warn "could not disable fetch.writeCommitGraph in $INSTALL_DIR"
            # A treeless (tree:0) checkout from a late-September installer downloads whole
            # directory snapshots again on every history walk (#129514). Fetch its trees once; the
            # new filter is recorded only after that succeeds, so `hermes update` retries otherwise.
            if [ "$(git -C "$INSTALL_DIR" config --get remote.origin.partialclonefilter)" = tree:0 ]; then
                if run_logged --may-fail "Fetching directory history once (treeless checkout)" \
                    git -C "$INSTALL_DIR" -c gc.auto=0 -c maintenance.auto=false fetch --refetch \
                    --filter=blob:none origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"; then
                    git -C "$INSTALL_DIR" config remote.origin.partialclonefilter blob:none \
                        || log_warn "could not record the blobless filter in $INSTALL_DIR"
            ;;
        linux)
            local sudo_cmd=""
            if [ "$(id -u 2>/dev/null || echo 1000)" -ne 0 ]; then
                command -v sudo >/dev/null 2>&1 && sudo_cmd="sudo"
            fi
            log_info "Attempting to install a C++ compiler automatically..."
            case "$DISTRO" in
                ubuntu|debian)
                    log_info "Installing build-essential via apt..."
                    $sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 || true
                    $sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential >/dev/null 2>&1 || true
                    ;;
                fedora)
                    log_info "Installing gcc-c++ via dnf..."
                    $sudo_cmd dnf install -y gcc-c++ >/dev/null 2>&1 || true
                    ;;
                arch)
                    log_info "Installing base-devel via pacman..."
                    $sudo_cmd pacman -S --noconfirm base-devel >/dev/null 2>&1 || true
                    ;;
            esac
            if command -v g++ &> /dev/null || command -v clang++ &> /dev/null; then
                log_success "C++ compiler installed"
                HAS_CXX_COMPILER=true
                return 0
            fi
            ;;
    esac

    log_warn "Could not install a C++ compiler automatically."
    log_warn "Node steps that compile native modules (e.g. node-pty) will fail below until one is installed."
    log_info "Install it manually, then re-run this installer:"
    case "$OS" in
        linux)
            case "$DISTRO" in
                ubuntu|debian) log_info "  sudo apt install build-essential" ;;
                fedora)        log_info "  sudo dnf install gcc-c++" ;;
                arch)          log_info "  sudo pacman -S base-devel" ;;
                *)             log_info "  Install a C++ compiler (g++/gcc-c++) via your package manager" ;;
            esac
            ;;
        android)
            log_info "  pkg install clang"
            ;;
        macos)
            log_info "  xcode-select --install"
            ;;
    esac
    return 1
}

# The dependency tree supports Node 22.22+, 24.11+, and 26+. nanoid 6 excludes
# Node 23 and 25 while its >=26 arm accepts later releases, and @babel/* 8.x
# requires ^22.18.0 || >=24.11.0 — so accepting 23/25 or an early Node 24
# here only defers the failure to `npm ci` under engine-strict. Keep this in
# sync with the root package.json. Anything outside the supported lines is
# replaced with the Hermes-managed Node $NODE_VERSION.
node_satisfies_build() {
    local ver="${1#v}"
    # Pre-release builds are rejected outright, however new they are. `node-pty`
    # ships no Linux prebuild, so every install compiles it with node-gyp, which
    # fetches the headers named by `process.release.headersUrl` — a URL only
    # published for final releases. nodejs.org/dist/latest-v26.x currently
    # serves node-v26.8.0-<os>-<arch>.tar.xz whose binary reports
    # v26.8.0-alpha.0.0.0; its headers 404, and the install dies at
    # `node-gyp rebuild` with the underlying error swallowed by the
    # `node scripts/prebuild.js || node-gyp rebuild` fallback.
    case "$ver" in *-*) return 1 ;; esac
    local major="${ver%%.*}"
    local minor="${ver#*.}"; minor="${minor%%.*}"
    case "$major" in ''|*[!0-9]*) return 1 ;; esac
    case "$minor" in ''|*[!0-9]*) minor=0 ;; esac
    if [ "$major" -eq 22 ] && [ "$minor" -ge 22 ]; then return 0; fi
    if [ "$major" -eq 24 ] && [ "$minor" -ge 11 ]; then return 0; fi
    if [ "$major" -ge 26 ]; then return 0; fi
    return 1
}

# npm 11.10.0–11.16.x honor `min-release-age` but ignore
# `min-release-age-exclude`, both of which `.npmrc` sets. That combination
# applies the 14-day age gate to packages we deliberately exempted, so every
# install fails ETARGET on a freshly published dependency. The root
# package.json excludes that band via `engines.npm`, and `engine-strict=true`
# makes it fatal — so a system npm in the band cannot install this repo, no
# matter how new its Node is. Returns 0 when the npm is usable.
npm_supports_npmrc() {
    local ver="${1#v}"
    local major="${ver%%.*}"
    local minor="${ver#*.}"; minor="${minor%%.*}"
    case "$major" in ''|*[!0-9]*) return 1 ;; esac
    case "$minor" in ''|*[!0-9]*) minor=0 ;; esac
    # The bad band is 11.10.0 through 11.16.x.
    if [ "$major" -eq 11 ] && [ "$minor" -ge 10 ] && [ "$minor" -le 16 ]; then
        return 1
    fi
    return 0
}

check_node() {
    log_info "Checking Node.js (for browser tools)..."

    # Repair pre-existing Hermes-managed installs where `npm install -g` lands
    # off PATH. No-op when there's no managed Node, so this is safe to run on
    # every install — including re-runs that skip the Node (re)install below.
    configure_managed_node_npm_prefix

    # The system toolchain is only usable when BOTH halves work: a Node new
    # enough for the desktop build AND an npm that can read our .npmrc. A
    # bad-band npm (see npm_supports_npmrc) fails `npm ci` outright, and the
    # managed Node we install instead bundles one that works.
    #
    # npm must actually be reachable, not just node: a stray `node` symlink
    # without a sibling npm (leftover from a node version manager) makes
    # `command -v node` succeed while every later `npm install` silently
    # fails and the desktop build dies with an opaque "Node.js / npm
    # unavailable" (#77003). Node only counts as found when npm resolves on
    # the same PATH.
    if command -v node &> /dev/null && command -v npm &> /dev/null \
        && node_satisfies_build "$(node --version)"; then
        if npm_supports_npmrc "$(npm --version 2>/dev/null)"; then
            log_success "Node.js $(node --version) found"
            HAS_NODE=true
            return 0
        fi
        log_warn "npm $(npm --version) cannot honor this repo's .npmrc (npm 11.10-11.16 ignore"
        log_warn "min-release-age-exclude) — installing Hermes-managed Node $NODE_VERSION instead..."
        install_node
        return
    fi

    # Prefer a Hermes-managed Node from a previous run over a too-old system one.
    if [ -x "$HERMES_HOME/node/bin/node" ] && [ -x "$HERMES_HOME/node/bin/npm" ] \
        && node_satisfies_build "$("$HERMES_HOME/node/bin/node" --version)"; then
        export PATH="$HERMES_HOME/node/bin:$PATH"
        log_success "Node.js $("$HERMES_HOME/node/bin/node" --version) found (Hermes-managed)"
        HAS_NODE=true
        return 0
    fi

    if command -v node &> /dev/null && ! command -v npm &> /dev/null; then
        log_warn "node found but npm is not on PATH (stray node symlink?) — installing Hermes-managed Node $NODE_VERSION LTS..."
    elif command -v node &> /dev/null; then
        log_warn "Node.js $(node --version) is unsupported (Hermes requires Node 22.22+, 24.11+, or 26+) — installing Hermes-managed Node $NODE_VERSION..."
    elif [ "$DISTRO" = "termux" ]; then
        log_info "Node.js not found — installing Node.js via pkg..."
    else
        log_info "Node.js not found — installing Node.js $NODE_VERSION LTS..."
    fi
    install_node
}

# Download and adopt one Node release line (e.g. 26) into ~/.hermes/node/.
#
# Split out of install_node() so the caller can walk a list of candidate lines.
# A line is rejected — and the caller should try an older one — when nodejs.org
# publishes no tarball for this platform, when the download or extraction
# fails, or when the tree it ships fails node_satisfies_build.
#
# Returns 0 with the tree in place, PATH exported and HAS_NODE=true; 1 when the
# line is unusable. Nothing on disk is replaced until the candidate passes, so
# a rejected line leaves any existing managed Node untouched.
install_node_line() {
    local node_line="$1"
    local node_os="$2"
    local node_arch="$3"

    # Resolve the latest v${node_line}.x.x tarball name from the index page
    local index_url="https://nodejs.org/dist/latest-v${node_line}.x/"
    local tarball_name=""
    # `tar xf` shells out to xz for .tar.xz; minimal Debian/DietPi/WSL images ship tar without it
    # and the extract dies mid-way ("xz: Cannot exec"). Only pick .tar.xz when xz is present (#11197).
    if command -v xz >/dev/null 2>&1; then
        tarball_name=$(curl -fsSL "$index_url" \
            | grep -oE "node-v${node_line}\.[0-9]+\.[0-9]+-${node_os}-${node_arch}\.tar\.xz" \
            | head -1)
    else
        log_info "xz not found — using the .tar.gz Node.js archive"
    fi

    # Fallback to .tar.gz if .tar.xz not available (or xz is missing)
    if [ -z "$tarball_name" ]; then
        tarball_name=$(curl -fsSL "$index_url" \
            | grep -oE "node-v${node_line}\.[0-9]+\.[0-9]+-${node_os}-${node_arch}\.tar\.gz" \
            | head -1)
    fi

    if [ -z "$tarball_name" ]; then
        log_warn "Could not find Node.js $node_line binary for $node_os-$node_arch"
        return 1
    fi

    local download_url="${index_url}${tarball_name}"
    local tmp_dir
    tmp_dir=$(mktemp -d)

    log_info "Downloading $tarball_name..."
    if ! curl -fsSL "$download_url" -o "$tmp_dir/$tarball_name"; then
        log_warn "Download failed"
        rm -rf "$tmp_dir"
        return 1
    fi

    log_info "Extracting to $HERMES_HOME/node/..."
    if [[ "$tarball_name" == *.tar.xz ]]; then
        tar xf "$tmp_dir/$tarball_name" -C "$tmp_dir"
    else
        tar xzf "$tmp_dir/$tarball_name" -C "$tmp_dir"
    fi

    local extracted_dir
    extracted_dir=$(ls -d "$tmp_dir"/node-v* 2>/dev/null | head -1)

    if [ ! -d "$extracted_dir" ]; then
        log_warn "Extraction failed"
        rm -rf "$tmp_dir"
        return 1
    fi

    # Trust the binary, not the filename. A tarball named for a final release
    # can still carry a pre-release build — latest-v26.x serves
    # node-v26.8.0-<os>-<arch>.tar.xz whose `node --version` is
    # v26.8.0-alpha.0.0.0 — and adopting it leaves node-pty unbuildable for the
    # life of the install. Probe the extracted tree before it replaces anything.
    local candidate_ver
    candidate_ver=$("$extracted_dir/bin/node" --version 2>/dev/null)
    if ! node_satisfies_build "$candidate_ver"; then
        log_warn "Node.js ${candidate_ver:-unreadable} from ${index_url} cannot build native modules — trying an older release line..."
        rm -rf "$tmp_dir"
        return 1
    fi

    # Place into HERMES_HOME/node/.  Non-isolated installs also symlink
    # node/npm/npx into the same bin dir the hermes command uses
    # (get_command_link_dir): /usr/local/bin for root FHS installs, $PREFIX/bin
    # on Termux, ~/.local/bin otherwise. Isolated installs keep them private
    # and rely on the hermes launcher PATH instead.
    rm -rf "$HERMES_HOME/node"
    mkdir -p "$HERMES_HOME"
    mv "$extracted_dir" "$HERMES_HOME/node"
    rm -rf "$tmp_dir"

    # Node's official linux-x64 builds (observed: v26.7.0) link
    # libatomic.so.1, which minimal Debian/Ubuntu images do not ship —
    # the freshly downloaded binary then fails to start. Install the
    # library up front, best-effort; the version probe below reports
    # clearly if the binary still cannot run (#87460).
    if [ "$OS" = "linux" ] && { [ "$DISTRO" = "ubuntu" ] || [ "$DISTRO" = "debian" ]; }; then
        if command -v apt-get >/dev/null 2>&1; then
            local sudo_cmd=""
            if [ "$(id -u 2>/dev/null || echo 1000)" -ne 0 ]; then
                command -v sudo >/dev/null 2>&1 && sudo_cmd="sudo"
            fi
            $sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libatomic1 >/dev/null 2>&1 || true
        fi
    fi

    local node_link_dir
    node_link_dir="$(get_command_link_dir)"
    if [ "$ISOLATED_LAYOUT" != true ]; then
        mkdir -p "$node_link_dir"
        ln -sf "$HERMES_HOME/node/bin/node" "$node_link_dir/node"
        ln -sf "$HERMES_HOME/node/bin/npm"  "$node_link_dir/npm"
        ln -sf "$HERMES_HOME/node/bin/npx"  "$node_link_dir/npx"
    fi

    configure_managed_node_npm_prefix

    export PATH="$HERMES_HOME/node/bin:$PATH"

    local installed_ver
    if ! installed_ver=$("$HERMES_HOME/node/bin/node" --version 2>&1); then
        # The adopted Node exists but cannot start (observed: missing
        # libatomic.so.1 on minimal Debian/Ubuntu, #87460). Degrade loudly,
        # surface the loader's real error, and remove the broken tree and
        # bin links so later steps and retry runs start clean. Signal the
        # caller to try an older release line rather than claiming success.
        log_error "Downloaded Node.js failed to start:"
        printf '%s\n' "$installed_ver" >&2
        log_info "On Debian/Ubuntu the usual fix is: sudo apt-get install -y libatomic1"
        rm -rf "$HERMES_HOME/node"
        rm -f "$node_link_dir/node" "$node_link_dir/npm" "$node_link_dir/npx"
        return 1
    fi
    log_success "Node.js $installed_ver installed to $HERMES_HOME/node/"
    HAS_NODE=true
    return 0
}

install_node() {
    if [ "$DISTRO" = "termux" ]; then
        log_info "Installing Node.js via pkg..."
        if pkg install -y nodejs >/dev/null; then
            local installed_ver
            installed_ver=$(node --version 2>/dev/null || true)
            if [ -n "$installed_ver" ]; then
                log_success "Node.js $installed_ver installed via pkg"
                HAS_NODE=true
            else
                # pkg succeeded but the binary cannot start — the same
                # silent-success class the managed-download probe guards
                # against (#87460). Degrade instead of claiming success.
                log_error "Node.js installed via pkg failed to start:"
                node --version >&2 || true
                HAS_NODE=false
            fi
        else
            log_warn "Failed to install Node.js via pkg"
            HAS_NODE=false
        fi
        return 0
    fi

    local arch=$(uname -m)
    local node_arch
    case "$arch" in
        x86_64)        node_arch="x64"    ;;
        aarch64|arm64) node_arch="arm64"  ;;
        armv7l)        node_arch="armv7l" ;;
        *)
            log_warn "Unsupported architecture ($arch) for Node.js auto-install"
            log_info "Install manually: https://nodejs.org/en/download/"
            HAS_NODE=false
            return 0
            ;;
    esac

    local node_os
    case "$OS" in
        linux) node_os="linux"  ;;
        macos) node_os="darwin" ;;
        *)
            log_warn "Unsupported OS for Node.js auto-install"
            HAS_NODE=false
            return 0
            ;;
    esac

    # Try the target line first, then the two previous even-numbered (LTS)
    # lines. Whether a line is usable cannot be known from its index page —
    # see install_node_line — and stepping down beats adopting a Node that
    # leaves every native dependency unbuildable.
    local node_line
    for node_line in "$NODE_VERSION" "$((NODE_VERSION - 2))" "$((NODE_VERSION - 4))"; do
        if install_node_line "$node_line" "$node_os" "$node_arch"; then
            return 0
        fi
    done

    log_warn "No usable Node.js release line found for $node_os-$node_arch"
    log_info "Install manually: https://nodejs.org/en/download/"
    HAS_NODE=false
    return 0
}

check_network_prerequisites() {
    log_info "Checking internet connectivity for package install and web tools..."

    local url
    local failed=false
    local checks=("https://pypi.org/simple/" "https://duckduckgo.com/")

    if ! command -v curl >/dev/null 2>&1; then
        log_warn "curl not found; skipping connectivity probes"
        return 0
    fi

    # Run the probes in parallel — serially, two blocked probes cost
    # 2 × --max-time (16 s) before the user sees any useful error; in
    # parallel the worst case is one --max-time (8 s).
    local pids=()
    local tmpdir
    tmpdir=$(mktemp -d)
    local i=0
    for url in "${checks[@]}"; do
        (
            if curl -fsSI --max-time 8 "$url" >/dev/null 2>&1; then
                : > "$tmpdir/ok_$i"
            fi
        ) &
        pids+=($!)
        i=$((i + 1))
    done
    wait "${pids[@]}" 2>/dev/null

    i=0
    for url in "${checks[@]}"; do
        if [ ! -e "$tmpdir/ok_$i" ]; then
            failed=true
            log_warn "Could not reach $url"
        fi
        i=$((i + 1))
    done
    rm -rf "$tmpdir"

    if [ "$failed" = false ]; then
        log_success "Internet connectivity looks good"
        return 0
    fi

    if [ "$DISTRO" = "termux" ]; then
        log_warn "Termux network prerequisites may be incomplete."
        log_info "Try: pkg install -y ca-certificates curl && pkg update"
        log_info "If mirrors are stale: termux-change-repo"
        log_info "Then test: curl -I https://pypi.org/simple/ && curl -I https://duckduckgo.com/"
    else
        log_warn "Network checks failed. Hermes install may complete, but web search and dependency downloads can fail."
        log_info "Verify internet/DNS and retry if pip install fails."
    fi
}

install_system_packages() {
    # Detect what's missing
    HAS_RIPGREP=false
    HAS_FFMPEG=false
    HAS_RTK=false
    local need_ripgrep=false
    local need_ffmpeg=false

    log_info "Checking ripgrep (fast file search)..."
    if command -v rg &> /dev/null; then
        log_success "$(rg --version | head -1) found"
        HAS_RIPGREP=true
    else
        need_ripgrep=true
    fi

    log_info "Checking ffmpeg (TTS voice messages)..."
    if command -v ffmpeg &> /dev/null; then
        local ffmpeg_ver=$(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')
        log_success "ffmpeg $ffmpeg_ver found"
        HAS_FFMPEG=true
    else
        need_ffmpeg=true
    fi

    log_info "Checking rtk (reasoning toolkit, for token-kill)..."
    if [ -x "${MANAGED_TOOLS_DIR}/rtk" ]; then
        log_success "rtk found in ${MANAGED_TOOLS_DIR}"
        HAS_RTK=true
    elif command -v rtk &> /dev/null; then
        log_success "$(rtk --version 2>/dev/null || echo rtk) found"
        HAS_RTK=true
    elif [ -x "${HERMES_HOME}/bin/rtk" ]; then
        export PATH="${HERMES_HOME}/bin:$PATH"
        log_success "rtk found in ${HERMES_HOME}/bin"
        HAS_RTK=true
    else
        log_info "rtk not found; downloading..."
        if _install_rtk_direct; then
            HAS_RTK=true
        else
            log_warn "rtk not installed (token-kill will be unavailable)"
            log_info "Install manually from: https://github.com/rtk-ai/rtk/releases"
        fi
    fi

    # Termux always needs the Android build toolchain for the tested pip path,
    # even when ripgrep/ffmpeg are already present.
    if [ "$DISTRO" = "termux" ]; then
        local termux_pkgs=(clang rust make pkg-config libffi openssl ca-certificates curl)
        if [ "$need_ripgrep" = true ]; then
            termux_pkgs+=("ripgrep")
        fi
        if [ "$need_ffmpeg" = true ]; then
            termux_pkgs+=("ffmpeg")
        fi

        log_info "Installing Termux packages: ${termux_pkgs[*]}"
        if pkg install -y "${termux_pkgs[@]}" >/dev/null; then
            [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep installed"
            [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg installed"
            log_success "Termux build dependencies installed"
            return 0
        fi

        log_warn "Could not auto-install all Termux packages"
        log_info "Install manually: pkg install ${termux_pkgs[*]}"
        return 0
    fi

    # Nothing to install — done
    if [ "$need_ripgrep" = false ] && [ "$need_ffmpeg" = false ]; then
        return 0
    fi

    # Build a human-readable description + package list
    local desc_parts=()
    local pkgs=()
    if [ "$need_ripgrep" = true ]; then
        desc_parts+=("ripgrep for faster file search")
        pkgs+=("ripgrep")
    fi
    if [ "$need_ffmpeg" = true ]; then
        desc_parts+=("ffmpeg for TTS voice messages")
        pkgs+=("ffmpeg")
    fi
    local description
    description=$(IFS=" and "; echo "${desc_parts[*]}")

    # ── macOS: brew ──
    if [ "$OS" = "macos" ]; then
        # On macOS, add coreutils to the brew install list if not already present
        if command -v gcat &>/dev/null || command -v cat &>/dev/null; then
            :  # coreutils already available
        else
            log_info "Checking GNU coreutils (POSIX CLI tools)..."
            # On macOS, the system cat is BSD-based; GNU coreutils provides
            # gcat, gcp, etc. with a 'g' prefix via Homebrew.
            if command -v brew &>/dev/null; then
                pkgs+=("coreutils")
            fi
        fi
        
        if command -v brew &>/dev/null; then
            log_info "Installing ${pkgs[*]} via Homebrew..."
            if brew install "${pkgs[@]}"; then
                [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep installed"
                [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg installed"
                return 0
            fi
        fi
        log_warn "Could not auto-install (brew not found or install failed)"
        log_info "Install manually: brew install ${pkgs[*]}"
        return 0
    fi

    # ── Linux: resolve package manager command ──
    local pkg_install=""
    case "$DISTRO" in
        ubuntu|debian) pkg_install="apt install -y"   ;;
        fedora)        pkg_install="dnf install -y"   ;;
        arch)          pkg_install="pacman -S --noconfirm" ;;
    esac

    if [ -n "$pkg_install" ]; then
        local install_cmd="$pkg_install ${pkgs[*]}"

        # Prevent needrestart/whiptail dialogs from blocking non-interactive installs
        case "$DISTRO" in
            ubuntu|debian) export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a ;;
        esac

        # Already root — just install
        if [ "$(id -u)" -eq 0 ]; then
            log_info "Installing ${pkgs[*]}..."
            if $install_cmd; then
                [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep installed"
                [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg installed"
                return 0
            fi
        # Passwordless sudo — just install
        elif command -v sudo &> /dev/null && sudo -n true 2>/dev/null; then
            log_info "Installing ${pkgs[*]}..."
            if sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a $install_cmd; then
                [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep installed"
                [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg installed"
                return 0
            fi
        # sudo needs password — ask once for everything
        elif command -v sudo &> /dev/null; then
            if [ "$IS_INTERACTIVE" = true ]; then
                echo ""
                log_info "sudo is needed ONLY to install optional system packages (${pkgs[*]}) via your package manager."
                log_info "Hermes Agent itself does not require or retain root access."
                if prompt_yes_no "Install ${description}? (requires sudo)" "no"; then
                    if sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a $install_cmd; then
                        [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep installed"
                        [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg installed"
                        return 0
                    fi
                fi
            elif (: </dev/tty) 2>/dev/null; then
                # Non-interactive (e.g. curl | bash) but a terminal is available.
                # Read the prompt from /dev/tty (same approach the setup wizard uses).
                # Probe by actually opening /dev/tty: a bare existence test passes
                # in Docker builds where the device node is in the mount namespace
                # but opening fails with ENXIO. See #16746.
                echo ""
                log_info "sudo is needed ONLY to install optional system packages (${pkgs[*]}) via your package manager."
                log_info "Hermes Agent itself does not require or retain root access."
                if prompt_yes_no "Install ${description}?" "yes"; then
                    if sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a $install_cmd < /dev/tty; then
                        [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep installed"
                        [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg installed"
                        return 0
                    fi
                fi
            else
                log_warn "Non-interactive mode and no terminal available — cannot install system packages"
                log_info "Install manually after setup completes: sudo $install_cmd"
            fi
        fi
    fi

    # ── Fallback for ripgrep: cargo ──
    if [ "$need_ripgrep" = true ] && [ "$HAS_RIPGREP" = false ]; then
        if command -v cargo &> /dev/null; then
            log_info "Trying cargo install ripgrep (no sudo needed)..."
            if cargo install ripgrep; then
                log_success "ripgrep installed via cargo"
                HAS_RIPGREP=true
            fi
        fi
    fi

    # ── Fallback for ripgrep: direct GitHub download to managed tools dir ──
    if [ "$need_ripgrep" = true ] && [ "$HAS_RIPGREP" = false ]; then
        log_info "Trying direct download of ripgrep to ${MANAGED_TOOLS_DIR}..."
        if _install_ripgrep_direct; then
            HAS_RIPGREP=true
        fi
    fi

    # ── Show manual instructions for anything still missing ──
    if [ "$HAS_RIPGREP" = false ] && [ "$need_ripgrep" = true ]; then
        log_warn "ripgrep not installed (file search will use grep fallback)"
        show_manual_install_hint "ripgrep"
    fi
    if [ "$HAS_FFMPEG" = false ] && [ "$need_ffmpeg" = true ]; then
        log_warn "ffmpeg not installed (TTS voice messages will be limited)"
        show_manual_install_hint "ffmpeg"
    fi
}

# ── Hermes-managed external tools (ripgrep, rtk, ...) ──
RIPGREP_VERSION="15.2.0"
RTK_VERSION="0.44.0"

_install_ripgrep_direct() {
    local arch_suffix
    case "$(uname -m)" in
        x86_64|amd64) arch_suffix="x86_64" ;;
        aarch64|arm64) arch_suffix="aarch64" ;;
        *) log_warn "Unsupported architecture for ripgrep"; return 1 ;;
    esac

    local os_suffix
    case "$(uname -s)" in
        Darwin) os_suffix="apple-darwin" ;;
        Linux)  os_suffix="unknown-linux-gnu" ;;
        *) log_warn "Unsupported OS for ripgrep"; return 1 ;;
    esac

    local arch="${arch_suffix}-${os_suffix}"
    local tarball="ripgrep-${RIPGREP_VERSION}-${arch}.tar.gz"
    local url="https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP_VERSION}/${tarball}"
    local tmpdir
    tmpdir=$(mktemp -d) || return 1
    local dest="${MANAGED_TOOLS_DIR}"
    mkdir -p "$dest"

    log_info "Downloading ripgrep ${RIPGREP_VERSION}..."
    if curl -fsSL "$url" -o "${tmpdir}/${tarball}"; then
        tar -xzf "${tmpdir}/${tarball}" -C "$tmpdir"
        local binary="${tmpdir}/ripgrep-${RIPGREP_VERSION}-${arch}/rg"
        if [ -f "$binary" ]; then
            cp "$binary" "${dest}/rg"
            chmod +x "${dest}/rg"
            rm -rf "$tmpdir"
            if "${dest}/rg" --version >/dev/null 2>&1; then
                log_success "ripgrep ${RIPGREP_VERSION} installed to ${dest}/rg"
                return 0
            fi
        else
            log_warn "Could not find rg binary in extracted tarball"
        fi
    fi
    rm -rf "$tmpdir"
    return 1
}

_install_rtk_direct() {
    local arch_suffix
    case "$(uname -m)" in
        x86_64|amd64) arch_suffix="x86_64" ;;
        aarch64|arm64) arch_suffix="aarch64" ;;
        *) log_warn "Unsupported architecture for rtk"; return 1 ;;
    esac

    local os_suffix
    case "$(uname -s)" in
        Darwin) os_suffix="apple-darwin" ;;
        Linux)  os_suffix="unknown-linux-gnu" ;;
        *) log_warn "Unsupported OS for rtk"; return 1 ;;
    esac

    local arch="${arch_suffix}-${os_suffix}"
    local tarball="rtk-${RTK_VERSION}-${arch}.tar.gz"
    local url="https://github.com/rtk-ai/rtk/releases/download/v${RTK_VERSION}/${tarball}"
    local tmpdir
    tmpdir=$(mktemp -d) || return 1
    local dest="${MANAGED_TOOLS_DIR}"
    mkdir -p "$dest"

    log_info "Downloading rtk ${RTK_VERSION}..."
    if curl -fsSL "$url" -o "${tmpdir}/${tarball}"; then
        tar -xzf "${tmpdir}/${tarball}" -C "$tmpdir"
        if [ -f "${tmpdir}/rtk-${RTK_VERSION}-${arch}/rtk" ]; then
            cp "${tmpdir}/rtk-${RTK_VERSION}-${arch}/rtk" "${dest}/rtk"
        elif [ -f "${tmpdir}/rtk" ]; then
            cp "${tmpdir}/rtk" "${dest}/rtk"
        else
            log_warn "Could not find rtk binary in extracted tarball"
            rm -rf "$tmpdir"
            return 1
        fi
        chmod +x "${dest}/rtk"
        rm -rf "$tmpdir"
        if "${dest}/rtk" --version >/dev/null 2>&1; then
            log_success "rtk ${RTK_VERSION} installed to ${dest}/rtk"
            return 0
        fi
    fi
    rm -rf "$tmpdir"
    return 1
}

show_manual_install_hint() {
    local pkg="$1"
    log_info "To install $pkg manually:"
    case "$OS" in
        linux)
            case "$DISTRO" in
                ubuntu|debian) log_info "  sudo apt install $pkg" ;;
                fedora)        log_info "  sudo dnf install $pkg" ;;
                arch)          log_info "  sudo pacman -S $pkg"   ;;
                *)             log_info "  Use your package manager or visit the project homepage" ;;
            esac
            ;;
        android)
            log_info "  pkg install $pkg"
            ;;
        macos) log_info "  brew install $pkg" ;;
    esac
}

# ============================================================================
# Installation
# ============================================================================

clone_repo() {
    log_info "Installing to $INSTALL_DIR..."

    # An interrupted previous clone leaves a .git with no initial commit, where
    # the update path's `git stash` / `git checkout` abort with "You do not
    # have the initial commit yet" and fail the install (#40998). Move such a
    # partial checkout aside -- never delete it, in case it holds something the
    # user wants -- so the fresh-clone path below can proceed.
    if [ -d "$INSTALL_DIR/.git" ] && ! git -C "$INSTALL_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
        backup_dir="${INSTALL_DIR}.broken-$(date -u +%Y%m%d-%H%M%S)"
        log_warn "Existing checkout at $INSTALL_DIR has no commits (interrupted clone)."
        log_warn "Moving it aside to $backup_dir before re-cloning."
        mv "$INSTALL_DIR" "$backup_dir"
    fi

    if [ -d "$INSTALL_DIR" ]; then
        if [ -d "$INSTALL_DIR/.git" ]; then
            log_info "Existing installation found, updating..."
            cd "$INSTALL_DIR"

            local autostash_ref=""
            discard_update_lockfile_churn "$INSTALL_DIR"
            if [ -n "$(git status --porcelain)" ]; then
                # A previously interrupted update can leave the index with
                # unmerged entries. In that state `git stash` aborts with
                # "could not write index" and the later `git checkout` aborts
                # with "you need to resolve your current index first", failing
                # the whole install at the repository stage. Clear the conflict
                # markers with `git reset` first -- this keeps working-tree
                # changes (they're still stashed just below) and only drops the
                # index-level conflict state. Mirrors the `hermes update` path
                # (#4735).
                if [ -n "$(git ls-files --unmerged)" ]; then
                    log_info "Clearing unmerged index entries from a previous conflict..."
                    git reset -q
                fi
                local stash_name
                stash_name="hermes-install-autostash-$(date -u +%Y%m%d-%H%M%S)"
                log_info "Local changes detected, stashing before update..."
                git stash push --include-untracked -m "$stash_name"
                autostash_ref="stash@{0}"
            fi

            # Fetch only the target branch. A bare `git fetch origin` pulls
            # every ref, and this repo carries thousands of auto-generated
            # branches — on a non-single-branch checkout that turns each update
            # into a multi-minute download that can stall the installer.
            git remote set-branches origin "$BRANCH" 2>/dev/null || true
            git fetch origin "$BRANCH"
            git checkout "$BRANCH"
            # Managed installs should follow origin/$BRANCH exactly. If the
            # checkout has diverged (or has local-only commits), ff-only pull
            # cannot succeed — mirror ``hermes update`` and reset to the
            # fetched remote so bootstrap/install can recover.
            if ! git pull --ff-only origin "$BRANCH"; then
                log_warn "Fast-forward not possible; resetting managed install to origin/$BRANCH..."
                git reset --hard "origin/$BRANCH"
            fi

            if [ -n "$autostash_ref" ]; then
                local restore_now="yes"
                if [ -t 0 ] && [ -t 1 ]; then
                    echo
                    log_warn "Local changes were stashed before updating."
                    log_warn "Restoring them may reapply local customizations onto the updated codebase."
                    printf "Restore local changes now? [Y/n] "
                    read -r restore_answer
                    case "$restore_answer" in
                        ""|y|Y|yes|YES|Yes) restore_now="yes" ;;
                        *) restore_now="no" ;;
                    esac
                fi

                if [ "$restore_now" = "yes" ]; then
                    log_info "Restoring local changes..."
                    local restore_output=""
                    local restore_ok="yes"
                    if restore_output="$(git stash apply "$autostash_ref" 2>&1)"; then
                        restore_ok="yes"
                    else
                        restore_ok="no"
                    fi
                    local conflicted_files=""
                    conflicted_files="$(git diff --name-only --diff-filter=U || true)"
                    if [ "$restore_ok" = "yes" ] && [ -z "$conflicted_files" ]; then
                        git stash drop "$autostash_ref" >/dev/null
                        log_warn "Local changes were restored on top of the updated codebase."
                        log_warn "Review git diff / git status if Hermes behaves unexpectedly."
                    else
                        log_error "Update pulled new code, but restoring local changes hit conflicts."
                        if [ -n "$restore_output" ]; then
                            printf '%s\n' "$restore_output"
                        fi
                        if [ -n "$conflicted_files" ]; then
                            printf '\nConflicted files:\n'
                            while IFS= read -r file; do
                                [ -n "$file" ] && printf '  • %s\n' "$file"
                            done <<EOF
$conflicted_files
EOF
                        fi
                        printf '\n'
                        log_info "Your stashed changes are preserved — nothing is lost."
                        log_info "  Stash ref: $autostash_ref"
                        git reset --hard HEAD >/dev/null 2>&1 || true
                        log_info "Working tree reset to clean state."
                        log_info "Restore your changes later with: git stash apply $autostash_ref"
                    fi
                else
                    log_warn "could not fetch the directory history; the next hermes update retries it"
                fi
            fi
        fi
        run_logged "Fetching origin/$BRANCH" git -C "$INSTALL_DIR" fetch origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" \
            || fail "git fetch failed" git_fetch_failed
        local stamp
        stamp="$(date -u +%Y%m%d-%H%M%S)"
        # Park local work BEFORE switching branches: checkout refuses a dirty
        # tree that conflicts, and the reset below would discard it. Work
        # that cannot be parked stops the install -- never overwrite it.
        if [ -n "$(git -C "$INSTALL_DIR" status --porcelain)" ]; then
            # An interrupted update can leave unmerged index entries, where
            # stash aborts ("could not write index"). Dropping only the
            # index-level conflict state keeps the working-tree changes for
            # the stash below (#4735).
            if [ -n "$(git -C "$INSTALL_DIR" ls-files --unmerged)" ]; then
                log_warn "clearing unmerged index entries from a previous conflict"
                git -C "$INSTALL_DIR" reset -q || fail "cannot clear the unmerged index in $INSTALL_DIR" local_changes_blocked
            fi
            run_logged "Stashing local changes" \
                git -C "$INSTALL_DIR" stash push --include-untracked -m "hermes-install-autostash-$stamp" \
                || fail "could not stash local changes in $INSTALL_DIR; commit or move them aside, then rerun" local_changes_blocked
            log_warn "local changes stashed as hermes-install-autostash-$stamp"
        fi
        # checkout's branch guess only sees remote refs the refspec maps, so a
        # narrow checkout (detached at its tag, no local branch) gets the branch
        # created at the fetched tip.
        if git -C "$INSTALL_DIR" show-ref --verify --quiet "refs/heads/$BRANCH"; then
            run_logged "Checking out $BRANCH" git -C "$INSTALL_DIR" checkout "$BRANCH" || fail "git checkout failed" git_checkout_failed
        else
            run_logged "Checking out $BRANCH" git -C "$INSTALL_DIR" checkout -b "$BRANCH" "origin/$BRANCH" \
                || fail "git checkout failed" git_checkout_failed
        fi
        if ! run_logged --may-fail "Fast-forwarding to origin/$BRANCH" \
            git -C "$INSTALL_DIR" merge --ff-only "origin/$BRANCH"; then
            # A release cut off the main line, a force-pushed remote, or the
            # user's own commits cannot fast-forward. Every stage below reads
            # files only the new tree has (pm/), so an install left on the old
            # tree cannot finish -- match the remote the way `hermes update`
            # does, after parking the old tip.
            # Only commits absent from origin need a rescue ref. Keep the same
            # namespace as `hermes update` so its pruning and recovery work.
            local dropped rescue_kind rescue_ref prior
            dropped="$(git -C "$INSTALL_DIR" rev-list --count "origin/$BRANCH..HEAD")" \
                || fail "cannot count commits before reset" git_reset_failed
            if [ "$dropped" -gt 0 ]; then
                rescue_kind="diverged"
                git -C "$INSTALL_DIR" merge-base HEAD "origin/$BRANCH" >/dev/null 2>&1 \
                    || rescue_kind="orphan"
                prior="$(git -C "$INSTALL_DIR" rev-parse --short=12 HEAD)" \
                    || fail "cannot identify commits before reset" git_reset_failed
                rescue_ref="refs/hermes-update-backups/$rescue_kind-$BRANCH-$stamp-$prior"
                git -C "$INSTALL_DIR" update-ref "$rescue_ref" HEAD \
                    || fail "cannot back up $dropped local commit(s); refusing to reset" git_reset_failed
                log_warn "$dropped commit(s) not on origin/$BRANCH backed up to $rescue_ref"
                log "List them with: git -C \"$INSTALL_DIR\" log origin/$BRANCH..$rescue_ref"
            fi
            run_logged "Resetting to origin/$BRANCH" git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH" \
                || fail "git reset failed" git_reset_failed
            log_warn "not fast-forwardable; reset to origin/$BRANCH"
        fi
    else
        # `mv <clone> <existing dir>` nests the checkout INSIDE it as
        # <dir>/tree, so a pre-existing destination must be empty (we take
        # the empty dir over) or we refuse: whatever lives there is not ours.
        if [ -e "$INSTALL_DIR" ] || [ -L "$INSTALL_DIR" ]; then
            if [ -d "$INSTALL_DIR" ] && [ ! -L "$INSTALL_DIR" ] && [ -z "$(ls -A "$INSTALL_DIR")" ]; then
                rmdir "$INSTALL_DIR" || fail "cannot replace empty $INSTALL_DIR" filesystem_error
            else
                fail "$INSTALL_DIR exists and is not a Hermes git checkout. Move it aside, or install elsewhere with --dir <path>." dir_not_checkout
            fi
        fi
        mkdir -p "$(dirname "$INSTALL_DIR")"
        local staged attempt label cloned=false progress=() materialize=(reset --hard HEAD)
        # Phase lines ("Receiving objects: 42%") feed the status line; git
        # prints none to a pipe unless asked.
        if quiet_output; then progress=(--progress); fi
        staged="$(mktemp -d "$(dirname "$INSTALL_DIR")/.hermes-clone-XXXXXX")" || fail "cannot stage clone" filesystem_error
        for attempt in 1 2 3; do
            # Blobless: every commit, tree and release tag (runtime identity is
            # the nearest reachable release; --commit pins and branch switches
            # still resolve), file contents fetched on demand. Not treeless:
            # a long-lived treeless checkout re-downloads whole trees on every
            # checkout and history walk (#129712).
            label="Cloning $REPO_URL ($BRANCH) into $INSTALL_DIR"
            [ "$attempt" = 1 ] || label="$label (attempt $attempt of 3)"
            # A pinned install materializes only the pin (below): checking out the
            # branch tip first fetches and rewrites every file the two differ in.
            if run_logged "$label" git clone ${progress[@]+"${progress[@]}"} \
                --filter=blob:none ${INSTALL_COMMIT:+--no-checkout} --branch "$BRANCH" "$REPO_URL" "$staged/tree"; then
                cloned=true
                break
            fi
            rm -rf "$staged/tree"
            [ "$attempt" = 3 ] || sleep "$((attempt * 5))"
        done
        if [ "$cloned" = false ]; then
            # The checkout step is where throttled downloads die: clone the
            # graph alone, then retry materializing the tree separately.
            log_warn "direct clone failed; trying deferred checkout"
            if run_logged "Cloning history" git clone ${progress[@]+"${progress[@]}"} \
                --filter=blob:none --no-checkout --branch "$BRANCH" "$REPO_URL" "$staged/tree"; then
                cloned=deferred
            fi
        fi
        if [ "$cloned" = false ]; then
            rm -rf "$staged"
            fail "git clone failed; no checkout published" git_clone_failed
        fi
        if [ -n "$INSTALL_COMMIT" ]; then
            # Pin before publishing: a pinned or deferred clone has no files yet,
            # and an unpublished tree never leaves an empty checkout behind.
            git -C "$staged/tree" merge-base --is-ancestor "$INSTALL_COMMIT" "origin/$BRANCH" 2>/dev/null \
                || { rm -rf "$staged"; fail "commit $INSTALL_COMMIT is not on branch $BRANCH" commit_not_on_branch; }
        fi
        if [ -n "$INSTALL_COMMIT" ] || [ "$cloned" = deferred ]; then
            # The checkout step is where throttled downloads die: retry it alone.
            # A pin detaches HEAD and leaves the branch at its tip, as the published pin did.
            [ -z "$INSTALL_COMMIT" ] || materialize=(checkout --force --detach "$INSTALL_COMMIT")
            cloned=false
            for attempt in 1 2; do
                if run_logged "Checking out ${INSTALL_COMMIT:-files} (attempt $attempt of 2)" \
                    git -C "$staged/tree" "${materialize[@]}"; then
                    cloned=true
                    [ -z "$INSTALL_COMMIT" ] || pinned=true
                    break
                fi
                [ "$attempt" = 2 ] || sleep 5
            done
            if [ "$cloned" = false ]; then
                rm -rf "$staged"
                fail "could not check out ${INSTALL_COMMIT:-the cloned tree}; no checkout published" git_checkout_failed
            fi
        fi
        if ! mv "$staged/tree" "$INSTALL_DIR"; then
            rm -rf "$staged"
            fail "cannot publish cloned checkout" filesystem_error
        fi
        rmdir "$staged"
        # A treeless checkout must never write a commit-graph: over a graph with
        # changed-path data that lazy-fetches the trees of every unseen commit, in a
        # loop (#127711). gc.auto stays on: `hermes update` folds lazy-fetch packs with
        # `gc --auto`.
        git -C "$INSTALL_DIR" config maintenance.commit-graph.enabled false \
            || log_warn "could not disable maintenance.commit-graph.enabled in $INSTALL_DIR"
        git -C "$INSTALL_DIR" config gc.writeCommitGraph false \
            || log_warn "could not disable gc.writeCommitGraph in $INSTALL_DIR"
        git -C "$INSTALL_DIR" config fetch.writeCommitGraph false \
            || log_warn "could not disable fetch.writeCommitGraph in $INSTALL_DIR"
        log_success "Hermes Agent cloned"
    fi
    if [ -n "$INSTALL_COMMIT" ] && [ "$pinned" = false ]; then
        # A rerun over an existing checkout pins here (a fresh clone pinned
        # before publishing). A pin must come from the branch being installed:
        # the complete marker records both, and a commit off that branch would
        # make the next plain rerun "update" onto a different line.
        git -C "$INSTALL_DIR" merge-base --is-ancestor "$INSTALL_COMMIT" "origin/$BRANCH" 2>/dev/null \
            || fail "commit $INSTALL_COMMIT is not on branch $BRANCH" commit_not_on_branch
        run_logged "Pinning $INSTALL_COMMIT" git -C "$INSTALL_DIR" checkout "$INSTALL_COMMIT" \
            || fail "could not pin commit $INSTALL_COMMIT" git_checkout_failed

    log_success "Repository ready"
}

setup_venv() {
    if [ "$USE_VENV" = false ]; then
        log_info "Skipping virtual environment (--no-venv)"
        return 0
    fi

    if [ "$DISTRO" = "termux" ]; then
        log_info "Creating virtual environment with Termux Python..."

        if [ -d "venv" ]; then
            log_info "Virtual environment already exists, recreating..."
            rm -rf venv
        fi

        "$PYTHON_PATH" -m venv venv
        log_success "Virtual environment ready ($(./venv/bin/python --version 2>/dev/null))"
        return 0
    fi

    log_info "Creating virtual environment with Python $PYTHON_VERSION..."

    if [ -d "venv" ]; then
        log_info "Virtual environment already exists, recreating..."
        rm -rf venv
    fi

    # uv creates the venv and pins the Python version in one step. Fail loudly: `set -e` does not
    # reach this line's callers on every path, and a missing venv used to be reported as ready.
    if ! $UV_CMD venv venv --python "$PYTHON_VERSION" || [ ! -x "venv/bin/python" ]; then
        log_error "Failed to create the virtual environment with Python $PYTHON_VERSION"
        exit 1
    fi

    # Neutralize any inherited UV_PYTHON (e.g. UV_PYTHON=3.14 left in the
    # user's shell env). uv honours UV_PYTHON over an existing venv for the
    # later `uv sync` / `uv pip install` tiers, so without this it would
    # silently delete this 3.11 venv and recreate it at the inherited
    # version — building Rust transitives that have no wheel for that
    # version from source via maturin, which fails. Pinning UV_PYTHON to the
    # interpreter we just created forces every subsequent uv command onto it.
    if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        export UV_PYTHON="$INSTALL_DIR/venv/bin/python"
    fi

    log_success "Virtual environment ready ($(./venv/bin/python --version 2>/dev/null || echo "Python $PYTHON_VERSION"))"
}

run_locked_uv_sync() {
    # Bootstrap uv calls stay isolated from ambient config via UV_NO_CONFIG
    # (#21269). A locked project sync is different: uv.lock records resolver
    # settings from this checkout's [tool.uv], so hiding pyproject.toml makes
    # uv 0.12+ reject the valid lock. Re-enable project discovery only for
    # this subprocess while redirecting user/system config lookups to an empty
    # directory. Keep HOME unchanged so caches, credentials, and git continue
    # to work normally.
    local project_env="$1"
    local isolated_uv_config
    local sync_rc
    isolated_uv_config="$(mktemp -d)" || return 1

    (
        unset UV_NO_CONFIG UV_CONFIG_FILE
        export XDG_CONFIG_HOME="$isolated_uv_config"
        export XDG_CONFIG_DIRS="$isolated_uv_config"
        UV_PROJECT_ENVIRONMENT="$project_env" $UV_CMD sync --extra all --locked
    )
    sync_rc=$?
    rmdir "$isolated_uv_config" 2>/dev/null || true
    return "$sync_rc"
}

install_deps() {
    log_info "Installing dependencies..."

    # Re-pin UV_PYTHON to the venv interpreter. setup_venv already does this,
    # but the bootstrap runs install stages (`venv`, `python-deps`) as separate
    # processes, so an export from setup_venv does NOT survive into a separate
    # python-deps invocation. Re-deriving it here covers that path. Without it,
    # an inherited UV_PYTHON=3.14 makes the uv sync/pip tiers below recreate the
    # venv at 3.14 and fail the maturin source build (no cp314 wheels yet).
    if [ "$DISTRO" != "termux" ] && [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        export UV_PYTHON="$INSTALL_DIR/venv/bin/python"
    fi

    if [ "$DISTRO" = "termux" ]; then
        if [ "$USE_VENV" = true ]; then
            export VIRTUAL_ENV="$INSTALL_DIR/venv"
            PIP_PYTHON="$INSTALL_DIR/venv/bin/python"
        else
            PIP_PYTHON="$PYTHON_PATH"
        fi

        if [ -z "${ANDROID_API_LEVEL:-}" ]; then
            ANDROID_API_LEVEL="$(getprop ro.build.version.sdk 2>/dev/null || true)"
            if [ -z "$ANDROID_API_LEVEL" ]; then
                ANDROID_API_LEVEL=24
            fi
            export ANDROID_API_LEVEL
            log_info "Using ANDROID_API_LEVEL=$ANDROID_API_LEVEL for Android wheel builds"
        fi

        "$PIP_PYTHON" -m pip install --upgrade pip setuptools wheel >/dev/null

        # On Android, psutil's setup.py rejects sys.platform == 'android' before
        # it ever invokes the C build, so the next pip install would fail at
        # "platform android is not supported".  Prebuild psutil from the official
        # sdist with a one-line marker patch (Linux source path is fine on
        # Android).  Stopgap until psutil#2762 ships upstream.
        if "$PIP_PYTHON" -c 'import sys; raise SystemExit(0 if sys.platform == "android" else 1)' 2>/dev/null; then
            log_info "Android Python detected: prebuilding psutil compatibility shim..."
            if ! "$PIP_PYTHON" "$INSTALL_DIR/scripts/install_psutil_android.py" --pip "$PIP_PYTHON -m pip"; then
                log_warn "psutil Android prebuild failed — package install will likely fail next."
                log_info "Workaround: manually rerun 'python scripts/install_psutil_android.py' once your toolchain is set up."
            fi
        fi

        # Try the broad Termux profile first (best-effort "install all" for Android),
        # then fall back to the conservative Termux baseline, then base package.
        if ! "$PIP_PYTHON" -m pip install -e '.[termux-all]' -c constraints-termux.txt; then
            log_warn "Termux broad profile (.[termux-all]) failed, trying baseline Termux profile..."
            if ! "$PIP_PYTHON" -m pip install -e '.[termux]' -c constraints-termux.txt; then
                log_warn "Termux baseline profile (.[termux]) failed, trying base install..."
                if ! "$PIP_PYTHON" -m pip install -e '.' -c constraints-termux.txt; then
                    log_error "Package installation failed on Termux."
                    log_info "Ensure these packages are installed: pkg install clang rust make pkg-config libffi openssl ca-certificates curl"
                    log_info "Then re-run: cd $INSTALL_DIR && python -m pip install -e '.[termux-all]' -c constraints-termux.txt"
                    exit 1
                fi
            fi
        fi

        log_success "Main package installed"
        log_info "Termux note: matrix e2ee and local faster-whisper extras are excluded from .[termux-all] due to upstream Android wheel/toolchain blockers."
        log_info "Termux note: browser/WhatsApp tooling is not installed by default; see the Termux guide for optional follow-up steps."

        log_success "All dependencies installed"
        return 0
    fi

    if [ "$USE_VENV" = true ]; then
        # Tell uv to install into our venv (no need to activate)
        export VIRTUAL_ENV="$INSTALL_DIR/venv"
    fi

    # On Debian/Ubuntu (including WSL), some Python packages need build tools.
    # Check and offer to install them if missing.
    if [ "$DISTRO" = "ubuntu" ] || [ "$DISTRO" = "debian" ]; then
        local need_build_tools=false
        for pkg in gcc python3-dev libffi-dev; do
            if ! dpkg -s "$pkg" &>/dev/null; then
                need_build_tools=true
                break
            fi
        done
        if [ "$need_build_tools" = true ]; then
            log_info "Some build tools may be needed for Python packages..."
            if command -v sudo &> /dev/null; then
                if sudo -n true 2>/dev/null; then
                    sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y -qq build-essential python3-dev libffi-dev >/dev/null 2>&1 || true
                    log_success "Build tools installed"
                else
                    log_info "sudo is needed ONLY to install build tools (build-essential, python3-dev, libffi-dev) via apt."
                    log_info "Hermes Agent itself does not require or retain root access."
                    if prompt_yes_no "Install build tools?" "yes"; then
                        sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y -qq build-essential python3-dev libffi-dev >/dev/null 2>&1 || true
                        log_success "Build tools installed"
                    fi
                fi
            fi
        fi
    fi

    # Install the main package in editable mode with all extras.
    #
    # Hash-verified install (Tier 0) — when uv.lock is present, prefer
    # `uv sync --locked`. The lockfile records SHA256 hashes for every
    # transitive, so a compromised transitive (different hash than what
    # we shipped) is REJECTED by the resolver. This is the *only* path
    # that protects against the "direct dep is fine, but the dep's dep
    # got worm-poisoned overnight" failure mode. All `uv pip install`
    # tiers below re-resolve transitives fresh from PyPI without any
    # hash verification — they exist to keep installs working when the
    # lockfile is stale, missing, or out-of-sync with the current
    # extras spec, NOT because they're equivalent in posture.
    if [ -f "uv.lock" ]; then
        log_info "Trying tier: hash-verified (uv.lock) ..."
        log_info "(this resolves + downloads the curated [all] set — first run on a"
        log_info " fresh venv can take 1-5 minutes; uv prints progress below)"
        # Stream uv's progress directly to the user instead of swallowing
        # it with `2>"$(mktemp)"`.  Two reasons:
        #   1. `--extra all --locked` against a fresh venv has to pull
        #      every transitive — silencing stderr makes the install
        #      look frozen for minutes on slow networks. Users see
        #      "Trying tier: hash-verified ..." and assume it's hung.
        #   2. The previous `2>"$(mktemp)"` substituted the path at
        #      command-build time but never saved it, so on failure the
        #      uv error message was unreachable — the user just got the
        #      generic "lockfile may be stale" warning.
        #
        # Critical flag choice: `--extra all`, NOT `--all-extras`.
        #   --all-extras = every [project.optional-dependencies] key.
        #                  This bypasses the curated `[all]` extra
        #                  entirely and pulls e.g. [matrix] (which
        #                  needs python-olm + make on Windows) and
        #                  [rl] (git+https deps that fail offline).
        #   --extra all  = install just the `[all]` extra's contents.
        #                  This respects the curation in pyproject.toml.
        # uv's own progress UI handles TTY detection and downgrades
        # gracefully when stdout/stderr aren't terminals.
        #
        # Run the Tier-0 locked sync via run_locked_uv_sync: the global
        # UV_NO_CONFIG export at script start (the #21269 sudo -u hygiene
        # guard) also hides the project's own [tool.uv] policy —
        # exclude-newer, its package exemptions, and override-dependencies —
        # from uv. The resolver then runs under a different policy than the
        # one uv.lock was resolved under, and --locked turns that mismatch
        # fatal, so every fresh install fell through to the non-hash-verified
        # PyPI tiers. The helper re-enables project config discovery for this
        # one subprocess while keeping ambient user/system uv config hidden
        # (redirected to an empty XDG dir), preserving the #21269 guarantee.
        # Runtime code does the same before its locked syncs
        # (hermes_cli/managed_uv.py).
        if run_locked_uv_sync "$INSTALL_DIR/venv"; then
            log_success "Main package installed (hash-verified via uv.lock)"
            log_success "All dependencies installed"
            return 0
        fi
        log_warn "uv.lock sync failed (see uv output above), falling back to PyPI resolve..."
    else
        log_info "uv.lock not found — falling back to PyPI resolve (no hash verification)"
    fi

    # Multi-tier fallback. The point of the tiers is that ONE compromised
    # PyPI package (a worm-poisoned release that gets quarantined, like
    # mistralai 2.4.6 in May 2026) shouldn't be able to silently demote a
    # fresh install all the way down to "core only" — the user should keep
    # everything else they signed up for.
    #
    # Tier 1: [all] — the curated extra in pyproject.toml.
    # Tier 2: [all] minus the currently-broken extras list (_BROKEN_EXTRAS).
    #         Edit _BROKEN_EXTRAS below when something on PyPI breaks; this
    #         lets users keep the rest of [all] when one transitive is
    #         unavailable. The list of [all]'s contents is parsed from
    #         pyproject.toml at runtime — there is NO hand-mirrored copy
    #         to drift out of sync. If you want to change what [all]
    #         contains, edit pyproject.toml only.
    # Tier 3: bare `.` — last-resort so at least the core CLI launches.
    #         Skipped tiers like "PyPI-only extras (no git deps)" used to
    #         exist to dodge [rl] / [matrix] git+sdist deps; those are no
    #         longer in [all] post-2026-05-12 lazy-install migration, so
    #         a separate PyPI-only tier had no remaining content.
    local _BROKEN_EXTRAS=()  # populate when an extra becomes unresolvable

    # Parse [project.optional-dependencies].all from pyproject.toml.
    # tomllib is stdlib on Python 3.14+ which uv's bootstrap guarantees.
    # Falls back to a hand list if parse fails — defensive only.
    local _ALL_EXTRAS_CSV
    _ALL_EXTRAS_CSV="$(
        "$PYTHON_PATH" - <<'PY' 2>/dev/null
import re, sys, tomllib
try:
    with open("pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    specs = data["project"]["optional-dependencies"]["all"]
    extras = []
    for s in specs:
        m = re.search(r"hermes-agent\[([\w-]+)\]", s)
        if m:
            extras.append(m.group(1))
    print(",".join(extras))
except Exception as e:
    print("", file=sys.stderr)
    sys.exit(1)
PY
    )"
    if [ -z "$_ALL_EXTRAS_CSV" ]; then
        log_warn "Could not parse [all] from pyproject.toml; falling back to .[all] only."
        _ALL_EXTRAS_CSV=""
    fi

    # Build "[all] minus broken" spec by filtering the parsed list.
    local _SAFE_SPEC=".[all]"
    if [ -n "$_ALL_EXTRAS_CSV" ] && [ "${#_BROKEN_EXTRAS[@]}" -gt 0 ]; then
        local _SAFE_EXTRAS=()
        local _e _b _skip
        IFS=',' read -ra _ALL_EXTRAS_ARR <<< "$_ALL_EXTRAS_CSV"
        for _e in "${_ALL_EXTRAS_ARR[@]}"; do
            _skip=false
            for _b in "${_BROKEN_EXTRAS[@]}"; do
                if [ "$_e" = "$_b" ]; then _skip=true; break; fi
            done
            if [ "$_skip" = false ]; then _SAFE_EXTRAS+=("$_e"); fi
        done
        _SAFE_SPEC=".[$(IFS=,; echo "${_SAFE_EXTRAS[*]}")]"
    fi

    ALL_INSTALL_LOG=$(mktemp)
    local _installed=false
    local _tier_name=""

    install_tier() {
        local name="$1"; local spec="$2"
        log_info "Trying tier: $name ..."
        if $UV_CMD pip install -e "$spec" 2>"$ALL_INSTALL_LOG"; then
            log_success "Main package installed ($name)"
            _installed=true
            _tier_name="$name"
            return 0
        fi
        log_warn "Tier '$name' failed. Top of pip output:"
        head -5 "$ALL_INSTALL_LOG" | sed 's/^/    /' >&2
        return 1
    }

    install_tier "all" ".[all]" \
        || install_tier "all minus known-broken (${_BROKEN_EXTRAS[*]:-none})" "$_SAFE_SPEC" \
        || install_tier "core only (no extras)" "."

    rm -f "$ALL_INSTALL_LOG"

    if [ "$_installed" = false ]; then
        log_error "Package installation failed even with no extras."
        log_info "Check that build tools are installed: sudo apt install build-essential python3-dev"
        log_info "Then re-run: cd $INSTALL_DIR && uv pip install -e '.[all]'"
        exit 1
    fi

    if [ "$_tier_name" != "all" ]; then
        log_warn "Note: installed via fallback tier ($_tier_name)."
        log_info "Some optional features may be missing. After resolving any"
        log_info "PyPI/network issue, re-run: $UV_CMD pip install -e '.[all]'"
    fi

    log_success "Main package installed"

    log_success "All dependencies installed"
}

setup_path() {
    log_info "Setting up hermes command..."

    if [ "$USE_VENV" = true ]; then
        HERMES_BIN="$INSTALL_DIR/venv/bin/python"
        HERMES_ENTRYPOINT="$INSTALL_DIR/hermes"
    else
        HERMES_BIN="$(which hermes 2>/dev/null || echo "")"
        if [ -z "$HERMES_BIN" ]; then
            log_warn "hermes not found on PATH after install"
            return 0
        fi
    fi

    # Verify the interpreter and the checked-in entrypoint needed by the launcher.
    if [ ! -x "$HERMES_BIN" ] || { [ "$USE_VENV" = true ] && [ ! -f "$HERMES_ENTRYPOINT" ]; }; then
        log_warn "Hermes launcher prerequisites not found"
        log_info "This usually means the Python package install didn't complete successfully."
        if [ "$DISTRO" = "termux" ]; then
            log_info "Try: cd $INSTALL_DIR && python -m pip install -e '.[termux-all]' -c constraints-termux.txt"
        else
            log_info "Try: cd $INSTALL_DIR && uv pip install -e '.[all]'"
        fi
        return 0
    fi

    local command_link_dir
    local command_link_display_dir
    command_link_dir="$(get_command_link_dir)"
    command_link_display_dir="$(get_command_link_display_dir)"

    # Create a user-facing shim for the hermes command.
    # We intentionally clear PYTHONPATH/PYTHONHOME here so inherited env vars
    # can't make this launcher import modules from another checkout.
    HERMES_HOME_ESCAPED="$(printf '%q' "$HERMES_HOME")"
    PLAYWRIGHT_BROWSERS_PATH_ESCAPED="$(printf '%q' "$PLAYWRIGHT_BROWSERS_PATH")"
    mkdir -p "$command_link_dir"
    # Older installs created this path as a symlink to $HERMES_BIN. Without
    # the rm, `cat >` follows the symlink and overwrites the venv pip entry
    # point with this shim — making `exec "$HERMES_BIN"` self-recurse. (#21454)
    rm -f "$command_link_dir/hermes"
    if [ "$USE_VENV" = true ]; then
        # uv-generated console scripts resolve themselves through `realpath`,
        # which stock macOS does not provide. Run the checked-in entrypoint
        # with the venv interpreter instead, so the public launcher remains
        # independent of non-standard shell utilities.
        cat > "$command_link_dir/hermes" <<EOF
#!/usr/bin/env bash
unset PYTHONPATH
unset PYTHONHOME
exec "$HERMES_BIN" "$HERMES_ENTRYPOINT" "\$@"
EOF
    else
        cat > "$command_link_dir/hermes" <<EOF
#!/usr/bin/env bash
unset PYTHONPATH
unset PYTHONHOME
export HERMES_HOME=$HERMES_HOME_ESCAPED
export PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS_PATH_ESCAPED
export PATH="\$HERMES_HOME/node/bin:\$HERMES_HOME/node_modules/.bin:\$PATH"
exec "$HERMES_BIN" "\$@"
EOF
    fi
    chmod +x "$command_link_dir/hermes"
    log_success "Installed hermes launcher → $command_link_display_dir/hermes"

    # Also expose `hermes-agent`. The `hermes-agent` console script declared in
    # pyproject.toml's [project.scripts] lives inside the venv, which is not on
    # the login-shell PATH. Without this launcher users can't invoke the agent
    # entrypoint directly from outside the venv. (#74819)
    rm -f "$command_link_dir/hermes-agent"
    if [ "$USE_VENV" = true ]; then
        cat > "$command_link_dir/hermes-agent" <<EOF
#!/usr/bin/env bash
unset PYTHONPATH
unset PYTHONHOME
exec "$HERMES_BIN" "$INSTALL_DIR/run_agent.py" "\$@"
EOF
    else
        cat > "$command_link_dir/hermes-agent" <<EOF
#!/usr/bin/env bash
unset PYTHONPATH
unset PYTHONHOME
exec "$HERMES_BIN" run_agent.py "\$@"
EOF
    fi
    chmod +x "$command_link_dir/hermes-agent"
    log_success "Installed hermes-agent launcher → $command_link_display_dir/hermes-agent"

    # Also expose `hermes-acp`. ACP hosts (Zed, JetBrains, Buzz) resolve the
    # agent by command name on the login-shell PATH, and the `hermes-acp`
    # console script lives inside the venv, which is not on that PATH. Without
    # this launcher those hosts report Hermes as not installed. (#21454 applies
    # here too: clear the path first so `cat >` cannot follow an old symlink
    # into the venv and overwrite the console script.)
    rm -f "$command_link_dir/hermes-acp"
    if [ "$USE_VENV" = true ]; then
        cat > "$command_link_dir/hermes-acp" <<EOF
#!/usr/bin/env bash
unset PYTHONPATH
unset PYTHONHOME
exec "$HERMES_BIN" "$HERMES_ENTRYPOINT" acp "\$@"
EOF
    else
        cat > "$command_link_dir/hermes-acp" <<EOF
#!/usr/bin/env bash
unset PYTHONPATH
unset PYTHONHOME
exec "$HERMES_BIN" acp "\$@"
EOF
    fi
    chmod +x "$command_link_dir/hermes-acp"
    log_success "Installed hermes-acp launcher → $command_link_display_dir/hermes-acp"

    if [ "$DISTRO" = "termux" ]; then
        export PATH="$command_link_dir:$PATH"
        log_info "$command_link_display_dir is the native Termux command path"
        log_success "hermes command ready"
        return 0
    fi

    # FHS layout: /usr/local/bin is normally on PATH for login shells (via
    # /etc/profile pathmunge), but on RHEL/CentOS/Rocky/Alma 8+ non-login
    # interactive root shells (su, sudo -s, tmux panes, some web terminals)
    # only source /etc/bashrc, which does NOT add /usr/local/bin — and
    # /root/.bash_profile doesn't either.  So verify with `command -v` and
    # fall back to writing a PATH guard into /root/.bashrc when needed.
    if [ "$ROOT_FHS_LAYOUT" = true ]; then
        export PATH="$command_link_dir:$PATH"
        # Probe a fresh non-login interactive bash the way the user will use it.
        # `bash -i -c` sources ~/.bashrc but NOT ~/.bash_profile or /etc/profile,
        # which is the exact scenario where RHEL root loses /usr/local/bin.
        if env -i HOME="$HOME" TERM="${TERM:-dumb}" bash -i -c 'command -v hermes' \
                >/dev/null 2>&1; then
            log_info "/usr/local/bin is already on PATH for all shells"
            log_success "hermes command ready"
            return 0
        fi

        log_info "hermes not on PATH in non-login shells (common on RHEL-family)"
        PATH_LINE='export PATH="/usr/local/bin:$PATH"'
        PATH_COMMENT='# Hermes Agent — ensure /usr/local/bin is on PATH (RHEL non-login shells)'
        for SHELL_CONFIG in "$HOME/.bashrc" "$HOME/.bash_profile"; do
            [ -f "$SHELL_CONFIG" ] || continue
            if ! grep -v '^[[:space:]]*#' "$SHELL_CONFIG" 2>/dev/null \
                    | grep -qE 'PATH=.*(/usr/local/bin|\$command_link_dir)'; then
                echo "" >> "$SHELL_CONFIG"
                echo "$PATH_COMMENT" >> "$SHELL_CONFIG"
                echo "$PATH_LINE" >> "$SHELL_CONFIG"
                log_success "Added /usr/local/bin to PATH in $SHELL_CONFIG"
            fi
        done
        log_success "hermes command ready"
        return 0
    fi

    # Check if ~/.local/bin is on PATH; if not, add it to shell config.
    # Detect the user's actual login shell (not the shell running this script,
    # which is always bash when piped from curl).
    if ! echo "$PATH" | tr ':' '\n' | grep -q "^$command_link_dir$"; then
        SHELL_CONFIGS=()
        IS_FISH=false
        LOGIN_SHELL="$(basename "${SHELL:-/bin/bash}")"
        case "$LOGIN_SHELL" in
            zsh)
                [ -f "$HOME/.zshrc" ] && SHELL_CONFIGS+=("$HOME/.zshrc")
                [ -f "$HOME/.zprofile" ] && SHELL_CONFIGS+=("$HOME/.zprofile")
                # If neither exists, create ~/.zshrc (common on fresh macOS installs)
                if [ ${#SHELL_CONFIGS[@]} -eq 0 ]; then
                    touch "$HOME/.zshrc"
                    SHELL_CONFIGS+=("$HOME/.zshrc")
                fi
                ;;
            bash)
                [ -f "$HOME/.bashrc" ] && SHELL_CONFIGS+=("$HOME/.bashrc")
                [ -f "$HOME/.bash_profile" ] && SHELL_CONFIGS+=("$HOME/.bash_profile")
                ;;
            fish)
                # fish uses ~/.config/fish/config.fish and fish_add_path — not export PATH=
                IS_FISH=true
                FISH_CONFIG="$HOME/.config/fish/config.fish"
                mkdir -p "$(dirname "$FISH_CONFIG")"
                touch "$FISH_CONFIG"
                ;;
            *)
                [ -f "$HOME/.bashrc" ] && SHELL_CONFIGS+=("$HOME/.bashrc")
                [ -f "$HOME/.zshrc" ] && SHELL_CONFIGS+=("$HOME/.zshrc")
                ;;
        esac
        # Also ensure ~/.profile has it (sourced by login shells on
        # Ubuntu/Debian/WSL even when ~/.bashrc is skipped)
        [ "$IS_FISH" = "false" ] && [ -f "$HOME/.profile" ] && SHELL_CONFIGS+=("$HOME/.profile")

        PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

        for SHELL_CONFIG in "${SHELL_CONFIGS[@]}"; do
            if ! grep -v '^[[:space:]]*#' "$SHELL_CONFIG" 2>/dev/null | grep -qE 'PATH=.*\.local/bin'; then
                echo "" >> "$SHELL_CONFIG"
                echo "# Hermes Agent — ensure ~/.local/bin is on PATH" >> "$SHELL_CONFIG"
                echo "$PATH_LINE" >> "$SHELL_CONFIG"
                log_success "Added ~/.local/bin to PATH in $SHELL_CONFIG"
            fi
        done

        # fish uses fish_add_path instead of export PATH=...
        if [ "$IS_FISH" = "true" ]; then
            if ! grep -q 'fish_add_path.*\.local/bin' "$FISH_CONFIG" 2>/dev/null; then
                echo "" >> "$FISH_CONFIG"
                echo "# Hermes Agent — ensure ~/.local/bin is on PATH" >> "$FISH_CONFIG"
                echo 'fish_add_path "$HOME/.local/bin"' >> "$FISH_CONFIG"
                log_success "Added ~/.local/bin to PATH in $FISH_CONFIG"
            fi
        fi

        if [ "$IS_FISH" = "false" ] && [ ${#SHELL_CONFIGS[@]} -eq 0 ]; then
            log_warn "Could not detect shell config file to add ~/.local/bin to PATH"
            log_info "Add manually: $PATH_LINE"
        fi
    else
        log_info "~/.local/bin already on PATH"
    fi

    # Export for current session so hermes works immediately
    export PATH="$command_link_dir:$PATH"

    log_success "hermes command ready"
}

copy_config_templates() {
    log_info "Setting up configuration files..."

    # Create Hermes data directory structure (config at top level, code elsewhere).
    mkdir -p "$HERMES_HOME"/{cron,sessions,logs,pairing,hooks,memories,skills,skins,plans,workspace}
    mkdir -p "$HERMES_HOME"/cache/{ms-playwright,uv,pip,npm,images,audio,documents}
    # Legacy cache names remain for existing integrations that still probe them.
    mkdir -p "$HERMES_HOME"/{image_cache,audio_cache,document_cache}
    if [ "$ISOLATED_LAYOUT" = true ]; then
        mkdir -p "$HERMES_HOME/home"
    fi

    # Create .env at HERMES_HOME/.env (top level, easy to find)
    if [ ! -f "$HERMES_HOME/.env" ]; then
        if [ -f "$INSTALL_DIR/.env.example" ]; then
            cp "$INSTALL_DIR/.env.example" "$HERMES_HOME/.env"
            log_success "Created $HERMES_HOME/.env from template"
        else
            touch "$HERMES_HOME/.env"
            log_success "Created $HERMES_HOME/.env"
        fi
    else
        log_info "$HERMES_HOME/.env already exists, keeping it"
    fi
    # Restrict .env permissions — this file holds API keys and tokens.
    # 0600 ensures only the file owner can read/write, matching standard
    # practice for credential files (.netrc, .aws/credentials, .ssh/config).
    chmod 600 "$HERMES_HOME/.env"
    configure_browser_env_from_system_browser

    # Create config.yaml at HERMES_HOME/config.yaml (top level, easy to find)
    if [ ! -f "$HERMES_HOME/config.yaml" ]; then
        if [ -f "$INSTALL_DIR/cli-config.yaml.example" ]; then
            cp "$INSTALL_DIR/cli-config.yaml.example" "$HERMES_HOME/config.yaml"
            log_success "Created $HERMES_HOME/config.yaml from template"
        fi
    else
        log_info "$HERMES_HOME/config.yaml already exists, keeping it"
    fi

    # Create SOUL.md if it doesn't exist (global persona file).
    # This MUST match DEFAULT_SOUL_MD in hermes_cli/default_soul.py — the
    # runtime (_ensure_default_soul_md) treats the old comment-only scaffold as
    # "never customized" and upgrades it to this text on next run, so any drift
    # here is self-healing, but keep them in sync to avoid a churn on first run.
    if [ ! -f "$HERMES_HOME/SOUL.md" ]; then
        cat > "$HERMES_HOME/SOUL.md" << 'SOUL_EOF'
You are Hermes Agent, built by Nous Research. Be direct: match the length of your reply to the weight of the ask — a one-line question gets a one-line answer, and finished work gets a short report of what changed, what's verified, and what's left, never a replay of the process. No filler ("Great question," "I'd be happy to"), no restating the request back, no re-summarizing what you already said, no narrating tool calls the user can see. Plain claims over adjectives; when unsure, say so plainly. Agree because it's right, not because the user said it. Depth is earned — give it when the user asks for detail, teaches, or the stakes demand it, not by default.
SOUL_EOF
        log_success "Created $HERMES_HOME/SOUL.md (edit to customize personality)"
    fi

    log_success "Configuration directory ready: $HERMES_HOME/"

    # Seed bundled skills into $HERMES_HOME/skills/ (manifest-based, one-time per skill)
    if [ "$NO_SKILLS" = true ]; then
        # Blank-slate install: write the opt-out marker and skip seeding.
        # skills_sync.py and `hermes update` both honor this marker, so the
        # default profile stays empty across future updates too.
        printf '%s\n' \
            "This profile opted out of bundled-skill seeding (installed with --no-skills)." \
            "Delete this file to re-enable sync on the next 'hermes update'." \
            > "$HERMES_HOME/.no-bundled-skills" 2>/dev/null || true
        log_info "Skipping bundled skills (--no-skills). Wrote $HERMES_HOME/.no-bundled-skills"
        log_info "  Future 'hermes update' runs will not inject bundled skills. Delete the marker to opt back in."
    else
        log_info "Syncing bundled skills to $HERMES_HOME/skills/ ..."
        if "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/tools/skills_sync.py" 2>/dev/null; then
            log_success "Skills synced to $HERMES_HOME/skills/"
        else
            # Fallback: simple directory copy if Python sync fails
            if [ -d "$INSTALL_DIR/skills" ] && [ ! "$(ls -A "$HERMES_HOME/skills/" 2>/dev/null | grep -v '.bundled_manifest')" ]; then
                cp -r "$INSTALL_DIR/skills/"* "$HERMES_HOME/skills/" 2>/dev/null || true
                log_success "Skills copied to $HERMES_HOME/skills/"
            fi
        fi
    fi
}

stage_venv() {
    # Keep the installer stage protocol; PM alone creates dependency environments.
    local boot_py
    bootstrap_python
    log_success "bootstrap Python ready; PM prepares the dependency environment"
}

# Tool-only bootstrap: acquire uv and Python before PM's own dependencies exist.
# The application dependency graph is never installed in this interpreter.
bootstrap_python() {
    ensure_uv
    local _py
    # Read packages.python.version by following object names and braces, not
    # indentation — same pre-Python reader contract as setup-hermes.sh's pin().
    _py="$(awk -F '"' '
        /^[[:space:]]*("[^"]+"[[:space:]]*:[[:space:]]*)?\{/ { path[++depth] = $2; next }
        /^[[:space:]]*\}[[:space:]]*,?[[:space:]]*$/ { delete path[depth--]; next }
        path[2] == "packages" && path[3] == "python" && $2 == "version" && depth == 3 { print $4; exit }
    ' "$INSTALL_DIR/pm/lock.json" | cut -d+ -f1 | cut -d. -f1,2)"
    [ -n "$_py" ] || _py="3.14"
    # Only base interpreters qualify: an activated app venv must not become
    # PM's bootstrap parent. Prefer the existing managed Python, then a host
    # Python of the same supported minor before attempting a download (#10778).
    # This interpreter only boots PM; PM still owns the exact runtime pin.
    if ! boot_py="$(UV_SYSTEM_PYTHON=1 UV_NO_PROJECT=1 "$UV_CMD" python find --managed-python "$_py" 2>/dev/null)" \
        && ! boot_py="$("$UV_CMD" python find --system --no-project "$_py" 2>/dev/null)"; then
        run_logged "Downloading Python $_py" "$UV_CMD" python install --no-bin --no-registry "$_py" \
            || fail "bootstrap Python installation failed" python_install_failed
        boot_py="$(UV_SYSTEM_PYTHON=1 UV_NO_PROJECT=1 "$UV_CMD" python find --managed-python "$_py")" || fail "bootstrap Python lookup failed" python_install_failed
    fi
    boot_py="${boot_py%$'\r'}"
    [ -x "$boot_py" ] && "$boot_py" --version >/dev/null 2>&1 || fail "bootstrap Python is not executable: $boot_py" python_install_failed
}

# uv exits before PM can replace its tool entry. pm.cli then prepares and
# enters its independently locked runtime before mutating application deps.
bootstrap_pm() {
    local boot_py
    local pm_args=(install)
    # PM records the opt-outs, so later installs and `hermes update` keep the
    # tools off until `hermes pm install <name>` opts back in.
    [ "$SKIP_BROWSER" = true ] && pm_args+=(--without agent-browser)
    [ "$SKIP_COMPUTER_USE" = true ] && pm_args+=(--without cua-driver)
    bootstrap_python
    # Refresh the sudo ticket cached in prerequisites (never prompts): the
    # clone and downloads can outlast sudo's timestamp_timeout.
    if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then sudo -n -v 2>/dev/null || :; fi
    (cd "$INSTALL_DIR" && run_logged "Installing dependencies (hash-verified via uv.lock)" \
        "$boot_py" -m pm.cli "${pm_args[@]}") \
        || fail "pm install failed" deps_install_failed
    log_success "dependencies installed"
}

stage_python_deps() {
    bootstrap_pm
}

desktop_product_present() {
    # Does this checkout already carry a built desktop app? A plain repair or
    # upgrade rerun on a desktop install must REBUILD it rather than leave a
    # bundle built from the previous code: the app is part of that install, and
    # the artifacts live inside the tree (gitignored), so an update makes them
    # stale instead of removing them.
    # electron-builder suffixes the output dir with the arch on every non-x64
    # target (linux-arm64-unpacked, mac-arm64, win-arm64-unpacked), so the x64
    # names alone miss a desktop build on ARM64 Linux/Windows (#94703).
    local release="$INSTALL_DIR/apps/desktop/release" dir
    for dir in linux-unpacked linux-arm64-unpacked mac mac-arm64 \
               win-unpacked win-ia32-unpacked win-arm64-unpacked; do
        [ -d "$release/$dir" ] && return 0
    done
    return 1
}

# Guarded: a login shell whose ~/.bash_profile sources ~/.bashrc (Fedora, RHEL)
# reads both files, so an unconditional prepend would put ~/.local/bin on PATH twice.
SHELL_PATH_LINE='case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac'
SHELL_PATH_SETUP_RE='^[[:space:]]*([^#[:space:]].*)?PATH=.*\.local/bin'

append_shell_path() {
    local rc="$1" line="$2" pattern="$3"
    # Existing user PATH setup wins; never append another line on a repair run.
    if [ -f "$rc" ] && grep -E "$pattern" "$rc" >/dev/null 2>&1; then
        return 0
    fi
    mkdir -p "$(dirname "$rc")"
    printf '\n# Hermes Agent command\n%s\n' "$line" >> "$rc" || fail "cannot update PATH in $rc" filesystem_error
    log_success "added ~/.local/bin to PATH in $rc"
}

wire_shell_path() {
    # The launcher is published by source_completion; shell rc files belong to
    # the installer, not to PM (updates must not modify a user's shell setup).
    # Never use the installer's inherited PATH as a proxy for a *new* shell.
    local login_shell="${SHELL:-/bin/bash}"
    case "${login_shell##*/}" in
        zsh)
            append_shell_path "$HOME/.zshrc" "$SHELL_PATH_LINE" "$SHELL_PATH_SETUP_RE"
            append_shell_path "$HOME/.zprofile" "$SHELL_PATH_LINE" "$SHELL_PATH_SETUP_RE"
            ;;
        fish)
            append_shell_path "$HOME/.config/fish/config.fish" 'fish_add_path "$HOME/.local/bin"' '^[[:space:]]*fish_add_path.*\.local/bin'
    esac
    if command -v cua-driver >/dev/null 2>&1; then
        if cua_driver_runtime_compatible; then
            log_success "Computer Use driver (cua-driver) already installed and compatible"
            return 0
        fi
        log_warn "Existing cua-driver is old or incomplete; repairing it"
    fi
    # Non-admin macOS accounts can't receive the CuaDriver.app bundle in
    # /Applications; skip cleanly instead of failing loudly (#47865 class).
    if [ "$(uname -s)" = "Darwin" ] && [ -d /Applications ] && [ ! -w /Applications ]; then
        log_info "Skipping Computer Use driver (cua-driver): /Applications is not writable"
        return 0
    fi

    log_info "Installing Computer Use driver (cua-driver)..."
    # Same upstream installer `hermes computer-use install` runs; time-boxed
    # so a stalled GitHub download can't hang the Hermes install. The
    # upstream installer serializes with its own lock (600s stale window),
    # so give it a ceiling above that — matching Hermes'
    # _CUA_INSTALLER_TIMEOUT (660s).
    local cua_log
    cua_log="$(mktemp)"
    if run_with_timeout 660 /bin/bash -c \
        'curl -fsSL https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.sh | /bin/bash' \
        >"$cua_log" 2>&1; then
        log_success "Computer Use driver installed (enable via 'hermes tools' → Computer Use)"
    else
        log_warn "Computer Use driver install failed — it will install on demand when you enable the tool."
        log_info "Install later with: hermes computer-use install"
        tail -n 5 "$cua_log" >&2 || true
    fi
    rm -f "$cua_log"
}

run_setup_wizard() {
    if [ "$RUN_SETUP" = false ]; then
        log_info "Skipping setup wizard (--skip-setup)"
        return 0
    fi

    # The setup wizard reads from /dev/tty, so it works even when the
    # install script itself is piped (curl | bash). Only skip if no
    # terminal is available at all (e.g. Docker build, CI).
    #
    # Probe by actually opening /dev/tty: a bare existence test passes
    # in Docker builds where the device node is in the mount namespace
    # but opening fails with ENXIO, so the wizard would proceed and
    # then crash on `< /dev/tty` below.
    if ! (: </dev/tty) 2>/dev/null; then
        log_info "Setup wizard skipped (no terminal available). Run 'hermes setup' after install."
        return 0
    fi

    echo ""
    log_info "Starting setup wizard..."
    echo ""

    cd "$INSTALL_DIR"

    # Run hermes setup using the venv Python directly (no activation needed).
    # Redirect stdin from /dev/tty so interactive prompts work when piped from curl.
    if [ "$USE_VENV" = true ]; then
        "$INSTALL_DIR/venv/bin/python" -m hermes_cli.main setup < /dev/tty
    else
        python -m hermes_cli.main setup < /dev/tty
    fi
}

maybe_start_gateway() {
    # Check if any messaging platform tokens were configured
    ENV_FILE="$HERMES_HOME/.env"
    if [ ! -f "$ENV_FILE" ]; then
        return 0
    fi

    HAS_MESSAGING=false
    for VAR in TELEGRAM_BOT_TOKEN DISCORD_BOT_TOKEN SLACK_BOT_TOKEN SLACK_APP_TOKEN WHATSAPP_ENABLED; do
        VAL=$(grep "^${VAR}=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2-)
        if [ -n "$VAL" ] && [ "$VAL" != "your-token-here" ]; then
            HAS_MESSAGING=true
            break
        fi
    done

    if [ "$HAS_MESSAGING" = false ]; then
        return 0
    fi

    echo ""
    log_info "Messaging platform token detected!"
    log_info "The gateway needs to be running for Hermes to send/receive messages."

    # If WhatsApp is enabled and no session exists yet, run foreground first for QR scan
    WHATSAPP_VAL=$(grep "^WHATSAPP_ENABLED=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2-)
    WHATSAPP_SESSION="$HERMES_HOME/whatsapp/session/creds.json"
    if [ "$WHATSAPP_VAL" = "true" ] && [ ! -f "$WHATSAPP_SESSION" ]; then
        if [ "$IS_INTERACTIVE" = true ]; then
            echo ""
            log_info "WhatsApp is enabled but not yet paired."
            log_info "Running 'hermes whatsapp' to pair via QR code..."
            echo ""
            if prompt_yes_no "Pair WhatsApp now?" "yes"; then
                HERMES_CMD="$(get_hermes_command_path)"
                $HERMES_CMD whatsapp || true
            fi
        else
            log_info "WhatsApp pairing skipped (non-interactive). Run 'hermes whatsapp' to pair."
        fi
    fi

    # Probe by actually opening /dev/tty: a bare existence test passes
    # in Docker builds where the device node is in the mount namespace
    # but opening fails with ENXIO. See #16746.
    if ! (: </dev/tty) 2>/dev/null; then
        log_info "Gateway setup skipped (no terminal available). Run 'hermes gateway install' later."
        return 0
    fi

    echo ""
    local should_install_gateway=false
    if [ "$DISTRO" = "termux" ]; then
        if prompt_yes_no "Would you like to start the gateway in the background?" "yes"; then
            should_install_gateway=true
        fi
    else
        if prompt_yes_no "Would you like to install the gateway as a background service?" "yes"; then
            should_install_gateway=true
        fi
    fi

    if [ "$should_install_gateway" = true ]; then
        HERMES_CMD="$(get_hermes_command_path)"

        if [ "$DISTRO" != "termux" ] && command -v systemctl &> /dev/null; then
            log_info "Installing systemd service..."
            if $HERMES_CMD gateway install 2>/dev/null; then
                log_success "Gateway service installed"
                if $HERMES_CMD gateway start 2>/dev/null; then
                    log_success "Gateway started! Your bot is now online."
                else
                    log_warn "Service installed but failed to start. Try: hermes gateway start"
                fi
            else
                log_warn "Systemd install failed. You can start manually: hermes gateway"
            fi
        else
            if [ "$DISTRO" = "termux" ]; then
                log_info "Termux detected — starting gateway in best-effort background mode..."
            else
                log_info "systemd not available — starting gateway in background..."
            fi
            nohup $HERMES_CMD gateway > "$HERMES_HOME/logs/gateway.log" 2>&1 &
            GATEWAY_PID=$!
            log_success "Gateway started (PID $GATEWAY_PID). Logs: $HERMES_HOME/logs/gateway.log"
            log_info "To stop: kill $GATEWAY_PID"
            log_info "To restart later: hermes gateway"
            if [ "$DISTRO" = "termux" ]; then
                log_warn "Android may stop background processes when Termux is suspended or the system reclaims resources."
            fi
        fi
    else
        log_info "Skipped. Start the gateway later with: hermes gateway"
    fi
}

write_bootstrap_marker() {
    # Writes $INSTALL_DIR/.hermes-bootstrap-complete, which tells the Hermes
    # desktop app (apps/desktop/electron/main.ts) and the macOS launcher fast
    # path (apps/bootstrap-installer) "a real install finished here -- don't
    # re-run first-run bootstrap."
    #
    # Schema mirrors install.ps1's Write-BootstrapMarker and main.ts's
    # writeBootstrapMarker(). Keep the three in lockstep:
    #   schemaVersion 1 + pinnedCommit (length >= 7) are what the desktop
    #   validator requires; desktopVersion is omitted because only the desktop
    #   app knows its own version.
    if [ ! -d "$INSTALL_DIR" ]; then
        log_warn "Skipping bootstrap marker: $INSTALL_DIR doesn't exist"
        return 0
    fi

    # Explicit --commit wins; otherwise read HEAD from the checkout we just
    # installed. If neither resolves, skip the marker entirely rather than
    # write one the desktop will reject -- an absent marker is a clean
    # "bootstrap needed", a malformed one is a confusing half-state.
    local pinned_commit="$INSTALL_COMMIT"
    if [ -z "$pinned_commit" ]; then
        pinned_commit=$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null) || pinned_commit=""
    fi

    if [ -z "$pinned_commit" ]; then
        log_warn "Skipping bootstrap marker: could not resolve HEAD in $INSTALL_DIR"
        return 0
    fi

    local marker_path="$INSTALL_DIR/.hermes-bootstrap-complete"
    local tmp_path="$marker_path.tmp"

    # Atomic publish: the macOS launcher predicate only checks existence, so a
    # torn write would arm the fast path against a half-written marker.
    printf '{\n  "schemaVersion": 1,\n  "pinnedCommit": "%s",\n  "pinnedBranch": "%s",\n  "completedAt": "%s"\n}\n' \
        "$pinned_commit" \
        "$BRANCH" \
        "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)" > "$tmp_path"
    mv -f "$tmp_path" "$marker_path"
}

print_success() {
    echo ""
    echo -e "${GREEN}${BOLD}"
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│              ✓ Installation Complete!                   │"
    echo "└─────────────────────────────────────────────────────────┘"
    echo -e "${NC}"
    echo ""

    # Show file locations
    echo -e "${CYAN}${BOLD}📁 Your files:${NC}"
    echo ""
    echo -e "   ${YELLOW}Config:${NC}    $HERMES_HOME/config.yaml"
    echo -e "   ${YELLOW}API Keys:${NC}  $HERMES_HOME/.env"
    echo -e "   ${YELLOW}Data:${NC}      $HERMES_HOME/cron/, sessions/, logs/"
    echo -e "   ${YELLOW}Code:${NC}      $INSTALL_DIR"
    echo ""

    echo -e "${CYAN}─────────────────────────────────────────────────────────${NC}"
    echo ""
    echo -e "${CYAN}${BOLD}🚀 Commands:${NC}"
    echo ""
    echo -e "   ${GREEN}hermes${NC}              Start chatting"
    echo -e "   ${GREEN}hermes setup${NC}        Configure API keys & settings"
    echo -e "   ${GREEN}hermes config${NC}       View/edit configuration"
    echo -e "   ${GREEN}hermes config edit${NC}  Open config in editor"
    echo -e "   ${GREEN}hermes gateway install${NC} Install gateway service (messaging + cron)"
    echo -e "   ${GREEN}hermes update${NC}       Update to latest version"
    echo ""

    echo -e "${CYAN}─────────────────────────────────────────────────────────${NC}"
    echo ""
    if [ "$DISTRO" = "termux" ]; then
        echo -e "${YELLOW}⚡ 'hermes' was linked into $(get_command_link_display_dir), which is already on PATH in Termux.${NC}"
        echo ""
    elif [ "$ROOT_FHS_LAYOUT" = true ]; then
        echo -e "${YELLOW}⚡ 'hermes' was linked into /usr/local/bin and is ready to use — no shell reload needed.${NC}"
        echo ""
    else
        echo -e "${YELLOW}⚡ Reload your shell to use 'hermes' command:${NC}"
        echo ""
        LOGIN_SHELL="$(basename "${SHELL:-/bin/bash}")"
        if [ "$LOGIN_SHELL" = "zsh" ]; then
            echo "   source ~/.zshrc"
        elif [ "$LOGIN_SHELL" = "bash" ]; then
            echo "   source ~/.bashrc"
        elif [ "$LOGIN_SHELL" = "fish" ]; then
            echo "   source ~/.config/fish/config.fish"
        else
            echo "   source ~/.bashrc   # or ~/.zshrc"
        fi
        echo ""
    fi

    # Show Node.js warning if auto-install failed
    if [ "$HAS_NODE" = false ]; then
        echo -e "${YELLOW}"
        echo "Note: Node.js could not be installed automatically."
        echo "Browser tools need Node.js. Install manually:"
        if [ "$DISTRO" = "termux" ]; then
            echo "  pkg install nodejs"
        else
            echo "  https://nodejs.org/en/download/"
        fi
        echo -e "${NC}"
    fi

    # Show ripgrep note if not installed
    if [ "$HAS_RIPGREP" = false ]; then
        echo -e "${YELLOW}"
        echo "Note: ripgrep (rg) was not found. File search will use"
        echo "grep as a fallback. For faster search in large codebases,"
        if [ "$DISTRO" = "termux" ]; then
            echo "install ripgrep: pkg install ripgrep"
        else
            echo "install ripgrep: sudo apt install ripgrep (or brew install ripgrep)"
        fi
        echo -e "${NC}"
    fi
}

ensure_browser() {
    if ! command -v node >/dev/null 2>&1; then
        local node_bin="$HERMES_HOME/node/bin/node"
        if [ -x "$node_bin" ]; then
            export PATH="$HERMES_HOME/node/bin:$PATH"
        else
            log_error "Node.js not found. Run with --ensure node first."
            return 1
        fi
    fi

    local npm_bin
    npm_bin="$(command -v npm 2>/dev/null || echo "$HERMES_HOME/node/bin/npm")"
    if [ ! -x "$npm_bin" ]; then
        log_error "npm not found"
        return 1
    fi

    # agent-browser itself is intentionally NOT installed here (#43564 /
    # PR #44772 review): it resolves lazily via `npx agent-browser` instead,
    # which every consumer (tools/browser_tool.py, `hermes update`'s npx
    # cache warm) already goes through. Eagerly npm-installing a second,
    # separately version-pinned copy here -- only reachable via this
    # explicit --ensure browser fallback in the first place -- was redundant
    # complexity and an extra credential/supply-chain surface for a path
    # npx already covers.
    log_info "Installing camofox browser server..."
    local log_file
    log_file="$(mktemp)"
    # Time-boxed (#39219): a stalled npm registry fetch here would otherwise
    # hang the installer with no progress, same class as the desktop build.
    if ! run_with_timeout "$NODE_DEPS_TIMEOUT" "$npm_bin" install -g --prefix "$HERMES_HOME/node" --silent --ignore-scripts \
        "@askjo/camofox-browser@^1.5.2" \
        >"$log_file" 2>&1; then
        log_error "npm install failed or timed out:"
        cat "$log_file" >&2
        rm -f "$log_file"
        return 1
    fi
    rm -f "$log_file"
    export PATH="$HERMES_HOME/node/bin:$PATH"

    strip_snap_browser_override
    local sys_browser
    sys_browser="$(find_system_browser 2>/dev/null || true)"
    if [ -n "$sys_browser" ]; then
        configure_browser_env_from_system_browser "$sys_browser"
        log_info "Explicit browser override set -- Chromium download will be skipped when agent-browser installs on demand"
    fi

    return 0
}

ensure_mode() {
    detect_os

    IFS=',' read -ra DEPS <<< "$ENSURE_DEPS"
    for dep in "${DEPS[@]}"; do
        dep="$(echo "$dep" | tr -d '[:space:]')"
        case "$dep" in
            node)
                check_node
                ;;
            browser)
                check_node
                if [ "$HAS_NODE" = true ]; then
                    ensure_browser
                fi
                ;;
            ripgrep)
                if [ ! -x "${MANAGED_TOOLS_DIR}/rg" ] && ! command -v rg &>/dev/null && [ ! -x "${HERMES_HOME}/bin/rg" ]; then
                    _install_ripgrep_direct || log_warn "ripgrep could not be installed"
                fi
                ;;
            ffmpeg)
                if ! command -v ffmpeg &>/dev/null; then
                    HAS_FFMPEG=false
                    HAS_RIPGREP=true
                    install_system_packages
                fi
                ;;
            rtk)
                if [ ! -x "${MANAGED_TOOLS_DIR}/rtk" ] && [ ! -x "${HERMES_HOME}/bin/rtk" ] && ! command -v rtk &> /dev/null; then
                    _install_rtk_direct || log_warn "rtk could not be installed"
                fi
                ;;
            coreutils)
                # On Linux/macOS, coreutils is provided by the system;
                # on macOS we can also install GNU coreutils via brew.
                log_info "Coreutils are provided by the system on Linux/macOS."
                ;;
            *)
                log_warn "Unknown dependency: $dep"
                ;;
        esac
    done
}


# Clear the cached Electron download + any half-written unpacked output so the
# next `npm run pack` re-downloads and re-stages from scratch. A corrupt zip in
# the per-user Electron download cache - most often a partial/resumed download
# that leaves concatenated junk - makes electron-builder's `unpack-electron`
# extract a tree MISSING the electron binary, so the `electron`->`Hermes` rename
# dies with ENOENT and every re-run repeats the broken extraction forever. This
# is the bash sibling of install.ps1's Clear-ElectronBuildCache and the Python
# _purge_electron_build_cache() used by `hermes desktop`; install.sh was the only
# build path lacking it. Echoes the removed paths (one per line); best-effort.
clear_electron_build_cache() {
    local desktop_dir="$1"
    local removed=""

    # Per-user Electron download cache dirs, honoring the overrides @electron/get
    # respects, then the platform defaults (macOS: ~/Library/Caches/electron,
    # Linux: $XDG_CACHE_HOME/electron or ~/.cache/electron).
    local cache_dirs=()
    [ -n "${electron_config_cache:-}" ] && cache_dirs+=("$electron_config_cache")
    [ -n "${ELECTRON_CACHE:-}" ] && cache_dirs+=("$ELECTRON_CACHE")
    if [ "$OS" = "macos" ]; then
        cache_dirs+=("$HOME/Library/Caches/electron")
    else
        [ -n "${XDG_CACHE_HOME:-}" ] && cache_dirs+=("$XDG_CACHE_HOME/electron")
        cache_dirs+=("$HOME/.cache/electron")
    fi

    local dir zip
    for dir in "${cache_dirs[@]}"; do
        [ -d "$dir" ] || continue
        # Recurse: the bad copy may be the top-level zip OR a copy inside an
        # @electron/get hash subdir.
        while IFS= read -r zip; do
            [ -n "$zip" ] || continue
            if rm -f "$zip" 2>/dev/null; then
                removed="$removed$zip
"
            fi
        done <<EOF
$(find "$dir" -type f -name 'electron-*.zip' 2>/dev/null)
EOF
    done

    # A half-written unpacked dir from an interrupted prior pack poisons the
    # rename even after the zip is fixed (mac-arm64-unpacked / linux-unpacked).
    local release_dir="$desktop_dir/release"
    if [ -d "$release_dir" ]; then
        local unpacked
        while IFS= read -r unpacked; do
            [ -n "$unpacked" ] || continue
            if rm -rf "$unpacked" 2>/dev/null; then
                removed="$removed$unpacked
"
            fi
        done <<EOF
$(find "$release_dir" -maxdepth 1 -type d -name '*-unpacked' 2>/dev/null)
EOF
    fi

    printf '%s' "$removed"
}

# Run the desktop pack in $1 (the apps/desktop dir). `npm run pack` = tsc +
# vite build + electron-builder --dir, producing an unpacked app for the
# current OS. Signing auto-discovery is disabled so electron-builder falls back
# to an ad-hoc signature instead of grabbing an unrelated Developer ID from the
# keychain (a real signed/notarized .dmg needs Apple credentials — a separate
# release concern). Optional $2 = an ELECTRON_MIRROR base URL for this attempt,
# used as a fallback when the default GitHub release download is blocked.
_desktop_pack() {
    local desktop_dir="$1"
    local mirror="${2:-}"
    if [ -n "$mirror" ]; then
        ( cd "$desktop_dir" && ELECTRON_MIRROR="$mirror" CSC_IDENTITY_AUTO_DISCOVERY=false npm run pack )
    else
        ( cd "$desktop_dir" && CSC_IDENTITY_AUTO_DISCOVERY=false npm run pack )
    fi
}

# Last-resort Electron mirror after GitHub download fails (#47266).
DESKTOP_ELECTRON_FALLBACK_MIRROR="https://npmmirror.com/mirrors/electron/"

# Per-attempt wall-clock cap for the desktop npm install / electron-builder pack
# (#39219). A stalled (not failed) Electron download on a throttled/blocked link
# never returns, so without this the installer hangs forever on "Build desktop
# app". 900s is generous enough for a slow-but-progressing ~150MB fetch + build;
# override with DESKTOP_BUILD_TIMEOUT for very slow links.
DESKTOP_BUILD_TIMEOUT="${DESKTOP_BUILD_TIMEOUT:-900}"

# Wall-clock cap for the plain registry `npm install`s (browser-tools + TUI
# deps). Same #39219 stall class but no ~150MB Electron binary, so a shorter
# default; override with NODE_DEPS_TIMEOUT for very slow links.
NODE_DEPS_TIMEOUT="${NODE_DEPS_TIMEOUT:-600}"

# Electron package dir — workspace-local nest first, then root hoist.
_electron_dir() {
    local install_dir="$1"
    if [ -d "$install_dir/apps/desktop/node_modules/electron" ]; then
        printf '%s\n' "$install_dir/apps/desktop/node_modules/electron"
    else
        printf '%s\n' "$install_dir/node_modules/electron"
    fi
}

# True when dist/ holds a usable Electron binary (#38673 / run-electron-builder.mjs).
_electron_dist_ok() {
    local install_dir="$1"
    local electron_dir
    electron_dir="$(_electron_dir "$install_dir")"
    if [ "$OS" = "macos" ]; then
        [ -e "$electron_dir/dist/Electron.app/Contents/MacOS/Electron" ]
    else
        [ -e "$electron_dir/dist/electron" ]
    fi
}

# Best-effort: run electron/install.js to populate dist/ (optional mirror).
_restore_electron_dist() {
    local install_dir="$1"
    local mirror="${2:-}"
    local electron_dir
    electron_dir="$(_electron_dir "$install_dir")"
    _electron_dist_ok "$install_dir" && return 0

    [ -f "$electron_dir/install.js" ] || return 1
    command -v node >/dev/null 2>&1 || return 1

    rm -rf "$electron_dir/dist" 2>/dev/null || true
    rm -f "$electron_dir/path.txt" 2>/dev/null || true

    if [ -n "$mirror" ]; then
        ( cd "$electron_dir" && ELECTRON_MIRROR="$mirror" node install.js ) || true
    else
        ( cd "$electron_dir" && node install.js ) || true
    fi
    _electron_dist_ok "$install_dir"
}

_electron_pkg_staged_missing_dist() {
    local install_dir="$1"
    local electron_dir
    electron_dir="$(_electron_dir "$install_dir")"
    [ -f "$electron_dir/package.json" ] && [ -f "$electron_dir/install.js" ] && ! _electron_dist_ok "$install_dir"
}

_restore_electron_dist_with_fallback() {
    local install_dir="$1"
    _restore_electron_dist "$install_dir" \
        || { [ -z "${ELECTRON_MIRROR:-}" ] && _restore_electron_dist "$install_dir" "$DESKTOP_ELECTRON_FALLBACK_MIRROR"; }
}

# Build apps/desktop into a launchable native app. Mirrors install.ps1's
# Install-Desktop: a root-level npm install so the apps/* workspace resolves
# the desktop's own deps (Electron ~150MB), then `npm run pack`
# (electron-builder --dir) which emits an unpacked app for the current OS. Only invoked
# via the 'desktop' stage / --include-desktop, which the Electron app's own
# first-launch bootstrap never requests (it must not rebuild itself).
install_desktop_voice_deps() {
    # Desktop ships with working voice out of the box: eagerly install the
    # wake-word + local-STT stacks ([wake] + [voice] extras) instead of
    # leaving them to lazy first-use install. Policy change (Teknium, July
    # 2026, #70509 testing): the first ear-click used to trigger a
    # multi-minute onnxruntime pip install that froze the UI and blew RPC
    # timeouts. Lazy install remains the fallback for CLI-only installs and
    # for anything this best-effort step fails to fetch.
    local _prev_venv="${VIRTUAL_ENV:-}"
    if [ "$USE_VENV" = true ]; then
        export VIRTUAL_ENV="$INSTALL_DIR/venv"
    fi
    if [ -z "${UV_CMD:-}" ]; then
        install_uv || true
    fi
    if [ -z "${UV_CMD:-}" ]; then
        log_warn "uv unavailable — voice/wake deps will lazy-install at first use instead"
        return 0
    fi
    log_info "Installing voice + wake-word dependencies (onnxruntime, faster-whisper — 1-3min)..."
    if (cd "$INSTALL_DIR" && $UV_CMD pip install -e ".[wake,voice]") ; then
        log_success "Voice + wake-word dependencies installed"
    else
        log_warn "Voice/wake dependency install failed — they will lazy-install at first use"
    fi
    if [ "$USE_VENV" = true ] && [ -z "$_prev_venv" ]; then
        unset VIRTUAL_ENV
    fi
    return 0
}

install_desktop() {
    local desktop_dir="$INSTALL_DIR/apps/desktop"

    # The desktop stage only runs when a build is explicitly requested
    # (--include-desktop / 'desktop' stage), so a missing toolchain is a hard
    # failure, not a silent skip — a silent skip yields a "complete" install
    # with no app and a confusing "couldn't find a built desktop" at launch.
    # Always re-resolve Node here. Stages run in separate processes, so we can't
    # trust an earlier check; more importantly check_node now enforces the
    # supported Node lines and prepends the Hermes-managed Node to PATH, so
    # the build never runs on a too-old system Node — the cause of the opaque
    # "Build desktop app … exit code 1" failure (Vite crashes on old Node).
    check_node
    if ! command -v npm >/dev/null 2>&1; then
        log_error "Cannot build desktop app: Node.js / npm unavailable"
        log_info "Install Node.js and retry: cd $desktop_dir && npm run pack"
        return 1
    fi
    if [ ! -f "$desktop_dir/package.json" ]; then
        log_warn "Skipping desktop build (apps/desktop not present in checkout)"
        return 0
    fi

    # 1. Root workspace install so apps/desktop's deps (Electron, Vite,
    #    node-pty prebuilds) resolve. The browser-tools install runs in the
    #    repo-root package workspace, which does not pull apps/* deps.
    #
    #    Prefer `npm ci`: it deletes node_modules and reinstalls from the
    #    lockfile, so it always produces a complete tree. Bare `npm install`
    #    can report "up to date" against a stale node_modules/.package-lock.json
    #    marker while node_modules is actually empty (Windows workspace-hoisting
    #    flake) — leaving tsc/typescript unresolved and `npm run pack`'s
    #    `tsc -b` failing with no obvious cause. Fall back to `npm install`
    #    only if `npm ci` is unavailable or the lockfile is out of sync.
    #
    #    Both the install and the build below are wrapped in a hard wall-clock
    #    timeout (#39219): the Electron binary (~150MB) is fetched from GitHub,
    #    and on a throttled/blocked connection that download can *stall* — npm
    #    neither errors nor exits, so the installer sits on "Build desktop app"
    #    forever with only `npm warn deprecated` lines visible. A stall now
    #    converts to a non-zero exit, which feeds the existing self-heal /
    #    mirror-fallback escalation instead of hanging the whole install.
    #
    #    The `npm ci` and its `npm install` fallback SHARE one budget: a stalled
    #    link wedges both identically, so giving each a full DESKTOP_BUILD_TIMEOUT
    #    would double the worst-case hang. We compute a single deadline and pass
    #    the remaining seconds to the fallback (min 30s so it still gets a real
    #    attempt if `npm ci` failed fast rather than stalling).
    log_info "Installing desktop workspace dependencies (includes Electron ~150MB, 1-3min)..."
    local _deps_start _deps_remaining
    _deps_start=$(date +%s)
    if run_with_timeout "$DESKTOP_BUILD_TIMEOUT" bash -c 'cd "$1" && npm ci --include=optional && node apps/desktop/scripts/ensure-rolldown-binding.mjs' _ "$INSTALL_DIR"; then
        log_success "Desktop workspace dependencies installed"
    elif _deps_remaining=$(( DESKTOP_BUILD_TIMEOUT - ($(date +%s) - _deps_start) )); \
         [ "$_deps_remaining" -lt 30 ] && _deps_remaining=30; \
         run_with_timeout "$_deps_remaining" bash -c 'cd "$1" && npm install --include=optional && node apps/desktop/scripts/ensure-rolldown-binding.mjs' _ "$INSTALL_DIR"; then
        log_success "Desktop workspace dependencies installed"
    elif _electron_pkg_staged_missing_dist "$INSTALL_DIR"; then
        log_warn "Desktop dependency install failed with a missing Electron dist; attempting self-heal..."
        _restore_electron_dist_with_fallback "$INSTALL_DIR" || true
    else
        log_error "Desktop workspace npm install failed"
        # Common cause: a previous 'sudo npm'/'sudo npx' left root-owned files in
        # ~/.npm, so this non-root install can't write the shared cache. npm hides
        # it behind a confusing EEXIST / "File exists" message while the real errno
        # is EACCES (-13). Point the user at the fix instead of a raw npm trace.
        log_info "If the errors above mention EACCES / 'permission denied' / EEXIST while"
        log_info "writing the npm cache, your ~/.npm likely holds root-owned files from an"
        log_info "earlier 'sudo npm' or 'sudo npx'. Reclaim ownership and retry:"
        log_info "  sudo chown -R \"\$(id -un)\" ~/.npm && npm cache verify"
        log_info "Then re-run this installer, or build manually:"
        log_info "  cd \"$INSTALL_DIR\" && npm ci && cd apps/desktop && npm run pack"
        return 1
    fi

    # 2. Build, with up to three escalating attempts so a transient/blocked
    #    Electron download self-heals instead of failing the whole install:
    #      a) plain `npm run pack` (downloads Electron from GitHub),
    #      b) on failure, purge a corrupt cached zip + stale unpacked dir and
    #         retry (matches install.ps1 / `hermes desktop`),
    #      c) on still-failing, fall back to a public Electron mirror — this is
    #         the GitHub-blocked/throttled case (the repeating "retrying" log).
    log_info "Building desktop app (this takes 1-3 minutes)..."
    local pack_ok=false
    if run_with_timeout "$DESKTOP_BUILD_TIMEOUT" _desktop_pack "$desktop_dir"; then
        pack_ok=true
    else
        local purged=""
        local restored=false
        if ! _electron_dist_ok "$INSTALL_DIR"; then
            purged="$(clear_electron_build_cache "$desktop_dir")"
            if _restore_electron_dist "$INSTALL_DIR"; then restored=true; fi
        fi
        if [ "$restored" = true ]; then
            log_warn "Desktop build failed; refreshed the Electron download and retrying once..."
            if run_with_timeout "$DESKTOP_BUILD_TIMEOUT" _desktop_pack "$desktop_dir"; then
                pack_ok=true
            fi
        fi
    fi

    # (c) GitHub blocked → mirror fallback (#47266).
    if [ "$pack_ok" = false ] && [ -z "${ELECTRON_MIRROR:-}" ] && ! _electron_dist_ok "$INSTALL_DIR"; then
        log_warn "Desktop build still failing — the Electron download from GitHub looks blocked."
        log_warn "Re-downloading Electron via a public mirror ($DESKTOP_ELECTRON_FALLBACK_MIRROR), then rebuilding..."
        log_warn "  (set ELECTRON_MIRROR yourself to use a different/trusted mirror)"
        _electron_dist_ok "$INSTALL_DIR" || _restore_electron_dist "$INSTALL_DIR" "$DESKTOP_ELECTRON_FALLBACK_MIRROR" || true
        if run_with_timeout "$DESKTOP_BUILD_TIMEOUT" _desktop_pack "$desktop_dir" "$DESKTOP_ELECTRON_FALLBACK_MIRROR"; then
            pack_ok=true
        fi
    fi

    if [ "$pack_ok" = false ]; then
        log_error "Desktop app build failed"
        # If the log shows repeated "retrying" lines fetching the Electron zip,
        # the binary download is blocked/throttled (firewall, proxy, region) and
        # the mirror fallback above also couldn't reach a host. Try a mirror you
        # trust and rebuild (@electron/get honors ELECTRON_MIRROR):
        if ! _electron_dist_ok "$INSTALL_DIR"; then
            log_info "If the log shows Electron download retries, rebuild via a reachable mirror:"
            log_info "  ELECTRON_MIRROR=<mirror-base-url> \\"
            log_info "    bash -c 'cd \"$desktop_dir\" && CSC_IDENTITY_AUTO_DISCOVERY=false npm run pack'"
        fi
        log_info "Otherwise build manually: cd $desktop_dir && npm run pack"
        return 1
    fi

    local app=""
    if [ "$OS" = "linux" ]; then
        if [ -x "$desktop_dir/release/linux-unpacked/Hermes" ]; then
            app="$desktop_dir/release/linux-unpacked/Hermes"
        elif [ -x "$desktop_dir/release/linux-unpacked/hermes" ]; then
            app="$desktop_dir/release/linux-unpacked/hermes"
        fi
    else
        local cand
        for cand in \
            "$desktop_dir/release/mac-arm64/Hermes.app" \
            "$desktop_dir/release/mac/Hermes.app"; do
            if [ -d "$cand" ]; then
                app="$cand"
                break
            fi
        done
    fi
    if [ -z "$app" ]; then
        log_error "Desktop build completed but no app was found under $desktop_dir/release/"
        return 1
    fi
    log_success "Desktop app built: $app"

    # Linux: Electron's chrome-sandbox helper needs root:root 4755 or the
    # sandboxed renderer will abort on startup.  Check the file is a regular
    # file (not a symlink) before chown/chmod so we don't follow an
    # attacker-controlled link to an arbitrary path.
    if [ "$OS" = "linux" ]; then
        local sandbox="$desktop_dir/release/linux-unpacked/chrome-sandbox"
        if [ -f "$sandbox" ] && [ ! -L "$sandbox" ]; then
            if [ "$(id -u)" -eq 0 ]; then
                chown root:root "$sandbox" && chmod 4755 "$sandbox" || {
                    log_error "Cannot configure Electron sandbox helper: $sandbox"
                    return 1
                }
            elif command -v sudo >/dev/null 2>&1; then
                sudo chown root:root "$sandbox" && sudo chmod 4755 "$sandbox" || {
                    log_error "Cannot configure Electron sandbox helper (sudo failed): $sandbox"
                    return 1
                }
            else
                log_error "Cannot configure Electron sandbox helper without sudo: $sandbox"
                return 1
            fi
        fi
    fi

    # macOS: route through the same config-aware signing fixup as
    # `hermes desktop`, so install/repair and self-update agree about the app's
    # identity. The fixup preserves the Electron entitlement plists and signs
    # with a stable Designated Requirement (configured keychain identity, else
    # identifier-pinned ad-hoc), so macOS TCC grants — Full Disk Access,
    # Desktop/Downloads/Documents, Accessibility, microphone — survive the
    # rebuild instead of resetting on every update. The shell's
    # publisher-signing decision governed the build and is passed explicitly so
    # importing Python cannot reverse it by loading HERMES_HOME/.env. If the
    # helper is unavailable or fails, branch into the historical quarantine
    # strip + deep ad-hoc repair so a broken venv never leaves the bundle
    # unsigned/unlaunchable.
    if [ "$OS" = "macos" ] && [ -z "${CSC_LINK:-}" ] && [ -z "${APPLE_SIGNING_IDENTITY:-}" ] && command -v codesign >/dev/null 2>&1; then
        local config_python="$INSTALL_DIR/venv/bin/python"
        local fixup_ok=""
        if [ -x "$config_python" ]; then
            if HERMES_HOME="$HERMES_HOME" "$config_python" - "$desktop_dir" <<'PYEOF'
import sys
from pathlib import Path
from hermes_cli.main import _desktop_macos_relaunchable_fixup
ok = _desktop_macos_relaunchable_fixup(
    Path(sys.argv[1]), publisher_signing_configured=False
)
sys.exit(0 if ok else 1)
PYEOF
            then
                fixup_ok=1
            else
                log_warn "Config-aware macOS signing fixup failed; applying the historical ad-hoc fallback."
            fi
        fi
        if [ -z "$fixup_ok" ]; then
            xattr -cr "$app" 2>/dev/null || true
            codesign --force --deep --sign - "$app" >/dev/null 2>&1 || true
        fi
    fi

    # `npm install` + `npm run pack` rewrite lockfiles; restore them so the
    # checkout stays clean for the next `hermes update`.
    restore_dirty_lockfiles "$INSTALL_DIR"
}

# Each --stage runs in its own process, so (unlike the monolithic main() where
# clone_repo cd's once and later steps inherit it) a stage that operates on the
# checkout must cd into it explicitly. Without this, install_deps/setup_path run
# from the desktop app's cwd and resolve `.` / the venv against the wrong tree.
require_install_dir() {
    if [ -z "$INSTALL_DIR" ] || [ ! -d "$INSTALL_DIR" ]; then
        log_error "Install directory not found: ${INSTALL_DIR:-<unset>}"
        log_info "The 'repository' stage must run before this one."
        return 1
    fi
    cd "$INSTALL_DIR"
}

# Desktop bootstrap stage protocol. Mirrors the Windows install.ps1 surface
# closely enough for the Electron bootstrap runner to show structured progress.
run_stage_body() {
    local stage="$1"

    case "$stage" in
        prerequisites)
            print_banner
            detect_os
            resolve_install_layout
            install_uv
            check_python
            check_git
            check_node
            check_cxx_compiler
            check_network_prerequisites
            install_system_packages
            ;;
        repository)
            detect_os
            resolve_install_layout
            check_git
            clone_repo
            ;;
        venv)
            detect_os
            resolve_install_layout
            require_install_dir
            install_uv
            check_python
            setup_venv
            ;;
        python-deps)
            detect_os
            resolve_install_layout
            require_install_dir
            install_uv
            check_python
            install_deps
            ;;
        node-deps)
            detect_os
            resolve_install_layout
            require_install_dir
            check_node
            install_node_deps || return
            install_uv
            install_browser_use_cli
            install_computer_use_driver
            ;;
        path)
            detect_os
            resolve_install_layout
            require_install_dir
            setup_path
            ;;
        config)
            detect_os
            resolve_install_layout
            require_install_dir
            copy_config_templates
            ;;
        setup)
            detect_os
            resolve_install_layout
            require_install_dir
            run_setup_wizard
            ;;
        gateway)
            detect_os
            resolve_install_layout
            require_install_dir
            maybe_start_gateway
            ;;
        desktop)
            detect_os
            resolve_install_layout
            require_install_dir
            # Each stage runs in its own process, so the Hermes-managed Node
            # provisioned during prerequisites/node-deps (at $HERMES_HOME/node/bin)
            # isn't on PATH here. check_node re-adds it (or installs if missing)
            # so install_desktop can find npm instead of silently skipping.
            check_node
            install_desktop_voice_deps
            install_desktop
            ;;
        complete)
            detect_os
            resolve_install_layout
            print_success
            write_bootstrap_marker
            # Code-scoped stamp: write next to the install tree, not into
            # $HERMES_HOME. $HERMES_HOME is a shared data dir (it can be
            # bind-mounted into a Docker gateway too), so a stamp there gets
            # clobbered by the container's 'docker' stamp and wrongly blocks
            # 'hermes update' on this host install. See detect_install_method().
            echo "git" > "$INSTALL_DIR/.install_method"
            ;;
        *)
            append_shell_path "$HOME/.bashrc" "$SHELL_PATH_LINE" "$SHELL_PATH_SETUP_RE"
            append_shell_path "$HOME/.profile" "$SHELL_PATH_LINE" "$SHELL_PATH_SETUP_RE"
            # A login bash reads only the first of .bash_profile, .bash_login, .profile,
            # so an existing earlier file hides the .profile line above.
            local rc
            for rc in "$HOME/.bash_profile" "$HOME/.bash_login"; do
                if [ -f "$rc" ]; then
                    append_shell_path "$rc" "$SHELL_PATH_LINE" "$SHELL_PATH_SETUP_RE"
                fi
            done
            ;;
    esac
}

stage_products() {
    # The whole tail in one place, by calling the completion an update calls:
    # publish the commands, build the products (tui/web, plus the desktop app),
    # then run the post-build maintenance that syncs bundled skills and migrates
    # config. Node, browsers and the frontend build tools arrive through pm as
    # the build asks for them; the bootstrap interpreter itself only re-enters
    # the tree on PM's selected Python.
    local boot_py
    local args=(--source "$INSTALL_DIR")
    bootstrap_python
    if [ "$INCLUDE_DESKTOP" = true ] || desktop_product_present; then
        args+=(--desktop)
    fi
    (cd "$INSTALL_DIR" && run_logged "Building the hermes command and apps" \
        "$boot_py" -I -B -X utf8 hermes_cli/source_completion.py "${args[@]}") \
        || fail "app products or command publication failed" products_build_failed
    wire_shell_path
    log_success "app products and hermes command ready"
}

stage_desktop() {
    # External-caller contract: `--stage desktop` stays dispatchable on its own
    # (the manifest never lists it now -- --include-desktop selects the desktop
    # product inside `products`). Same completion call, desktop selected.
    INCLUDE_DESKTOP=true
    stage_products
}

stage_config() {
    mkdir -p "$HERMES_HOME"/cron "$HERMES_HOME"/sessions "$HERMES_HOME"/logs \
        "$HERMES_HOME"/pairing "$HERMES_HOME"/hooks "$HERMES_HOME"/image_cache \
        "$HERMES_HOME"/audio_cache "$HERMES_HOME"/memories "$HERMES_HOME"/skills
    if [ ! -f "$HERMES_HOME/.env" ]; then
        cp "$INSTALL_DIR/.env.example" "$HERMES_HOME/.env" 2>/dev/null || touch "$HERMES_HOME/.env"
    fi
    chmod 600 "$HERMES_HOME/.env"
    if [ ! -f "$HERMES_HOME/config.yaml" ] && [ -f "$INSTALL_DIR/cli-config.yaml.example" ]; then
        cp "$INSTALL_DIR/cli-config.yaml.example" "$HERMES_HOME/config.yaml"
    fi
    log_success "config prepared in $HERMES_HOME"
}

# Interactive stages read the terminal, not stdin: under `curl | bash` stdin
# IS the script. Probe by opening /dev/tty -- a Docker build has the device
# node in its mount namespace but opening it fails (ENXIO).
has_terminal() { (: </dev/tty) 2>/dev/null; }

stage_setup() {
    if [ "$NON_INTERACTIVE" = true ]; then return 0; fi
    if ! has_terminal; then
        log "setup skipped (no terminal); run 'hermes setup' after install"
        return 0
    fi
    "$INSTALL_DIR/.hermes/bin/hermes" setup </dev/tty || fail "setup failed" setup_failed
}

stage_gateway() {
    if [ "$NON_INTERACTIVE" = true ]; then return 0; fi
    if ! has_terminal; then
        log "gateway setup skipped (no terminal); run 'hermes gateway install' after install"
        return 0
    fi
    # Setup installs the service when it handles the gateway; ask only if it did not.
    "$INSTALL_DIR/.hermes/bin/hermes" gateway install --if-missing </dev/tty || fail "gateway installation failed" gateway_failed
}

stage_complete() {
    local commit
    commit="$INSTALL_COMMIT"
    [ -n "$commit" ] || commit=$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null) || commit=""
    if [ -n "$commit" ]; then
        printf '{\n  "schemaVersion": 1,\n  "pinnedCommit": "%s",\n  "pinnedBranch": "%s",\n  "completedAt": "%s"\n}\n' \
            "$commit" "$BRANCH" "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)" > "$INSTALL_DIR/.hermes-bootstrap-complete.tmp"
        mv -f "$INSTALL_DIR/.hermes-bootstrap-complete.tmp" "$INSTALL_DIR/.hermes-bootstrap-complete"
    fi
    log_success "Hermes Agent install complete. Run: hermes"
}

print_path_reload_hint() {
    # The rc files only reach shells started later, and this installer is
    # always a child (`curl | bash`, `bash install.sh`) that cannot change its
    # parent's PATH. The inherited PATH is the parent's, so it says whether
    # the user can run `hermes` right away.
    case ":$PATH:" in *":$HOME/.local/bin:"*|*":$HOME/.local/bin/:"*) return 0 ;; esac
    local rc
    local login_shell="${SHELL:-}"
    case "${login_shell##*/}" in
        zsh) rc="source ~/.zshrc" ;;
        fish) rc="source ~/.config/fish/config.fish" ;;
        bash|"") rc="source ~/.bashrc" ;;
        *) rc=". ~/.profile" ;;
    esac
    log "Reload your shell to use hermes: open a new terminal, or run: $rc"
}

run_stage() (
    # Keep failure handling out of conditional calls, which disable errexit.
    set -e
    STAGE="$1"
    STAGE_REASON=""
    FAILURE_CLASS=""
    STAGE_INTERRUPTED=false
    STAGE_SKIPPED=false
    trap 'stage_result "$?"' EXIT
    if [ "$INSTALL_LADDER" = true ]; then
        trap 'stage_signal "$?" 130' INT
        trap 'stage_signal "$?" 143' TERM
        trap 'stage_signal "$?" 129' HUP
    fi
    if [ "$NON_INTERACTIVE" = true ] && { [ "$STAGE" = setup ] || [ "$STAGE" = gateway ]; }; then
        STAGE_SKIPPED=true
        STAGE_REASON="needs user input"
        exit 0
    fi
    case "$1" in
        prerequisites) stage_prerequisites ;;
        repository) stage_repository ;;
        venv) stage_venv ;;
        python-deps) stage_python_deps ;;
        config) stage_config ;;
        products) stage_products ;;
        setup) stage_setup ;;
        gateway) stage_gateway ;;
        desktop) stage_desktop ;;
        complete) stage_complete ;;
        *) STAGE_REASON="unknown stage: $1"; printf '%s\n' "$STAGE_REASON" >&2; exit 2 ;;
    esac
)

# Main. Guarded so the script can be SOURCED for its functions (the
# installer-test harness sources it with --manifest, which must define
# the functions and stop before main). Under `curl | bash` BASH_SOURCE is
# empty, and `set -u` would abort on the bare expansion.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
    if [ "$WANT_MANIFEST" = true ]; then
        emit_manifest
        exit 0
    fi

    if [ -n "$STAGE" ] && [ "$JSON" = true ]; then
        trap 'stage_result "$?"' EXIT
    elif [ -z "$STAGE" ]; then
        INSTALL_LADDER=true
        INSTALL_STARTED="$(date +%s 2>/dev/null)"
        INSTALL_RUN_ID="$(od -An -N16 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n')" || INSTALL_RUN_ID=""
        [ "${#INSTALL_RUN_ID}" -eq 32 ] || INSTALL_RUN_ID="$(printf '%08x%08x%08x%08x' "$(( RANDOM * 32768 + RANDOM ))" "$$" "${INSTALL_STARTED:-0}" "$RANDOM")"
        # No clock, no receipt: the reader drops a receipt without both timestamps anyway.
        case "$INSTALL_STARTED" in ''|*[!0-9]*) INSTALL_RUN_ID="" ;; esac
        trap '[ "$?" -eq 0 ] || write_install_receipt failed prerequisites "${FAILURE_CLASS:-other}"' EXIT
        # Ctrl-C, SIGTERM or a closed terminal (SIGHUP) anywhere in the run: one `interrupted` receipt.
        trap 'install_signal INT' INT
        trap 'install_signal TERM' TERM
        trap 'install_signal HUP' HUP
    fi
    check_platform
    trap - EXIT

    if [ -n "$STAGE" ]; then
        run_stage "$STAGE"
        exit "$?"
    fi

    # No --stage: run the whole ladder — the same authoritative list the
    # manifest prints, so --include-desktop inserts desktop here too.
    print_banner
    for s in $(stage_names); do
        # A TERM/HUP noted while the previous stage ran stops the run before this one.
        [ -z "$INSTALL_SIGNAL" ] || install_interrupted
        INSTALL_IN_STAGE=true
        run_stage "$s"
        rc=$?
        INSTALL_IN_STAGE=false
        if [ "$rc" -eq 0 ]; then
            # The stage succeeded, so a Ctrl-C it saw was handled there: carry on.
            [ "$INSTALL_SIGNAL" != INT ] || INSTALL_SIGNAL=""
            continue
        fi
        case "$rc" in
            129) INSTALL_SIGNAL="${INSTALL_SIGNAL:-HUP}" ;;
            130) INSTALL_SIGNAL="${INSTALL_SIGNAL:-INT}" ;;
            143) INSTALL_SIGNAL="${INSTALL_SIGNAL:-TERM}" ;;
        esac
        [ -z "$INSTALL_SIGNAL" ] || install_interrupted
        # The stage normally wrote its receipt; one killed outright (SIGKILL, OOM) did not.
        write_install_receipt failed "${s//-/_}" other
        exit "$rc"
    done
    write_install_receipt success none none
    # Every stage finished: the receipt says success, and a pending TERM/HUP still ends the run.
    [ -z "$INSTALL_SIGNAL" ] || install_interrupted
    trap - INT TERM HUP
    print_path_reload_hint
fi
