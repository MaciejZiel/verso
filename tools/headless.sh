#!/usr/bin/env bash
# Runs a command inside a private, invisible GNOME Shell (headless Wayland, own D-Bus session).
# Nothing appears on the real screen and the app does not talk to an already running Verso.
#   tools/headless.sh python3 tests/ui_smoke.py
set -euo pipefail
if [ "${VERSO_HEADLESS_INNER:-}" != "1" ]; then
  exec dbus-run-session -- env VERSO_HEADLESS_INNER=1 "$0" "$@"
fi
display="verso-headless-$$"
log="$(mktemp)"
env -u G_DEBUG gnome-shell --headless --wayland --no-x11 --virtual-monitor 1280x900 --wayland-display "$display" >"$log" 2>&1 &
shell=$!
trap 'kill $shell 2>/dev/null; wait $shell 2>/dev/null; rm -f "$log"' EXIT
for _ in $(seq 1 100); do
  [ -S "${XDG_RUNTIME_DIR}/${display}" ] && break
  sleep 0.1
done
if [ ! -S "${XDG_RUNTIME_DIR}/${display}" ]; then
  echo "headless gnome-shell did not start:" >&2; cat "$log" >&2; exit 1
fi
# Session files and drafts of test runs go to a throwaway folder, never to the real ~/.local/share/verso.
data_home="$(mktemp -d)"
trap 'kill $shell 2>/dev/null; wait $shell 2>/dev/null; rm -f "$log"; rm -rf "$data_home"' EXIT
# The real ~/.local/share stays readable through XDG_DATA_DIRS, so an app installed there
# (GSettings schema, icons, translations) still works with the throwaway XDG_DATA_HOME.
real_data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
XDG_DATA_DIRS="$real_data_home:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}" \
XDG_DATA_HOME="${VERSO_TEST_DATA_HOME:-$data_home}" WAYLAND_DISPLAY="$display" GDK_BACKEND=wayland "$@"
