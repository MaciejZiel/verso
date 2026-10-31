#!/usr/bin/env bash
# Instaluje Podgląd dla bieżącego użytkownika (bez sudo) i ustawia go jako domyślny dla .md i .json.
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
APP_ID=pl.mzielinski.Podglad
BIN="$HOME/.local/bin/podglad"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"

mkdir -p "$(dirname "$BIN")" "$APPS" "$ICONS/scalable/apps" "$ICONS/symbolic/apps"
ln -sfn "$REPO/bin/podglad" "$BIN"
sed "s|@BIN@|$BIN|g" "$REPO/data/$APP_ID.desktop.in" > "$APPS/$APP_ID.desktop"
cp "$REPO/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$ICONS/scalable/apps/"
cp "$REPO/data/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg" "$ICONS/symbolic/apps/"
gtk-update-icon-cache -q -f -t "$ICONS" 2>/dev/null || true
update-desktop-database -q "$APPS" 2>/dev/null || true

if [ "${1:-}" != "--no-default" ]; then
  for mime in text/markdown text/x-markdown application/json; do
    xdg-mime default "$APP_ID.desktop" "$mime"
  done
fi
echo "Zainstalowano: $APPS/$APP_ID.desktop"
