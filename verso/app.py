"""Aplikacja: jedna instancja (D-Bus), skróty, powiększenie wspólne dla okien."""

import os
from gettext import gettext as _
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import settings  # noqa: E402
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
    "win.zoom-in": ["<Control>plus", "<Control>equal", "<Control>KP_Add"],
    "win.zoom-out": ["<Control>minus", "<Control>KP_Subtract"],
    "win.zoom-reset": ["<Control>0", "<Control>KP_0"],
    "win.next-tab": ["<Control>Page_Down", "<Control>Tab"],
    "win.prev-tab": ["<Control>Page_Up", "<Control><Shift>Tab"],
    "app.quit": ["<Control>q"],
    "app.shortcuts": ["<Control>question"],
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
        window = self.get_active_window() or self._new_window()
        for file in files:
            window.open_file(file)
        window.present()
        self._maybe_screenshot(window)

    def _restore_session(self, window):
        prefs = settings.get()
        if not prefs.get_boolean("restore-session"):
            return
        uris = prefs.get_strv("session-uris")
        files = [Gio.File.new_for_uri(uri) for uri in uris]
        files = [f for f in files if f.query_exists(None)]
        for file in files:
            window.open_file(file)
        if files:
            index = min(max(prefs.get_int("session-selected"), 0), window.tab_view.get_n_pages() - 1)
            window.tab_view.set_selected_page(window.tab_view.get_nth_page(index))

    def save_session(self, window):
        """Called by the last window that closes: remembers its documents for the next start."""
        uris, selected = [], 0
        for doc in window.documents:
            if doc.file is not None:
                if doc is window.current:
                    selected = len(uris)
                uris.append(doc.file.get_uri())
        prefs = settings.get()
        prefs.set_strv("session-uris", uris)
        prefs.set_int("session-selected", selected)

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

    def _monospace_font(self):
        # Gio.Settings.new przy braku schematu przerywa proces, więc najpierw sprawdzamy, czy schemat jest.
        name = "Monospace 11"
        source = Gio.SettingsSchemaSource.get_default()
        if source is not None and source.lookup("org.gnome.desktop.interface", True) is not None:
            name = Gio.Settings.new("org.gnome.desktop.interface").get_string("monospace-font-name")
        desc = Pango.FontDescription.from_string(name or "Monospace 11")
        size = desc.get_size() / Pango.SCALE or 11
        return desc.get_family() or "Monospace", size

    def _apply_css(self):
        family, size = self._monospace_font()
        self._css.load_from_string(
            f'.verso-source {{ font-family: "{family}", monospace; font-size: {size * self.zoom:.1f}pt; }}\n'
            ".verso-search { padding: 6px 8px; border-bottom: 1px solid alpha(currentColor, 0.12); }"
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
                          (_("Save"), "<Control>s"), (_("Save As"), "<Control><Shift>s"), (_("Close Tab"), "<Control>w"))),
            (_("View"), ((_("Switch Between Edit and Preview"), "<Control>e"), (_("Zoom In"), "<Control>plus"),
                         (_("Zoom Out"), "<Control>minus"), (_("Reset Zoom"), "<Control>0"),
                         (_("Next Tab"), "<Control>Page_Down"), (_("Previous Tab"), "<Control>Page_Up"))),
            (_("Editing"), ((_("Find"), "<Control>f"), (_("Find and Replace"), "<Control>h"),
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
        if os.environ.get("VERSO_DARK"):
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

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
    return Application().run(sys.argv)
