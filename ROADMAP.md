# Verso roadmap

Goal: a GNOME text editor that can replace GNOME Text Editor for everyday work. It should be as fast and native, with live previews and deeper customization, and ship on Flathub in about 20 languages.

Status legend: [ ] todo, [~] in progress, [x] done.

## M1. Foundation

- [x] English source strings with gettext; Polish as the reference translation
- [x] Meson build: install modules, data, icons, desktop file, metainfo, GSettings schema and translations
- [x] GSettings schema: window state, session, all preferences
- [x] Session restore: reopen tabs, selected tab and window size after restart
- [x] JSON tree keeps the key order from the file (the preview payload uses ordered pairs)
- [x] Preferences dialog (`Adw.PreferencesDialog`) with style scheme previews, font, display, indentation, editing, session and default view

## M2. Editor on par with GNOME Text Editor, then better

- [x] Load and save through `GtkSource.FileLoader` / `FileSaver`: encodings, line endings, BOM, compression
- [x] Status and properties popover: cursor position, encoding, line endings, indentation, language (each one changeable)
- [x] Go to line (Ctrl+L)
- [x] Autosave drafts: unsaved documents survive closing the app and a reboot
- [x] Overview map (`GtkSource.Map`) next to the editor
- [x] Preferences: style scheme (all GtkSourceView schemes with live previews), custom font and size, tab width, spaces or tabs, line numbers, current-line highlight, visible whitespace, right margin, bracket auto-pairing, word wrap
- [ ] Optional `.editorconfig` support
- [x] Markdown writing: list and quote continuation, Ctrl+B / Ctrl+I / Ctrl+` / Ctrl+K, links from pasted URLs, pasted images saved to `images/` next to the file
- [x] Outline sidebar for Markdown (F9): headings, current section, jump to a section in the editor or the preview

## M3. Previews

- [x] Move Markdown rendering to markdown-it-py so every block carries its source line
- [x] Synchronized scrolling between editor and preview in split mode
- [x] Markdown extras: front matter, KaTeX math, Mermaid diagrams, footnotes and definition lists (mdit-py-plugins, optional outside the Flatpak)
- [x] Preview themes: GitHub, Document (serif, print-like), Reader (large, centered); content width and preview font
- [x] CSV / TSV: table with sticky header, column sorting, search, delimiter detection
- [x] YAML / TOML / XML: the same tree view as JSON
- [x] HTML: rendered page with scripts off by default; SVG: rendered image
- [x] Print and export to PDF from the preview (`WebKit.PrintOperation`)
- [~] Default view mode per file type (preferences; Markdown and data files so far)

## M4. Release quality

- [x] Startup and memory: the preview's WebView (and web process) is made only when a tab shows it, spell checking loads after the preview; first frame 1.25 s → 0.65 s, five tabs 1 GB → 0.2 GB of WebKit memory

- [ ] Translations: pl (reference), de, fr, es, pt_BR, it, ru, uk, zh_CN, zh_TW, ja, ko, tr, nl, cs, sv, hu, fi, ar, hi, id
- [ ] RTL layout check (ar)
- [x] MetaInfo: description, light and dark screenshots (`tools/screenshots.sh`), releases, OARS rating, URLs; validated with `appstreamcli`
- [x] Accessibility pass: keyboard-only use, labels for icon buttons, screen reader names (UI test checks every icon button)
- [x] English README with screenshots; Weblate later

## M5. Flatpak and Flathub

- [x] Flatpak manifest `io.github.maciejziel.Verso.yml` (GNOME 51; bundled libspelling, markdown-it-py, plugins, pygments, PyYAML); Verso writes its own font configuration without the host's font caches (stale ones hung the preview) and shares its font cache with the web process (`verso/fonts.py`)
- [ ] Print dialog in the Flatpak (goes through the print portal; PDF export is verified)
- [ ] `--filesystem=home` justified in the submission: relative images, links between files, file watching
- [x] GitHub Actions: build the Flatpak on every push (`.github/workflows/flatpak.yml`)
- [ ] Public repo, `v1.0.0` tag and GitHub release
- [ ] Flathub submission PR; app verification through the GitHub account

## After 1.0

- Promotion: This Week in GNOME, r/gnome, r/linux; GNOME Circle application
- Weblate for community translations
- Ideas: snippets, document statistics, split editor (two files side by side)
