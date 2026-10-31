"""Preferences dialog. Every control is bound straight to GSettings, so changes apply immediately."""

from gettext import gettext as _

from gi.repository import Adw, Gio, GObject, Gtk, GtkSource, Pango

from . import settings
from .settings import MODES


def resolve_scheme(scheme_id, dark):
    """The light or dark variant of a style scheme, using the variant metadata that schemes ship."""
    manager = GtkSource.StyleSchemeManager.get_default()
    scheme = manager.get_scheme(scheme_id) or manager.get_scheme("Adwaita")
    if scheme is None:
        return None
    variant = scheme.get_metadata("variant")
    other = scheme.get_metadata("dark-variant" if dark else "light-variant")
    if other and variant == ("light" if dark else "dark"):
        return manager.get_scheme(other) or scheme
    return scheme


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self):
        super().__init__(title=_("Preferences"), search_enabled=True)
        self.prefs = settings.get()
        self.add(self._editor_page())
        self.add(self._behavior_page())
        self.add(self._preview_page())

    # ---------- helpers ----------

    def _switch(self, key, title, subtitle=None):
        row = Adw.SwitchRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        self.prefs.bind(key, row, "active", Gio.SettingsBindFlags.DEFAULT)
        return row

    def _spin(self, key, title, lower, upper):
        row = Adw.SpinRow.new_with_range(lower, upper, 1)
        row.set_title(title)
        self.prefs.bind(key, row, "value", Gio.SettingsBindFlags.DEFAULT)
        return row

    def _mode_row(self, key, title):
        labels = {"edit": _("Edit"), "split": _("Side by Side"), "preview": _("Preview")}
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new([labels[m] for m in MODES]))
        row.set_selected(MODES.index(self.prefs.get_string(key)))
        row.connect("notify::selected", lambda r, _p: self.prefs.set_string(key, MODES[r.get_selected()]))
        return row

    # ---------- pages ----------

    def _editor_page(self):
        page = Adw.PreferencesPage(title=_("Appearance"), icon_name="applications-graphics-symbolic")

        schemes = Adw.PreferencesGroup(title=_("Style"))
        self.scheme_box = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            column_spacing=12,
            row_spacing=12,
            min_children_per_line=2,
            max_children_per_line=4,
        )
        schemes.add(self.scheme_box)
        # Global objects outlive the dialog: disconnect when it closes, or closed dialogs stay alive.
        style = Adw.StyleManager.get_default()
        dark_handler = style.connect("notify::dark", lambda *_: self._fill_schemes())
        scheme_handler = self.prefs.connect("changed::style-scheme", lambda *_: self._mark_scheme())
        self.connect("closed", lambda *_: (style.disconnect(dark_handler), self.prefs.disconnect(scheme_handler)))
        self._fill_schemes()
        page.add(schemes)

        font = Adw.PreferencesGroup(title=_("Font"))
        use_system = self._switch("use-system-font", _("Use System Font"))
        font.add(use_system)
        font_row = Adw.ActionRow(title=_("Editor Font"))
        dialog = Gtk.FontDialog(title=_("Editor Font"))
        dialog.set_filter(self._monospace_filter())
        self.font_button = Gtk.FontDialogButton(dialog=dialog, valign=Gtk.Align.CENTER, level=Gtk.FontLevel.FONT)
        self.font_button.set_font_desc(Pango.FontDescription.from_string(self.prefs.get_string("custom-font")))
        self.font_button.connect("notify::font-desc", self._on_font_chosen)
        font_row.add_suffix(self.font_button)
        font_row.set_activatable_widget(self.font_button)
        use_system.bind_property("active", font_row, "sensitive", GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.INVERT_BOOLEAN)
        font.add(font_row)
        page.add(font)

        display = Adw.PreferencesGroup(title=_("Display"))
        display.add(self._switch("show-line-numbers", _("Line Numbers")))
        display.add(self._switch("highlight-current-line", _("Highlight Current Line")))
        display.add(self._switch("show-map", _("Overview Map"), _("A miniature of the document next to the scrollbar")))
        display.add(self._switch("show-whitespace", _("Show Spaces and Tabs")))
        margin = Adw.ExpanderRow(title=_("Right Margin"), show_enable_switch=True)
        self.prefs.bind("show-right-margin", margin, "enable-expansion", Gio.SettingsBindFlags.DEFAULT)
        margin.add_row(self._spin("right-margin-position", _("Margin Position"), 20, 250))
        display.add(margin)
        page.add(display)
        return page

    def _behavior_page(self):
        page = Adw.PreferencesPage(title=_("Behavior"), icon_name="document-edit-symbolic")

        indent = Adw.PreferencesGroup(title=_("Indentation"))
        indent.add(self._spin("tab-width", _("Tab Width"), 1, 16))
        indent.add(self._switch("insert-spaces", _("Insert Spaces Instead of Tabs")))
        indent.add(self._switch("auto-indent", _("Automatic Indentation")))
        page.add(indent)

        editing = Adw.PreferencesGroup(title=_("Editing"))
        editing.add(self._switch("auto-pair", _("Close Brackets and Quotes"), _("Typing ( [ { \" ' inserts the closing character")))
        editing.add(self._switch("wrap-lines", _("Wrap Lines")))
        editing.add(self._switch("spellcheck", _("Check Spelling")))
        page.add(editing)

        session = Adw.PreferencesGroup(title=_("Session"))
        session.add(self._switch("restore-session", _("Restore Session"), _("Reopen the documents from last time")))
        page.add(session)
        return page

    def _preview_page(self):
        page = Adw.PreferencesPage(title=_("Preview"), icon_name="view-reveal-symbolic")
        modes = Adw.PreferencesGroup(
            title=_("Default View"),
            description=_("How files open; Ctrl+E switches at any time"),
        )
        modes.add(self._mode_row("default-mode-markdown", _("Markdown")))
        modes.add(self._mode_row("default-mode-data", _("JSON and Data Files")))
        page.add(modes)
        return page

    # ---------- style schemes ----------

    def _fill_schemes(self):
        self.scheme_box.remove_all()
        dark = Adw.StyleManager.get_default().get_dark()
        manager = GtkSource.StyleSchemeManager.get_default()
        seen = set()
        for scheme_id in manager.get_scheme_ids():
            scheme = resolve_scheme(scheme_id, dark)
            if scheme is None or scheme.get_id() in seen:
                continue
            seen.add(scheme.get_id())
            preview = GtkSource.StyleSchemePreview.new(scheme)
            preview.connect("activate", self._on_scheme_activated)
            self.scheme_box.append(preview)
        self._mark_scheme()

    def _mark_scheme(self):
        dark = Adw.StyleManager.get_default().get_dark()
        current = resolve_scheme(self.prefs.get_string("style-scheme"), dark)
        child = self.scheme_box.get_first_child()
        while child is not None:
            preview = child.get_child()
            preview.set_selected(current is not None and preview.get_scheme().get_id() == current.get_id())
            child = child.get_next_sibling()

    def _on_scheme_activated(self, preview):
        self.prefs.set_string("style-scheme", preview.get_scheme().get_id())

    # ---------- font ----------

    @staticmethod
    def _monospace_filter():
        return Gtk.CustomFilter.new(lambda family: isinstance(family, Pango.FontFamily) and family.is_monospace())

    def _on_font_chosen(self, button, _pspec):
        desc = button.get_font_desc()
        if desc is not None:
            self.prefs.set_string("custom-font", desc.to_string())

