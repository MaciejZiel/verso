#!/usr/bin/env bash
# Usuwa Verso z konta użytkownika i przywraca Edytor tekstu jako domyślny dla .md i .json.
set -euo pipefail
APP_ID=io.github.maciejziel.Verso
ICONS="$HOME/.local/share/icons/hicolor"
rm -f "$HOME/.local/bin/verso" "$HOME/.local/share/applications/$APP_ID.desktop" \
  "$ICONS/scalable/apps/$APP_ID.svg" "$ICONS/symbolic/apps/$APP_ID-symbolic.svg"
gtk-update-icon-cache -q -f -t "$ICONS" 2>/dev/null || true
update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true
for mime in text/markdown text/x-markdown application/json; do
  xdg-mime default org.gnome.TextEditor.desktop "$mime"
done
echo "Odinstalowano."
