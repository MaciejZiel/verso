#!/usr/bin/env bash
# Removes the ~/.local installation and makes GNOME Text Editor the default for .md and .json again.
set -euo pipefail
cd "$(dirname "$0")"
if [ -d _build ]; then
  ninja -C _build uninstall >/dev/null
fi
for mime in text/markdown text/x-markdown application/json; do
  xdg-mime default org.gnome.TextEditor.desktop "$mime"
done
echo "Verso uninstalled"
