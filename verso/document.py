"""Jedna karta: edytor GtkSourceView, podgląd WebKit, pasek wyszukiwania i obsługa pliku."""

import json
from gettext import gettext as _
from gettext import ngettext

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
gi.require_version("WebKit", "6.0")
gi.require_version("Spelling", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, GtkSource, Spelling, WebKit  # noqa: E402

from . import render, session, settings  # noqa: E402
from .preferences import resolve_scheme  # noqa: E402
from .statusbar import StatusBar  # noqa: E402

PREVIEW_DELAY_MS = 250
AUTOSAVE_DELAY_MS = 1500

# Windows code pages first: they are supersets of the ISO ones for printable text, and files from
# Windows are far more common. GtkSourceView's own order makes cp1250 Polish text look broken.
_WINDOWS_FIRST = {"ISO-8859-1": "WINDOWS-1252", "ISO-8859-15": "WINDOWS-1252", "ISO-8859-2": "WINDOWS-1250",
                  "ISO-8859-5": "WINDOWS-1251", "ISO-8859-7": "WINDOWS-1253", "ISO-8859-9": "WINDOWS-1254"}


def candidate_encodings():
    result, seen = [GtkSource.Encoding.get_utf8()], {"UTF-8"}
    for encoding in GtkSource.Encoding.get_default_candidates():
        charsets = [_WINDOWS_FIRST.get(encoding.get_charset()), encoding.get_charset()]
        for charset in charsets:
            if charset and charset not in seen:
                candidate = GtkSource.Encoding.get_from_charset(charset)
                if candidate is not None:
                    result.append(candidate)
                    seen.add(charset)
    return result


def _rgba(spec):
    color = Gdk.RGBA()
    color.parse(spec)
    return color


class Document(Gtk.Box):
    __gsignals__ = {
        # Zmiana tytułu, ścieżki, stanu „zmodyfikowany” albo trybu widoku.
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Link w podglądzie do innego pliku .md/.json: otwórz go w nowej karcie.
        "open-file": (GObject.SignalFlags.RUN_FIRST, None, (Gio.File,)),
    }

    _untitled_counter = 0

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        Document._untitled_counter += 1
        self.untitled_number = Document._untitled_counter
        self.file = None
        self.kind = None
        self.mode = "edit"
        self._etag = None
        self._monitor = None
        self._preview_ready = False
        self._preview_timer = 0
        self._loading = False
        self._change_serial = 0  # grows with every buffer change; detects typing during a save
        self.source_file = GtkSource.File()
        self._save_encoding = None  # set when the user picks another encoding for saving
        self._save_newline = None
        self._indent_override = None  # (insert_spaces, width) chosen in the status bar for this document
        self.draft_id = None  # set while unsaved text is kept in the drafts folder
        self._autosave_timer = 0

        self._build_banner()
        self._build_search()
        self._build_editor()
        self._build_preview()

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True, hexpand=True)
        self.paned.set_start_child(self.editor_box)
        self.paned.set_end_child(self.webview)
        self.paned.set_resize_start_child(True)
        self.paned.set_resize_end_child(True)
        self.paned.set_shrink_start_child(False)
        self.paned.set_shrink_end_child(False)
        self.append(self.paned)
        self.statusbar = StatusBar(self)
        self.append(self.statusbar)

        style = Adw.StyleManager.get_default()
        self._style_handler = style.connect("notify::dark", lambda *_: self._apply_theme())
        self._settings_handler = settings.get().connect("changed", self._on_setting_changed)
        self._apply_editor_settings()
        self._apply_theme()
        self.set_mode("edit")

    # ---------- budowa ----------

    def _build_banner(self):
        self.banner = Adw.Banner(title=_("The file has changed on disk."), button_label=_("_Reload"))
        self.banner.connect("button-clicked", lambda *_: self.reload())
        self.append(self.banner)

    def _build_editor(self):
        self.buffer = GtkSource.Buffer()
        self.buffer.set_highlight_matching_brackets(True)
        self.view = GtkSource.View(buffer=self.buffer)
        self.view.set_smart_backspace(True)
        self.view.set_top_margin(10)
        self.view.set_bottom_margin(120)
        self.view.set_left_margin(6)
        self.view.set_right_margin(12)
        self.view.add_css_class("verso-source")

        self.source_scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.source_scroller.set_child(self.view)
        # The overview map is created only when enabled: an existing but hidden GtkSource.Map
        # clears the editor's selection shortly after it is made (breaks find next/previous).
        self.map = None
        self.editor_box = Gtk.Box(hexpand=True, vexpand=True)
        self.editor_box.append(self.source_scroller)

        pairs = Gtk.EventControllerKey()
        pairs.connect("key-pressed", self._on_editor_key)
        self.view.add_controller(pairs)

        self.buffer.connect("modified-changed", self._on_modified_changed)
        self.buffer.connect("changed", self._on_buffer_changed)

        # Sprawdzanie pisowni jak w Edytorze tekstu (libspelling, słownik z systemu).
        self.spelling = Spelling.TextBufferAdapter.new(self.buffer, Spelling.Checker.get_default())
        self.view.set_extra_menu(self.spelling.get_menu_model())
        self.view.insert_action_group("spelling", self.spelling)

        self.search_settings = GtkSource.SearchSettings(wrap_around=True)
        self.search_context = GtkSource.SearchContext(buffer=self.buffer, settings=self.search_settings)
        self.search_context.connect("notify::occurrences-count", lambda *_: self._update_search_label())

    def _build_preview(self):
        self.webview = WebKit.WebView(hexpand=True, vexpand=True)
        settings = self.webview.get_settings()
        settings.set_enable_developer_extras(False)
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_enable_back_forward_navigation_gestures(False)
        self.webview.connect("decide-policy", self._on_decide_policy)
        self.webview.connect("load-changed", self._on_load_changed)
        self._load_page()

    def _build_search(self):
        self.search_entry = Gtk.SearchEntry(hexpand=True, placeholder_text=_("Find"))
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda *_: self.find_next())
        self.search_entry.connect("stop-search", lambda *_: self.hide_search())
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_search_key)
        self.search_entry.add_controller(key)

        self.search_label = Gtk.Label(css_classes=["dim-label", "numeric"], width_chars=7)
        prev_button = Gtk.Button(icon_name="go-up-symbolic", tooltip_text=_("Previous Match (Shift+Enter)"))
        prev_button.connect("clicked", lambda *_: self.find_prev())
        next_button = Gtk.Button(icon_name="go-down-symbolic", tooltip_text=_("Next Match (Enter)"))
        next_button.connect("clicked", lambda *_: self.find_next())
        nav = Gtk.Box(css_classes=["linked"])
        nav.append(prev_button)
        nav.append(next_button)

        self.replace_toggle = Gtk.ToggleButton(icon_name="edit-find-replace-symbolic", tooltip_text=_("Replace (Ctrl+H)"))
        self.case_toggle = Gtk.ToggleButton(label="Aa", tooltip_text=_("Match Case"))
        self.case_toggle.connect("toggled", lambda b: self._set_case(b.get_active()))
        close_button = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "circular"])
        close_button.connect("clicked", lambda *_: self.hide_search())

        row = Gtk.Box(spacing=6)
        for widget in (self.search_entry, self.search_label, nav, self.case_toggle, self.replace_toggle, close_button):
            row.append(widget)

        self.replace_entry = Gtk.Entry(hexpand=True, placeholder_text=_("Replace with"))
        self.replace_entry.connect("activate", lambda *_: self.replace_one())
        replace_button = Gtk.Button(label=_("Replace"))
        replace_button.connect("clicked", lambda *_: self.replace_one())
        replace_all_button = Gtk.Button(label=_("Replace All"))
        replace_all_button.connect("clicked", lambda *_: self.replace_all())
        replace_row = Gtk.Box(spacing=6)
        for widget in (self.replace_entry, replace_button, replace_all_button):
            replace_row.append(widget)
        self.replace_revealer = Gtk.Revealer(child=replace_row)
        self.replace_toggle.bind_property("active", self.replace_revealer, "reveal-child", GObject.BindingFlags.SYNC_CREATE)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["verso-search"])
        box.append(row)
        box.append(self.replace_revealer)
        self.search_revealer = Gtk.Revealer(child=box, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.append(self.search_revealer)

    # ---------- plik ----------

    @property
    def title(self):
        if self.file is None:
            return _("New Document {number}").format(number=self.untitled_number) if self.untitled_number > 1 else _("New Document")
        return self.file.get_basename()

    @property
    def subtitle(self):
        if self.file is None:
            return _("Draft")
        parent = self.file.get_parent()
        path = parent.get_path() if parent else None
        if not path:
            return self.file.get_uri()
        return render.shorten_home(path)

    @property
    def modified(self):
        return self.buffer.get_modified()

    @property
    def is_blank(self):
        return self.file is None and not self.modified and self.buffer.get_char_count() == 0

    def load(self, file, encoding=None):
        """Reads the file through GtkSource.FileLoader: detects encoding, line endings and compression."""
        previous = self.file
        self.file = file
        self._loading = True
        self.emit("state-changed")
        self.source_file.set_location(file)
        loader = GtkSource.FileLoader.new(self.buffer, self.source_file)
        loader.set_candidate_encodings([encoding] if encoding else candidate_encodings())
        offset = self.buffer.get_property("cursor-position")
        loader.load_async(GLib.PRIORITY_DEFAULT, None, None, None, self._on_loaded, (previous, offset))

    def _on_loaded(self, loader, result, data):
        previous, offset = data
        file = loader.get_location()
        try:
            loader.load_finish(result)
        except GLib.Error as error:
            if error.matches(GtkSource.FileLoaderError.quark(), GtkSource.FileLoaderError.CONVERSION_FALLBACK):
                # Loaded, but some bytes did not fit the encoding and were replaced.
                self._toast(_("Some characters could not be read correctly. Try reopening the file with another encoding."))
            else:
                self._loading = False
                if previous is None or not previous.equal(file):
                    # An empty buffer must not stay attached to a real file: saving would wipe it.
                    self.file = previous
                    self.source_file.set_location(previous)
                    self.emit("state-changed")
                self._toast(_("Could not open the file: {error}").format(error=error.message))
                return
        self._etag = self._query_etag(file)
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(min(offset, self.buffer.get_char_count())))
        self._loading = False
        self.banner.set_revealed(False)
        first_load = self.kind is None and self._monitor is None
        self._set_kind_from_file()
        if first_load:
            self.set_mode(self._default_mode())
            self.buffer.place_cursor(self.buffer.get_start_iter())
        self._watch_file()
        self._schedule_preview(immediate=True)
        self.emit("state-changed")

    @staticmethod
    def _query_etag(file):
        try:
            return file.query_info("etag::value", Gio.FileQueryInfoFlags.NONE, None).get_etag()
        except GLib.Error:
            return None

    @property
    def encoding(self):
        return self._save_encoding or self.source_file.get_encoding() or GtkSource.Encoding.get_utf8()

    @property
    def newline_type(self):
        if self._save_newline is not None:
            return self._save_newline
        return self.source_file.get_newline_type() if self.file else GtkSource.NewlineType.LF

    def set_save_encoding(self, encoding):
        """Encoding for the next save (the document counts as changed until then)."""
        if encoding.get_charset() != self.encoding.get_charset():
            self._save_encoding = encoding
            self.buffer.set_modified(True)
            self.emit("state-changed")

    def set_newline_type(self, newline):
        if newline != self.newline_type:
            self._save_newline = newline
            self.buffer.set_modified(True)
            self.emit("state-changed")

    def reopen_with_encoding(self, encoding):
        if self.file is not None:
            self._save_encoding = None
            self.load(self.file, encoding)

    def reload(self):
        if self.file is not None:
            self.load(self.file)

    def _set_kind_from_file(self):
        name = self.file.get_basename() if self.file else None
        self.kind = render.kind_of(name)
        manager = GtkSource.LanguageManager.get_default()
        language = manager.guess_language(name, None) if name else None
        if language is None and self.kind == "markdown":
            language = manager.get_language("markdown")
        self.buffer.set_language(language)
        # JSON i kod nie potrzebują słownika; Markdown i zwykły tekst tak.
        self.spelling.set_enabled(self.kind != "json" and settings.get().get_boolean("spellcheck"))

    def save(self, file=None, callback=None):
        """Writes through GtkSource.FileSaver, keeping the file's encoding and line endings unless changed."""
        target = file or self.file
        if target is None:
            return
        if self.file is not None and target.equal(self.file):
            saver = GtkSource.FileSaver.new(self.buffer, self.source_file)
        else:
            saver = GtkSource.FileSaver.new_with_target(self.buffer, self.source_file, target)
        saver.set_encoding(self.encoding)
        saver.set_newline_type(self.newline_type)
        # Changes on disk are reported by the banner; saving after it means "overwrite".
        saver.set_flags(GtkSource.FileSaverFlags.IGNORE_MODIFICATION_TIME)
        serial = self._change_serial
        renamed = self.file is None or not target.equal(self.file)

        def done(saver, result, _data):
            try:
                saver.save_finish(result)
            except GLib.Error as error:
                self._toast(_("Could not save the file: {error}").format(error=error.message))
                if callback:
                    callback(False)
                return
            self._etag = self._query_etag(target)
            self._save_encoding = None
            self._save_newline = None
            self.file = target
            # Text typed while saving did not reach the file, so the document stays modified.
            self.buffer.set_modified(self._change_serial != serial)
            self.banner.set_revealed(False)
            if renamed:
                old_kind = self.kind
                self._set_kind_from_file()
                if self.kind != old_kind:
                    self.set_mode("split" if self.kind else "edit")
                self._watch_file()
            Gtk.RecentManager.get_default().add_item(target.get_uri())
            self.emit("state-changed")
            if callback:
                callback(True)

        saver.save_async(GLib.PRIORITY_DEFAULT, None, None, None, done, None)

    def _watch_file(self):
        if self._monitor is not None:
            self._monitor.cancel()
        try:
            self._monitor = self.file.monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error:
            self._monitor = None
            return
        self._monitor.connect("changed", self._on_file_changed)

    def _on_file_changed(self, _monitor, _file, other, event):
        events = Gio.FileMonitorEvent
        if event == events.RENAMED:
            # Zapis atomowy (plik tymczasowy przemianowany na nasz) zmienia treść; przemianowanie naszego pliku nie.
            if other is None or not other.equal(self.file):
                return
        elif event not in (events.CHANGES_DONE_HINT, events.CREATED, events.MOVED_IN):
            return
        try:
            info = self.file.query_info("etag::value", Gio.FileQueryInfoFlags.NONE, None)
        except GLib.Error:
            return
        if info.get_etag() == self._etag or self._loading:
            return  # nasz własny zapis
        if self.modified:
            self.banner.set_revealed(True)
        else:
            self.reload()

    # ---------- ustawienia ----------

    EDITOR_KEYS = {
        "show-line-numbers", "highlight-current-line", "show-map", "show-whitespace", "show-right-margin",
        "right-margin-position", "tab-width", "insert-spaces", "auto-indent", "wrap-lines", "spellcheck",
        "show-statusbar",
    }

    def _on_setting_changed(self, _settings, key):
        if key == "style-scheme":
            self._apply_theme()
        elif key in self.EDITOR_KEYS:
            self._apply_editor_settings()

    def _apply_editor_settings(self):
        prefs = settings.get()
        view = self.view
        view.set_show_line_numbers(prefs.get_boolean("show-line-numbers"))
        view.set_highlight_current_line(prefs.get_boolean("highlight-current-line"))
        view.set_show_right_margin(prefs.get_boolean("show-right-margin"))
        view.set_right_margin_position(prefs.get_uint("right-margin-position"))
        spaces, width = self._indent_override or (prefs.get_boolean("insert-spaces"), prefs.get_uint("tab-width"))
        view.set_tab_width(width)
        view.set_indent_width(-1)  # = tab width
        view.set_insert_spaces_instead_of_tabs(spaces)
        view.set_auto_indent(prefs.get_boolean("auto-indent"))
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if prefs.get_boolean("wrap-lines") else Gtk.WrapMode.NONE)
        if prefs.get_boolean("show-map"):
            if self.map is None:
                self.map = GtkSource.Map(view=self.view, vexpand=True, focusable=False)
                self.editor_box.append(self.map)
        elif self.map is not None:
            self.editor_box.remove(self.map)
            self.map = None
        drawer = view.get_space_drawer()
        drawer.set_types_for_locations(GtkSource.SpaceLocationFlags.ALL, GtkSource.SpaceTypeFlags.NONE)
        if prefs.get_boolean("show-whitespace"):
            drawer.set_types_for_locations(
                GtkSource.SpaceLocationFlags.ALL,
                GtkSource.SpaceTypeFlags.SPACE | GtkSource.SpaceTypeFlags.TAB | GtkSource.SpaceTypeFlags.NBSP,
            )
        drawer.set_enable_matrix(prefs.get_boolean("show-whitespace"))
        self.spelling.set_enabled(self.kind != "json" and prefs.get_boolean("spellcheck"))
        self._update_statusbar_visibility()
        if hasattr(self, "statusbar"):
            self.statusbar.update()

    def set_indent_override(self, insert_spaces, width):
        self._indent_override = (insert_spaces, width)
        self._apply_editor_settings()

    def _update_statusbar_visibility(self):
        if hasattr(self, "statusbar"):
            self.statusbar.set_visible(settings.get().get_boolean("show-statusbar") and self.mode != "preview")

    def show_goto(self):
        if self.mode == "preview":
            self.set_mode("edit")
        self.statusbar.show_goto()

    def _default_mode(self):
        if not self.kind:
            return "edit"
        key = "default-mode-markdown" if self.kind == "markdown" else "default-mode-data"
        return settings.get().get_string(key)

    # Automatic closing of brackets and quotes, and typing "over" a closing character.
    PAIRS = {"(": ")", "[": "]", "{": "}", '"': '"', "'": "'", "`": "`"}

    def _on_editor_key(self, _controller, keyval, _code, state):
        if not settings.get().get_boolean("auto-pair"):
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        char = chr(Gdk.keyval_to_unicode(keyval)) if Gdk.keyval_to_unicode(keyval) else ""
        buffer = self.buffer
        insert = buffer.get_iter_at_mark(buffer.get_insert())
        following = insert.get_char()

        if keyval == Gdk.KEY_BackSpace and not buffer.get_has_selection():
            before = insert.copy()
            if before.backward_char() and self.PAIRS.get(before.get_char()) == following and following:
                end = insert.copy()
                end.forward_char()
                buffer.begin_user_action()
                buffer.delete(before, end)
                buffer.end_user_action()
                return True
            return False

        if not char:
            return False
        if char in self.PAIRS.values() and following == char and not buffer.get_has_selection():
            # Already there: just step over it.
            insert.forward_char()
            buffer.place_cursor(insert)
            return True
        if char not in self.PAIRS:
            return False
        closing = self.PAIRS[char]
        if char in "\"'`" and not buffer.get_has_selection():
            # Quotes only pair at word boundaries, so apostrophes inside words stay single.
            previous = insert.copy()
            if previous.backward_char() and (previous.get_char().isalnum() or previous.get_char() == char):
                return False
            if following and following.isalnum():
                return False
        buffer.begin_user_action()
        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            text = buffer.get_text(start, end, True)
            buffer.delete(start, end)
            buffer.insert_at_cursor(char + text + closing)
        else:
            buffer.insert_at_cursor(char + closing)
            cursor = buffer.get_iter_at_mark(buffer.get_insert())
            cursor.backward_char()
            buffer.place_cursor(cursor)
        buffer.end_user_action()
        return True

    # ---------- widok ----------

    def set_mode(self, mode):
        if mode != "edit" and not self.kind:
            mode = "edit"
        self.mode = mode
        self.editor_box.set_visible(mode in ("edit", "split"))
        self._update_statusbar_visibility()
        self.webview.set_visible(mode in ("split", "preview"))
        if mode == "split":
            GLib.idle_add(self._center_paned)
            self._schedule_preview(immediate=True)
        elif mode == "preview":
            self._schedule_preview(immediate=True)
        if mode == "preview":
            self.webview.grab_focus()
        else:
            self.view.grab_focus()
        self.emit("state-changed")

    def _center_paned(self):
        width = self.paned.get_width()
        if width > 0:
            self.paned.set_position(width // 2)
        return GLib.SOURCE_REMOVE

    def cycle_mode(self):
        if not self.kind:
            return
        self.set_mode("preview" if self.mode == "edit" else "edit")

    def set_zoom(self, factor):
        self.webview.set_zoom_level(factor)

    def _apply_theme(self):
        dark = Adw.StyleManager.get_default().get_dark()
        scheme = resolve_scheme(settings.get().get_string("style-scheme"), dark)
        if scheme is not None:
            self.buffer.set_style_scheme(scheme)
        self.webview.set_background_color(_rgba("#1e1e1e" if dark else "#ffffff"))
        if self._preview_ready:
            self._run_js(f"window.verso.setDark({json.dumps(dark)})")

    def format_json(self):
        if self.kind != "json":
            self._toast(_("Formatting is available only for JSON files."))
            return
        text = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)
        formatted = render.format_json(text)
        if formatted is None:
            _data, error = render.parse_json(text)
            self._toast(_("Invalid JSON. {error}").format(error=error))
            return
        if formatted == text:
            return
        # set_text jest nieodwracalne; usunięcie i wstawienie w jednej akcji cofa się jednym Ctrl+Z.
        self.buffer.begin_user_action()
        self.buffer.delete(self.buffer.get_start_iter(), self.buffer.get_end_iter())
        self.buffer.insert(self.buffer.get_start_iter(), formatted)
        self.buffer.end_user_action()
        self.buffer.place_cursor(self.buffer.get_start_iter())

    # ---------- podgląd ----------

    def _load_page(self):
        self._preview_ready = False
        base = self.file.get_parent().get_uri() + "/" if self.file and self.file.get_parent() else "file:///"
        self._base_uri = base
        self.webview.load_html(render.build_page(), base)

    def _on_load_changed(self, _webview, event):
        if event == WebKit.LoadEvent.FINISHED:
            self._preview_ready = True
            self._apply_theme()
            self._schedule_preview(immediate=True)

    def _on_buffer_changed(self, *_):
        self._change_serial += 1
        if not self._loading:
            self._schedule_autosave()
        if not self._loading:
            self._schedule_preview()

    def _schedule_preview(self, immediate=False):
        if self._preview_timer:
            GLib.source_remove(self._preview_timer)
            self._preview_timer = 0
        if immediate:
            self._push_preview()
        else:
            self._preview_timer = GLib.timeout_add(PREVIEW_DELAY_MS, self._push_preview)

    def _push_preview(self):
        self._preview_timer = 0
        if not self._preview_ready or not self.kind or self.mode == "edit":
            return GLib.SOURCE_REMOVE
        # Względne obrazki i linki mają się odnosić do katalogu pliku.
        expected = self.file.get_parent().get_uri() + "/" if self.file and self.file.get_parent() else "file:///"
        if expected != self._base_uri:
            self._load_page()
            return GLib.SOURCE_REMOVE
        text = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)
        payload = render.preview_payload(self.kind, text)
        self._run_js(f"window.verso.update({json.dumps(payload, ensure_ascii=False)})")
        return GLib.SOURCE_REMOVE

    def _run_js(self, script):
        self.webview.evaluate_javascript(script, -1, None, None, None, None, None)

    def _on_decide_policy(self, _webview, decision, decision_type):
        if decision_type not in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        uri = decision.get_navigation_action().get_request().get_uri()
        if not self._preview_ready:
            return False  # pierwsze wczytanie naszej strony
        if uri.split("#", 1)[0] in (self._base_uri, "about:blank") and "#" in uri:
            return False  # kotwica w tym samym dokumencie
        decision.ignore()
        target = Gio.File.new_for_uri(uri.split("#", 1)[0])
        if uri.startswith("file:") and render.kind_of(target.get_basename()):
            self.emit("open-file", target)
        else:
            Gtk.UriLauncher.new(uri).launch(self.get_root(), None, None, None)
        return True

    # ---------- wyszukiwanie ----------

    def show_search(self, replace=False):
        selection = self.buffer.get_selection_bounds()
        if selection and self.mode != "preview":
            start, end = selection
            text = self.buffer.get_text(start, end, False)
            if "\n" not in text:
                self.search_entry.set_text(text)
        self.replace_toggle.set_visible(self.mode != "preview")
        self.replace_toggle.set_active(replace and self.mode != "preview")
        self.search_revealer.set_reveal_child(True)
        # hide_search czyści wyszukiwanie, a tekst w polu zostaje: przywróć je.
        if self.search_entry.get_text():
            self._on_search_changed(self.search_entry)
        self.search_entry.grab_focus()
        self.search_entry.select_region(0, -1)

    def hide_search(self):
        self.search_revealer.set_reveal_child(False)
        self.search_settings.set_search_text(None)
        self.webview.get_find_controller().search_finish()
        (self.webview if self.mode == "preview" else self.view).grab_focus()

    def _search_in_preview(self):
        return self.mode == "preview"

    def _on_search_changed(self, entry):
        text = entry.get_text()
        if self._search_in_preview():
            controller = self.webview.get_find_controller()
            if text:
                options = WebKit.FindOptions.WRAP_AROUND
                if not self.case_toggle.get_active():
                    options |= WebKit.FindOptions.CASE_INSENSITIVE
                controller.search(text, options, 10000)
            else:
                controller.search_finish()
            self.search_label.set_text("")
            return
        self.search_settings.set_search_text(text or None)
        if text:
            self._select_match(forward=True, from_selection_start=True)
        self._update_search_label()

    def _set_case(self, active):
        self.search_settings.set_case_sensitive(active)
        self._on_search_changed(self.search_entry)

    def _on_search_key(self, _controller, keyval, _code, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.SHIFT_MASK:
            self.find_prev()
            return True
        return False

    def _select_match(self, forward, from_selection_start=False):
        bounds = self.buffer.get_selection_bounds()
        if bounds:
            start, end = bounds
            anchor = start if (from_selection_start or not forward) else end
        else:
            anchor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        if forward:
            found, match_start, match_end, _wrapped = self.search_context.forward(anchor)
        else:
            found, match_start, match_end, _wrapped = self.search_context.backward(anchor)
        if found:
            self.buffer.select_range(match_start, match_end)
            self.view.scroll_to_iter(match_start, 0.2, False, 0, 0)
        self._update_search_label()

    def find_next(self):
        if self._search_in_preview():
            self.webview.get_find_controller().search_next()
        else:
            self._select_match(forward=True)

    def find_prev(self):
        if self._search_in_preview():
            self.webview.get_find_controller().search_previous()
        else:
            self._select_match(forward=False)

    def _update_search_label(self):
        if self._search_in_preview() or not self.search_entry.get_text():
            self.search_label.set_text("")
            return
        total = self.search_context.get_occurrences_count()
        if total < 0:
            return  # jeszcze liczy
        bounds = self.buffer.get_selection_bounds()
        position = self.search_context.get_occurrence_position(*bounds) if bounds else 0
        self.search_label.set_text(_("{position} of {total}").format(position=max(position, 0), total=total) if total else _("No matches"))

    def replace_one(self):
        bounds = self.buffer.get_selection_bounds()
        replacement = self.replace_entry.get_text()
        if bounds and self.search_context.get_occurrence_position(*bounds) > 0:
            start, end = bounds
            self.search_context.replace(start, end, replacement, -1)
        self._select_match(forward=True)

    def replace_all(self):
        if self.search_entry.get_text():
            count = self.search_context.replace_all(self.replace_entry.get_text(), -1)
            self._toast(ngettext("Replaced {count} match", "Replaced {count} matches", count).format(count=count))

    # ---------- drafts ----------

    def _on_modified_changed(self, *_):
        if not self.buffer.get_modified():
            self.discard_draft()  # saved or reverted: the draft is no longer needed
        self.emit("state-changed")

    def _schedule_autosave(self):
        if self._autosave_timer:
            GLib.source_remove(self._autosave_timer)
        self._autosave_timer = GLib.timeout_add(AUTOSAVE_DELAY_MS, self._autosave)

    def _autosave(self):
        self._autosave_timer = 0
        self.flush_draft()
        return GLib.SOURCE_REMOVE

    def flush_draft(self):
        """Writes the unsaved text to the drafts folder right now (or removes a draft that is not needed)."""
        if self._autosave_timer:
            GLib.source_remove(self._autosave_timer)
            self._autosave_timer = 0
        if not self.modified:
            self.discard_draft()
            return
        if self.draft_id is None:
            self.draft_id = session.new_draft_id()
            self.emit("state-changed")  # the session has to learn about the new draft
        text = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)
        try:
            session.write_draft(self.draft_id, text)
        except OSError as error:
            self._toast(_("Could not keep a backup of unsaved changes: {error}").format(error=error.strerror))

    def discard_draft(self):
        if self.draft_id is not None:
            session.delete_draft(self.draft_id)
            self.draft_id = None

    def restore_draft(self, file, text, draft_id, encoding=None, newline=None):
        """Brings back a document from the last session together with its unsaved text."""
        self.file = file
        if file is not None:
            self.source_file.set_location(file)
        if encoding:
            self._save_encoding = GtkSource.Encoding.get_from_charset(encoding)
        if newline in ("LF", "CR_LF", "CR"):
            self._save_newline = getattr(GtkSource.NewlineType, newline)
        self._loading = True
        self.buffer.begin_irreversible_action()
        self.buffer.set_text(text)
        self.buffer.end_irreversible_action()
        self._loading = False
        self.draft_id = draft_id
        self.buffer.set_modified(True)
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self._set_kind_from_file()
        self.set_mode(self._default_mode())
        if file is not None:
            self._etag = self._query_etag(file)
            self._watch_file()
        self._schedule_preview(immediate=True)
        self.emit("state-changed")

    def session_entry(self):
        """What session.json needs to bring this document back; None for an empty, untouched draft."""
        if self.file is None and not self.modified:
            return None
        if self.modified and self.draft_id is None:
            self.flush_draft()
        newline = {GtkSource.NewlineType.LF: "LF", GtkSource.NewlineType.CR_LF: "CR_LF", GtkSource.NewlineType.CR: "CR"}[self.newline_type]
        return {
            "uri": self.file.get_uri() if self.file else None,
            "draft": self.draft_id if self.modified else None,
            "encoding": self.encoding.get_charset(),
            "newline": newline,
        }

    def shutdown(self):
        """Sprzątanie po zamknięciu karty: monitor pliku, timery i proces WebKit."""
        if self._autosave_timer:
            GLib.source_remove(self._autosave_timer)
            self._autosave_timer = 0
        settings.get().disconnect(self._settings_handler)
        self.statusbar.shutdown()
        Adw.StyleManager.get_default().disconnect(self._style_handler)
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        if self._preview_timer:
            GLib.source_remove(self._preview_timer)
            self._preview_timer = 0
        self.webview.terminate_web_process()

    # ---------- pomocnicze ----------

    def _toast(self, message):
        window = self.get_root()
        if hasattr(window, "toast"):
            window.toast(message)
