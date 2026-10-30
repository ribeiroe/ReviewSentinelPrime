#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  build-releases.sh
#  Stamps the version across all project files and assembles distributable ZIPs.
#
#  Usage:
#    ./build-releases.sh                      # build using the current version
#    ./build-releases.sh --version v2.6.0     # stamp new version then build
#
#  Output:
#    dist/ReviewSentinel-<VERSION>-Docker.zip
#    dist/ReviewSentinel-<VERSION>-Python.zip
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DIST="$ROOT/dist"

# ── Current version (single source of truth in this file) ────────────────────
VERSION="v2.2.0"

APP_ITEMS=("app.py" "requirements.txt" "templates" "static" "LICENSE")

# ── Parse arguments ──────────────────────────────────────────────────────────
NEW_VERSION=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --version|-v)
            NEW_VERSION="$2"
            shift 2
            ;;
        *)
            echo "  [ERROR] Unknown argument: $1"
            echo "  Usage: $0 [--version vX.Y.Z]"
            exit 1
            ;;
    esac
done

# ── Validate version format ───────────────────────────────────────────────────
if [[ -n "$NEW_VERSION" ]]; then
    if [[ ! "$NEW_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "  [ERROR] Invalid version format: '$NEW_VERSION'"
        echo "          Expected format: vX.Y.Z  (e.g. v2.6.0)"
        exit 1
    fi
fi

# ── Portable sed -i (macOS requires '' after -i, Linux does not) ─────────────
sedi() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "$@"
    else
        sed -i "$@"
    fi
}

# ── Stamp version across all project files ────────────────────────────────────
stamp_version() {
    local old="$1"
    local new="$2"

    echo ""
    echo "  ── Stamping version: $old → $new ──────────────────────────────────"

    # Parallel arrays: file[i] is stamped with pattern[i]
    local files=(
        "$ROOT/build-releases.sh"
        "$ROOT/build-releases.ps1"
        "$ROOT/README.md"
        "$ROOT/app.py"
        "$ROOT/releases/docker/README.md"
        "$ROOT/releases/python/README.md"
    )
    local patterns=(
        's|^VERSION="v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*"|VERSION="'"$new"'"|'
        's|^\$CurrentVersion = "v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*"|\$CurrentVersion = "'"$new"'"|'
        's|^v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]* · Python 3.12 · Flask 3.1 · Docker ready|'"$new"' · Python 3.12 · Flask 3.1 · Docker ready|'
        's|^RSP_VERSION = "v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*"|RSP_VERSION = "'"$new"'"|'
        's|Review Sentinel Prime v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*|Review Sentinel Prime '"$new"'|g'
        's|Review Sentinel Prime v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*|Review Sentinel Prime '"$new"'|g'
    )

    for i in "${!files[@]}"; do
        local file="${files[$i]}"
        local pattern="${patterns[$i]}"
        if [[ -f "$file" ]]; then
            sedi "$pattern" "$file"
            # Extra pass for Python README zip filename examples
            if [[ "$file" == *"releases/python/README.md" ]]; then
                sedi 's|ReviewSentinelPrime_v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*|ReviewSentinelPrime_'"$new"'|g' "$file"
            fi
            echo "    [✓] $(realpath --relative-to="$ROOT" "$file" 2>/dev/null || basename "$file")"
        else
            echo "    [!] File not found — skipped: $file"
        fi
    done

    echo ""
    echo "  Version stamped. All files updated to $new."
    echo ""
}

# ── Perform version stamp if requested ───────────────────────────────────────
if [[ -n "$NEW_VERSION" ]]; then
    if [[ "$NEW_VERSION" == "$VERSION" ]]; then
        echo "  [INFO] Requested version ($NEW_VERSION) matches current version. No stamp needed."
    else
        echo ""
        echo "  ┌─────────────────────────────────────────────────────────────┐"
        echo "  │  Version update: $VERSION  →  $NEW_VERSION"
        echo "  │"
        echo "  │  The following files will be updated:"
        echo "  │    • build-releases.sh"
        echo "  │    • build-releases.ps1"
        echo "  │    • README.md"
        echo "  │    • app.py"
        echo "  │    • releases/docker/README.md"
        echo "  │    • releases/python/README.md"
        echo "  └─────────────────────────────────────────────────────────────┘"
        echo ""
        read -r -p "  Proceed? [y/N] " confirm
        case "$confirm" in
            [yY][eE][sS]|[yY])
                stamp_version "$VERSION" "$NEW_VERSION"
                VERSION="$NEW_VERSION"
                ;;
            *)
                echo "  Aborted. No files were changed."
                exit 0
                ;;
        esac
    fi
fi

# ── Helper: write minimal data skeleton ──────────────────────────────────────
add_data_skeleton() {
    local stage="$1"
    mkdir -p "$stage/data/results"
    printf '[]'  > "$stage/data/searches.json"
    printf '{}'  > "$stage/data/smtp_config.json"
    # Relay configs live in the tracked releases/relay/ folder so every release ships them.
    # They hold public form URLs and field IDs only - never credentials.
    for cfg in google_form_config.json google_form_feedback_config.json google_form_feature_config.json; do
        if [ ! -f "$ROOT/releases/relay/$cfg" ]; then
            echo "    [ERROR] Relay config missing: releases/relay/$cfg"
            exit 1
        fi
        cp "$ROOT/releases/relay/$cfg" "$stage/data/$cfg"
    done
}

# ── Helper: copy app source items ─────────────────────────────────────────────
copy_app_items() {
    local stage="$1"
    for item in "${APP_ITEMS[@]}"; do
        cp -r "$ROOT/$item" "$stage/"
    done
}

# ── Prepare dist/ folder ─────────────────────────────────────────────────────
rm -rf "$DIST"
mkdir -p "$DIST"

# ════════════════════════════════════════════════════════════════════════════
#  RELEASE 1 — Docker
# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "  Building: ReviewSentinel-${VERSION}-Docker.zip ..."

DOCKER_STAGE="$DIST/_stage_docker"
mkdir -p "$DOCKER_STAGE"

copy_app_items "$DOCKER_STAGE"
add_data_skeleton "$DOCKER_STAGE"

for item in "$ROOT/releases/docker/"*; do
    cp -r "$item" "$DOCKER_STAGE/"
done
chmod +x "$DOCKER_STAGE/start-app.command" "$DOCKER_STAGE/stop-app.command"

DOCKER_ZIP="$DIST/ReviewSentinel-${VERSION}-Docker.zip"
(cd "$DOCKER_STAGE" && zip -r "$DOCKER_ZIP" . -x "*.DS_Store" > /dev/null)
rm -rf "$DOCKER_STAGE"
echo "  [OK] dist/ReviewSentinel-${VERSION}-Docker.zip"

# ════════════════════════════════════════════════════════════════════════════
#  RELEASE 2 — Plain Python
# ════════════════════════════════════════════════════════════════════════════
echo ""
echo "  Building: ReviewSentinel-${VERSION}-Python.zip ..."

PYTHON_STAGE="$DIST/_stage_python"
mkdir -p "$PYTHON_STAGE"

copy_app_items "$PYTHON_STAGE"
add_data_skeleton "$PYTHON_STAGE"

for item in "$ROOT/releases/python/"*; do
    cp -r "$item" "$PYTHON_STAGE/"
done
chmod +x "$PYTHON_STAGE/start.command" "$PYTHON_STAGE/stop.command"

PYTHON_ZIP="$DIST/ReviewSentinel-${VERSION}-Python.zip"
(cd "$PYTHON_STAGE" && zip -r "$PYTHON_ZIP" . -x "*.DS_Store" > /dev/null)
rm -rf "$PYTHON_STAGE"
echo "  [OK] dist/ReviewSentinel-${VERSION}-Python.zip"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "  ─────────────────────────────────────────"
echo "  Release ZIPs ready in:  dist/"
echo ""
for f in "$DIST"/*.zip; do
    size=$(du -sh "$f" | cut -f1)
    printf "    %-42s %s\n" "$(basename "$f")" "$size"
done
echo "  ─────────────────────────────────────────"
echo ""
