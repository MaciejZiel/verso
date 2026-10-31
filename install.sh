#!/usr/bin/env bash
# Builds and installs Verso into ~/.local with Meson (no sudo) and makes it the default for .md and .json.
set -euo pipefail
cd "$(dirname "$0")"
# An older version installed ~/.local/bin/verso as a symlink into this repo; never install through it.
[ -L "$HOME/.local/bin/verso" ] && rm "$HOME/.local/bin/verso"
if [ -d _build ]; then
  meson setup --reconfigure _build --prefix="$HOME/.local" >/dev/null
else
  meson setup _build --prefix="$HOME/.local" >/dev/null
fi
meson install -C _build --quiet
if [ "${1:-}" != "--no-default" ]; then
  for mime in text/markdown text/x-markdown application/json; do
    xdg-mime default io.github.maciejziel.Verso.desktop "$mime"
  done
fi
echo "Verso installed to ~/.local"
