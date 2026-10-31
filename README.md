# Verso

A GNOME text editor with live previews for Markdown, JSON and more (GTK4, libadwaita, GtkSourceView, WebKitGTK).

- **Markdown** rendered like on GitHub: tables, task lists, highlighted code, table of contents
- **JSON** as a collapsible, searchable tree that keeps the key order of the file
- Edit, Preview, or both Side by Side (Ctrl+E switches)
- Tabs, find and replace, spell checking, dark mode, session restore

See [ROADMAP.md](ROADMAP.md) for what is coming before the Flathub release.

## Running from the source tree

Ubuntu 26.04 needs:

```
sudo apt install gir1.2-webkit-6.0 gir1.2-gtksource-5 gir1.2-spelling-1 meson gettext
```

```
./bin/verso file.md      # run without installing (compiles schema and translations into .dev/)
./install.sh             # meson install into ~/.local, default app for .md and .json
./uninstall.sh
```

## Tests

```
python3 -m pytest tests -q                         # unit tests
meson test -C _build                               # desktop file, metainfo and schema validation
GSETTINGS_SCHEMA_DIR=.dev/schemas GSETTINGS_BACKEND=memory tools/headless.sh python3 tests/ui_smoke.py   # real window in an invisible GNOME Shell
```

## Translations

Translations live in `po/`. To update the template after changing strings: `meson compile -C _build verso-pot verso-update-po`.

## License

MIT
