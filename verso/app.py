"""Aplikacja: jedna instancja (D-Bus), skróty, powiększenie wspólne dla okien."""

import os
from gettext import gettext as _
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import fonts, session, settings  # noqa: E402
from .window import Window  # noqa: E402

APP_ID = settings.APP_ID
VERSION = "1.0"
ZOOM_STEPS = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)

SHORTCUTS = {
    "win.open": ["<Control>o"],
    "win.new-tab": ["<Control>t", "<Control>n"],
    "app.new-window": ["<Control><Shift>n"],
    "win.save": ["<Control>s"],
    "win.save-as": ["<Control><Shift>s"],
    "win.close-tab": ["<Control>w"],
    "win.find": ["<Control>f"],
    "win.replace": ["<Control>h"],
    "win.toggle-view": ["<Control>e"],
    "win.format-json": ["<Control><Shift>i"],
    "win.goto-line": ["<Control>l"],
    "win.outline": ["F9"],
    "win.print": ["<Control>p"],
    "win.zoom-in": ["<Control>plus", "<Control>equal", "<Control>KP_Add"],
    "win.zoom-out": ["<Control>minus", "<Control>KP_Subtract"],
    "win.zoom-reset": ["<Control>0", "<Control>KP_0"],
    "win.next-tab": ["<Control>Page_Down", "<Control>Tab"],
    "win.prev-tab": ["<Control>Page_Up", "<Control><Shift>Tab"],
    "app.quit": ["<Control>q"],
    "app.shortcuts": ["<Control>question"],
    "app.preferences": ["<Control>comma"],
}


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        GLib.set_application_name("Verso")
        self.zoom = 1.0
        self._css = Gtk.CssProvider()
        self.add_main_option("new-window", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, _("Open a new window"), None)
        self.connect("handle-local-options", self._on_local_options)

    def _on_local_options(self, _app, options):
        if options.contains("new-window"):
            # Działa też, gdy program już jest uruchomiony: akcja trafia do pierwszej instancji.
            self.register(None)
            if self.get_is_remote():
                self.activate_action("new-window", None)
                return 0
        return -1  # zwykłe uruchomienie: do_activate otworzy okno

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(APP_ID)
        self.zoom = settings.get().get_double("zoom")
        for key in ("use-system-font", "custom-font"):
            settings.get().connect(f"changed::{key}", lambda *_: self._apply_css())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            self._css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._apply_css()
        for name, callback in (
            ("new-window", self._new_window),
            ("quit", self._quit),
            ("about", self._about),
            ("shortcuts", self._shortcuts),
            ("preferences", self._preferences),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a, cb=callback: cb())
            self.add_action(action)
        for action, accels in SHORTCUTS.items():
            self.set_accels_for_action(action, accels)

    def do_activate(self):
        first_window = self.get_active_window() is None
        window = self.get_active_window() or self._new_window()
        if first_window:
            self._restore_session(window)
        if not window.documents:
            window.new_document()
        window.present()
        self._maybe_screenshot(window)

    def do_open(self, files, _n_files, _hint):
        first_window = self.get_active_window() is None
        window = self.get_active_window() or self._new_window()
        if first_window:
            # Opening a file must not drop the last session: its drafts may hold the only copy of unsaved text.
            self._restore_session(window)
        for file in files:
            window.open_file(file)
        window.present()
        self._maybe_screenshot(window)

    def _restore_session(self, window):
        if not settings.get().get_boolean("restore-session"):
            session.prune_drafts(set())
            return
        data = session.load_session()
        keep, restored = set(), []
        for entry in data["documents"]:
            uri, draft_id = entry["uri"], entry["draft"]
            file = Gio.File.new_for_uri(uri) if uri else None
            text = session.read_draft(draft_id) if draft_id else None
            doc = None
            if text is not None:
                doc = window.restore_draft(file, text, draft_id, entry["encoding"], entry["newline"], entry["etag"])
                keep.add(draft_id)
            elif file is not None and file.query_exists(None):
                doc = window.open_file(file)
            restored.append(doc)
        session.prune_drafts(keep)
        selected = data["selected"]
        if 0 <= selected < len(restored) and restored[selected] is not None:
            window.tab_view.set_selected_page(window.tab_view.get_page(restored[selected]))

    def schedule_session_save(self):
        if getattr(self, "_session_timer", 0):
            return
        self._session_timer = GLib.timeout_add(1000, self._save_session_timeout)

    def _save_session_timeout(self):
        self._session_timer = 0
        self.save_session_now()
        return GLib.SOURCE_REMOVE

    def save_session_now(self, closing=None):
        """Writes session.json (and pending drafts) for all open windows, plus `closing` if given."""
        if getattr(self, "_session_timer", 0):
            GLib.source_remove(self._session_timer)
            self._session_timer = 0
        windows = [w for w in self.get_windows() if isinstance(w, Window) and (w is closing or not w.is_closing())]
        if not settings.get().get_boolean("restore-session") or not windows:
            # No open windows means the last one already wrote the session while closing;
            # writing now would replace it with an empty list and lose the drafts.
            return
        documents, selected = [], 0
        active = self.get_active_window()
        for window in windows:
            for doc in window.documents:
                entry = doc.session_entry()
                if entry is None:
                    continue
                if window is active and doc is window.current:
                    selected = len(documents)
                documents.append(entry)
        session.save_session(documents, selected)

    def _quit(self):
        # Przez close(), żeby każde okno zapytało o niezapisane zmiany (sam quit() to pomija).
        for window in list(self.get_windows()):
            window.close()

    def _new_window(self):
        window = Window(self)
        window.present()
        if not window.documents:
            window.new_document()
        return window

    # ---------- wygląd ----------

    def _preferences(self):
        from .preferences import PreferencesDialog

        PreferencesDialog().present(self.get_active_window())

    def _monospace_font(self):
        if not settings.get().get_boolean("use-system-font"):
            desc = Pango.FontDescription.from_string(settings.get().get_string("custom-font"))
            return desc.get_family() or "Monospace", (desc.get_size() / Pango.SCALE) or 11
        # Gio.Settings.new przy braku schematu przerywa proces, więc najpierw sprawdzamy, czy schemat jest.
        name = "Monospace 11"
        if settings.schema_exists("org.gnome.desktop.interface"):
            name = Gio.Settings.new("org.gnome.desktop.interface").get_string("monospace-font-name")
        desc = Pango.FontDescription.from_string(name or "Monospace 11")
        size = desc.get_size() / Pango.SCALE or 11
        return desc.get_family() or "Monospace", size

    def _apply_css(self):
        family, size = self._monospace_font()
        self._css.load_from_string(
            f'.verso-source {{ font-family: "{family}", monospace; font-size: {size * self.zoom:.1f}pt; }}\n'
            ".verso-search { padding: 6px 8px; border-bottom: 1px solid alpha(currentColor, 0.12); }\n"
            ".verso-statusbar { border-top: 1px solid alpha(currentColor, 0.1); padding: 1px 4px; }\n"
            ".verso-status-button > button { padding: 2px 8px; min-height: 22px; }"
        )

    def change_zoom(self, direction):
        if direction == 0:
            self.zoom = 1.0
        else:
            index = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - self.zoom))
            index = max(0, min(len(ZOOM_STEPS) - 1, index + direction))
            self.zoom = ZOOM_STEPS[index]
        settings.get().set_double("zoom", self.zoom)
        self._apply_css()
        for window in self.get_windows():
            if isinstance(window, Window):
                for doc in window.documents:
                    doc.set_zoom(self.zoom)
                window.toast(_("Zoom: {percent}%").format(percent=round(self.zoom * 100)))

    # ---------- okna informacyjne ----------

    def _about(self):
        dialog = Adw.AboutDialog(
            application_name="Verso",
            application_icon=APP_ID,
            developer_name="Maciej Zieliński",
            version=VERSION,
            comments=_("Text editor with live previews for Markdown, JSON and more"),
            license_type=Gtk.License.MIT_X11,
        )
        dialog.present(self.get_active_window())

    def _shortcuts(self):
        dialog = Adw.ShortcutsDialog()
        groups = (
            (_("Files"), ((_("Open"), "<Control>o"), (_("New Tab"), "<Control>t"), (_("New Window"), "<Control><Shift>n"),
                          (_("Save"), "<Control>s"), (_("Save As"), "<Control><Shift>s"), (_("Print"), "<Control>p"),
                          (_("Close Tab"), "<Control>w"))),
            (_("View"), ((_("Switch Between Edit and Preview"), "<Control>e"), (_("Show Outline"), "F9"),
                         (_("Zoom In"), "<Control>plus"),
                         (_("Zoom Out"), "<Control>minus"), (_("Reset Zoom"), "<Control>0"),
                         (_("Next Tab"), "<Control>Page_Down"), (_("Previous Tab"), "<Control>Page_Up"))),
            (_("Editing"), ((_("Find"), "<Control>f"), (_("Find and Replace"), "<Control>h"), (_("Go to Line"), "<Control>l"),
                            (_("Format JSON"), "<Control><Shift>i"), (_("Undo"), "<Control>z"), (_("Redo"), "<Control><Shift>z"))),
        )
        for title, items in groups:
            section = Adw.ShortcutsSection(title=title)
            for label, accel in items:
                section.add(Adw.ShortcutsItem(title=label, accelerator=accel))
            dialog.add(section)
        dialog.present(self.get_active_window())

    # ---------- zrzut ekranu do testów ----------

    def _maybe_screenshot(self, window):
        """VERSO_SCREENSHOT=plik.png: po chwili zapisuje widok okna i kończy (do weryfikacji)."""
        path = os.environ.get("VERSO_SCREENSHOT")
        if not path or getattr(self, "_shot_scheduled", False):
            return
        self._shot_scheduled = True
        mode = os.environ.get("VERSO_MODE")
        if mode:
            GLib.timeout_add(800, lambda: window.current and window.current.set_mode(mode) and False)
        action = os.environ.get("VERSO_ACTION")  # e.g. app.preferences: open a dialog before the screenshot
        if action:
            group, _sep, name = action.partition(".")
            target = self if group == "app" else window
            found = target.lookup_action(name)
            if found is None:
                print(f"VERSO_ACTION: no action {action!r}", file=sys.stderr)
            else:
                GLib.timeout_add(900, lambda: found.activate(None) and False)
        dark = os.environ.get("VERSO_DARK")  # 1: dark, 0: light, unset: follow the system
        if dark:
            scheme = Adw.ColorScheme.FORCE_LIGHT if dark == "0" else Adw.ColorScheme.FORCE_DARK
            Adw.StyleManager.get_default().set_color_scheme(scheme)

        def shoot():
            width, height = window.get_width(), window.get_height()
            paintable = Gtk.WidgetPaintable.new(window)
            snapshot = Gtk.Snapshot()
            paintable.snapshot(snapshot, width, height)
            node = snapshot.to_node()
            if node is not None:
                texture = window.get_renderer().render_texture(node, None)
                texture.save_to_png(path)
            self.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(int(os.environ.get("VERSO_DELAY", "2500")), shoot)


def main():
    fonts.setup()
    return Application().run(sys.argv)
