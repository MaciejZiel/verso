#!/usr/bin/env bash
# Regenerates data/screenshots/*.png (MetaInfo and README) in an invisible GNOME Shell.
# The sample files come from tools/screenshot-samples/ and appear as ~/Documents; the UI is in English.
#   tools/screenshots.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/data/screenshots"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$out" "$work/home/Documents" "$work/config/glib-2.0/settings" "$work/schemas"
cp "$root"/tools/screenshot-samples/* "$work/home/Documents/"
glib-compile-schemas --strict --targetdir="$work/schemas" "$root/data"

# shoot NAME FILE MODE DARK(1/0) [SETTING…]: one window, one picture; settings are GSettings keyfile lines.
shoot() {
  local name="$1" file="$2" mode="$3" dark="$4"
  shift 4
  {
    echo "[io/github/maciejziel/Verso]"
    echo "spellcheck=false"
    echo "restore-session=false"
    printf '%s\n' "$@"
  } >"$work/config/glib-2.0/settings/keyfile"
  "$root/tools/headless.sh" env -C "$root" \
    HOME="$work/home" XDG_CONFIG_HOME="$work/config" GSETTINGS_BACKEND=keyfile GSETTINGS_SCHEMA_DIR="$work/schemas" \
    LC_ALL=C.UTF-8 LANGUAGE= VERSO_SCREENSHOT="$out/$name.png" VERSO_MODE="$mode" VERSO_DELAY=3000 \
    VERSO_DARK="$dark" python3 -m verso "$work/home/Documents/$file" >/dev/null 2>&1
  echo "$out/$name.png"
}

shoot markdown-split trip-notes.md split 0
shoot markdown-outline-dark trip-notes.md preview 1 "show-outline=true"
shoot json-tree weather.json preview 0
shoot csv-table-dark cities.csv preview 1
