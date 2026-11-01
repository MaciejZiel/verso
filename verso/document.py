"""Jedna karta: edytor GtkSourceView, podgląd WebKit, pasek wyszukiwania i obsługa pliku."""

import gettext
import json
import re
from gettext import gettext as _
from gettext import ngettext

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
gi.require_version("WebKit", "6.0")
gi.require_version("Spelling", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, GtkSource, Spelling, WebKit  # noqa: E402

from . import fonts, md, mdedit, render, session, settings  # noqa: E402
from .outline import Outline  # noqa: E402
from .preferences import resolve_scheme  # noqa: E402
from .statusbar import StatusBar  # noqa: E402

PREVIEW_DELAY_MS = 250
OUTLINE_DELAY_MS = 300
AUTOSAVE_DELAY_MS = 1500

# Windows code pages first: they are supersets of the ISO ones for printable text, and files from
# Windows are far more common. GtkSourceView's own order makes cp1250 Polish text look broken.
_WINDOWS_FIRST = {"ISO-8859-1": "WINDOWS-1252", "ISO-8859-15": "WINDOWS-1252", "ISO-8859-2": "WINDOWS-1250",
                  "ISO-8859-5": "WINDOWS-1251", "ISO-8859-7": "WINDOWS-1253", "ISO-8859-9": "WINDOWS-1254"}


_EPHEMERAL = None


def _preview_session():
    """One shared ephemeral WebKit session: previews never store cookies, caches or site data on disk."""
    global _EPHEMERAL
    if _EPHEMERAL is None:
        # The previews use the default context; it starts no web process before the first preview.
        fonts.share_with_web_process(WebKit.WebContext.get_default())
        _EPHEMERAL = WebKit.NetworkSession.new_ephemeral()
    return _EPHEMERAL



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
        self._html_shown = False
        self._html_serial = 0
        self._html_scroll = 0
        self._async_features = False  # last Markdown preview had math or diagrams
        self._preview_timer = 0
        self._loading = False
        self._change_serial = 0  # grows with every buffer change; detects typing during a save
        self.source_file = GtkSource.File()
        self._save_encoding = None  # set when the user picks another encoding for saving
        self._save_newline = None
        self._indent_override = None  # (insert_spaces, width) chosen in the status bar for this document
        self.draft_id = None  # set while unsaved text is kept in the drafts folder
        self._autosave_timer = 0
        self._saving = False
        self._outline_timer = 0
        self._preview_line = 0.0  # top line of the preview, as reported by the page
        self._zoom = 1.0

        self._build_banner()
        self._build_search()
        self._build_editor()
        # The WebView (and its web process, tens of MB) is made only once the preview is shown:
        # tabs in the background and plain text files never start one.
        self.webview = None
        self.preview_box = Gtk.Box(hexpand=True, vexpand=True, visible=False)
        self._load_waiters = {}  # key → callback to run once the preview page has loaded
        self._load_handler = 0
        self.connect("map", self._on_map)

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True, hexpand=True)
        self.paned.set_start_child(self.editor_box)
        self.paned.set_end_child(self.preview_box)
        self.paned.set_resize_start_child(True)
        self.paned.set_resize_end_child(True)
        self.paned.set_shrink_start_child(False)
        self.paned.set_shrink_end_child(False)
        self.outline = Outline()
        self.outline.connect("heading-activated", lambda _o, line: self.go_to_heading(line))
        outline_keys = Gtk.EventControllerKey()
        outline_keys.connect("key-pressed", lambda _c, keyval, _code, _state: keyval == Gdk.KEY_Escape and self.focus_text())
        self.outline.add_controller(outline_keys)
        self.split_view = Adw.OverlaySplitView(
            sidebar=self.outline, content=self.paned, show_sidebar=False, min_sidebar_width=180, max_sidebar_width=280
        )
        self.append(self.split_view)
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
        self.source_scroller.get_vadjustment().connect("value-changed", self._on_editor_scroll)
        self._scroll_lock_until = 0  # monotonic µs; ignore scroll echoes from the other side until then
        # The overview map is created only when enabled: an existing but hidden GtkSource.Map
        # clears the editor's selection shortly after it is made (breaks find next/previous).
        self.map = None
        self.editor_box = Gtk.Box(hexpand=True, vexpand=True)
        self.editor_box.append(self.source_scroller)

        pairs = Gtk.EventControllerKey()
        pairs.connect("key-pressed", self._on_editor_key)
        self.view.add_controller(pairs)
        self.view.connect("paste-clipboard", self._on_paste)

        self.buffer.connect("modified-changed", self._on_modified_changed)
        self.buffer.connect("changed", self._on_buffer_changed)

        # Spell checking as in Text Editor (libspelling, system dictionaries). Loading the dictionaries
        # blocks for a quarter of a second, so the adapter is made once the tab is shown and its
        # preview, if any, has loaded (see _schedule_spelling).
        self.spelling = None
        self._spelling_idle = 0
        self._spelling_delay = 0

        self.search_settings = GtkSource.SearchSettings(wrap_around=True)
        self.search_context = GtkSource.SearchContext(buffer=self.buffer, settings=self.search_settings)
        self.search_context.connect("notify::occurrences-count", lambda *_: self._update_search_label())

    def _on_map(self, *_):
        if self.mode != "edit" and self.webview is None:
            self._ensure_preview()
            self._focus_main()  # set_mode could not focus the preview while it did not exist
        # Normally once the preview has loaded (_on_load_changed); this is the fallback.
        self._schedule_spelling(delay=0 if self.mode == "edit" else 2000)

    def _ensure_preview(self):
        if self.webview is not None:
            return
        content = WebKit.UserContentManager()
        content.register_script_message_handler("verso", None)
        content.connect("script-message-received::verso", self._on_preview_message)
        self.webview = WebKit.WebView(
            hexpand=True, vexpand=True, user_content_manager=content, network_session=_preview_session()
        )
        settings = self.webview.get_settings()
        settings.set_enable_developer_extras(False)
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_enable_back_forward_navigation_gestures(False)
        self.webview.connect("decide-policy", self._on_decide_policy)
        self.webview.connect("load-changed", self._on_load_changed)
        self.webview.set_zoom_level(self._zoom)
        self.preview_box.append(self.webview)
        self.webview.set_background_color(_rgba("#1e1e1e" if Adw.StyleManager.get_default().get_dark() else "#ffffff"))
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
        self.case_toggle.update_property([Gtk.AccessibleProperty.LABEL], [_("Match Case")])  # not "A a"
        self.case_toggle.connect("toggled", lambda b: self._set_case(b.get_active()))
        close_button = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "circular"], tooltip_text=_("Close Search (Esc)"))
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
        if self.file is None:
            return

        def reopen():
            self._save_encoding = None
            self.load(self.file, encoding)

        if not self.modified:
            reopen()
            return
        dialog = Adw.AlertDialog(
            heading=_("Discard Changes and Reopen?"),
            body=_("Reopening “{title}” with another encoding replaces your unsaved changes with the file on disk.").format(title=self.title),
        )
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("reopen", _("_Discard and Reopen"))
        dialog.set_response_appearance("reopen", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: response == "reopen" and reopen())
        dialog.present(self.get_root())

    def reload(self):
        if self.file is not None:
            self.load(self.file)

    def _set_kind_from_file(self):
        name = self.file.get_basename() if self.file else None
        self.kind = render.kind_of(name)
        manager = GtkSource.LanguageManager.get_default()
        language = manager.guess_language(name, None) if name else None
        if language is None and self.kind:
            # Names GtkSourceView does not recognise: .env, .jsonl, .jsonc, .editorconfig.
            fallback = {"markdown": "markdown", "json": "json", "jsonl": "json", "ini": "ini"}.get(self.kind)
            language = manager.get_language(fallback) if fallback else None
        self.buffer.set_language(language)
        self._update_spelling()
        self._update_outline()

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
        self._saving = True

        def done(saver, result, _data):
            self._saving = False
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
            # Text typed while saving did not reach the file, so the document stays modified (and keeps its draft).
            typed_meanwhile = self._change_serial != serial
            self.buffer.set_modified(typed_meanwhile)
            if typed_meanwhile:
                self.flush_draft()
            else:
                self.discard_draft()
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
        elif key in ("preview-theme", "preview-width"):
            self._apply_preview_options()
        elif key == "show-outline":
            self._update_outline()
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
        self._update_spelling()
        self._update_statusbar_visibility()
        self.statusbar.update()

    def set_indent_override(self, insert_spaces, width):
        self._indent_override = (insert_spaces, width)
        self._apply_editor_settings()

    def _update_statusbar_visibility(self):
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
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self._continue_markdown(state):
            return True
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

    # ---------- Markdown writing ----------

    def _continue_markdown(self, state):
        """Enter in a Markdown list or quote starts the next item; on an empty item it ends the list."""
        modifiers = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SHIFT_MASK
        if self.kind != "markdown" or state & modifiers or self.buffer.get_has_selection():
            return False
        buffer = self.buffer
        insert = buffer.get_iter_at_mark(buffer.get_insert())
        start = insert.copy()
        start.set_line_offset(0)
        end = insert.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        # Only the text before the cursor counts: Enter before or inside the marker is a plain newline.
        action, prefix = mdedit.continue_line(buffer.get_text(start, insert, True))
        if action == "end" and buffer.get_text(insert, end, True).strip():
            action = None
        if action is None:
            return False
        before = buffer.get_text(buffer.get_start_iter(), start, True).split("\n")[:-1]
        if mdedit.in_literal_block(before):
            return False
        buffer.begin_user_action()
        if action == "end":
            buffer.delete(start, end)
        else:
            buffer.insert_at_cursor("\n" + prefix)
        buffer.end_user_action()
        self.view.scroll_mark_onscreen(buffer.get_insert())
        return True

    def wrap_selection(self, marker):
        """Bold, italic or code: wraps (or unwraps) the selection or the word at the cursor;
        with neither, inserts the pair and puts the cursor inside."""
        if not self._writing_markdown():
            return
        buffer = self.buffer
        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            # Emphasis cannot start or end with a space: leave the selection's outer spaces out.
            while start.compare(end) < 0 and start.get_char().isspace():
                start.forward_char()
            while end.compare(start) > 0 and self._char_before(end).isspace():
                end.backward_char()
        else:
            start = buffer.get_iter_at_mark(buffer.get_insert())
            end = start.copy()
            if start.inside_word() or start.ends_word():
                if not start.starts_word():
                    start.backward_word_start()
                if not end.ends_word():
                    end.forward_word_end()
                # A word that already is bold or italic: take its markers too, so the toggle removes them.
                outer_start, outer_end = start.copy(), end.copy()
                outer_start.backward_chars(len(marker))
                outer_end.forward_chars(len(marker))
                if (buffer.get_text(outer_start, start, True) == marker
                        and buffer.get_text(end, outer_end, True) == marker):
                    start, end = outer_start, outer_end
        text = buffer.get_text(start, end, True)
        new = mdedit.toggle_wrap(text, marker)
        buffer.begin_user_action()
        offset = start.get_offset()
        buffer.delete(start, end)
        buffer.insert(buffer.get_iter_at_offset(offset), new)
        if text:
            buffer.select_range(buffer.get_iter_at_offset(offset), buffer.get_iter_at_offset(offset + len(new)))
        else:
            buffer.place_cursor(buffer.get_iter_at_offset(offset + len(marker)))
        buffer.end_user_action()
        self.view.grab_focus()

    @staticmethod
    def _char_before(it):
        previous = it.copy()
        return previous.get_char() if previous.backward_char() else ""

    def _writing_markdown(self):
        """The formatting shortcuts act only while the Markdown text itself has the focus
        (not in Preview mode, not in the find bar)."""
        return self.kind == "markdown" and self.mode != "preview" and self.view.is_focus()

    def insert_link(self):
        """[selection]() with the cursor in the parentheses, or []() with it in the brackets."""
        if not self._writing_markdown():
            return
        buffer = self.buffer
        text = ""
        buffer.begin_user_action()
        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            text = mdedit.escape_label(buffer.get_text(start, end, True))
            buffer.delete(start, end)
        offset = buffer.get_iter_at_mark(buffer.get_insert()).get_offset()
        buffer.insert_at_cursor(f"[{text}]()")
        buffer.place_cursor(buffer.get_iter_at_offset(offset + (len(text) + 3 if text else 1)))
        buffer.end_user_action()
        self.view.grab_focus()

    def _on_paste(self, view):
        """In Markdown: a URL pasted over selected text makes a link; an image is saved next to the file."""
        if self.kind != "markdown":
            return
        clipboard = view.get_clipboard()
        formats = clipboard.get_formats()
        # Other apps offer MIME types; content set in this process offers GTypes instead.
        mimes = formats.get_mime_types() or []
        gtypes = formats.get_gtypes() or []
        has_text = formats.contain_gtype(GObject.TYPE_STRING) or any(m.startswith("text/plain") for m in mimes)
        has_image = any(m.startswith("image/") for m in mimes) or any(t.is_a(Gdk.Texture) for t in gtypes)
        if self.buffer.get_has_selection() and has_text:
            view.stop_emission_by_name("paste-clipboard")
            clipboard.read_text_async(None, self._paste_text)
        elif has_image and not has_text:
            view.stop_emission_by_name("paste-clipboard")
            if self.file is None or self.file.get_parent() is None:
                self._toast(_("Save the document first to paste images."))
                return
            clipboard.read_texture_async(None, self._paste_image)

    def _paste_text(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            return
        if not text:
            return
        buffer = self.buffer
        buffer.begin_user_action()
        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            selected = buffer.get_text(start, end, True)
            buffer.delete(start, end)
            if mdedit.is_url(text) and not mdedit.is_url(selected) and "\n" not in selected:
                text = mdedit.link(selected, text)
        buffer.insert_at_cursor(text)
        buffer.end_user_action()
        self.view.scroll_mark_onscreen(buffer.get_insert())

    def _paste_image(self, clipboard, result):
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error as error:
            self._toast(_("Could not paste the image: {error}").format(error=error.message))
            return
        if texture is None or self.file is None or self.file.get_parent() is None:
            return
        folder = self.file.get_parent().get_child("images")
        # A name that needs no escaping in the link: letters, digits and dashes.
        stem = re.sub(r"[^\w-]+", "-", self.file.get_basename().rsplit(".", 1)[0]).strip("-") or "image"
        stamp = GLib.DateTime.new_now_local().format("%Y%m%d-%H%M%S")
        name = f"{stem}-{stamp}.png"
        number = 1
        while folder.get_child(name).query_exists(None):
            number += 1
            name = f"{stem}-{stamp}-{number}.png"
        try:
            folder.make_directory_with_parents(None)
        except GLib.Error as error:
            if not error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.EXISTS):
                self._toast(_("Could not paste the image: {error}").format(error=error.message))
                return
        # The link goes in right away, where the paste happened (replacing a selection); the file is
        # written in the background, through GIO so that remote folders work too.
        buffer = self.buffer
        buffer.begin_user_action()
        if buffer.get_has_selection():
            buffer.delete_selection(True, True)
        buffer.insert_at_cursor(f"![](images/{name})")
        buffer.end_user_action()

        def written(file, res):
            try:
                file.replace_contents_finish(res)
            except GLib.Error as error:
                self._toast(_("Could not paste the image: {error}").format(error=error.message))

        folder.get_child(name).replace_contents_bytes_async(
            texture.save_to_png_bytes(), None, False, Gio.FileCreateFlags.NONE, None, written
        )

    # ---------- widok ----------

    def set_mode(self, mode):
        if mode != "edit" and not self.kind:
            mode = "edit"
        self.mode = mode
        self.editor_box.set_visible(mode in ("edit", "split"))
        self._update_statusbar_visibility()
        self.preview_box.set_visible(mode in ("split", "preview"))
        if mode != "edit" and self.get_mapped():
            self._ensure_preview()
        if mode == "split":
            GLib.idle_add(self._center_paned)
            self._schedule_preview(immediate=True)
        elif mode == "preview":
            self._schedule_preview(immediate=True)
        self._focus_main()
        self._update_outline_current()
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
        self._zoom = factor
        if self.webview is not None:
            self.webview.set_zoom_level(factor)

    def _apply_theme(self):
        dark = Adw.StyleManager.get_default().get_dark()
        scheme = resolve_scheme(settings.get().get_string("style-scheme"), dark)
        if scheme is not None:
            self.buffer.set_style_scheme(scheme)
        if self.webview is not None:
            self.webview.set_background_color(_rgba("#1e1e1e" if dark else "#ffffff"))
        if self._preview_ready:
            self._run_js(f"window.verso.setDark({json.dumps(dark)})")

    def format_document(self):
        """Ctrl+Shift+I: formats JSON, or in Markdown the table at the cursor (all tables when the
        cursor is in none)."""
        if self.kind == "markdown":
            self.format_tables()
        else:
            self.format_json()

    def format_tables(self):
        buffer = self.buffer
        lines = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).split("\n")
        tables = mdedit.find_tables(lines)
        if not tables:
            self._toast(_("There is no table to format."))
            return
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        line, column = cursor.get_line(), cursor.get_line_offset()
        current = [(start, end) for start, end in tables if start <= line < end]
        changed = False
        buffer.begin_user_action()
        for start, end in reversed(current or tables):  # from the bottom, so earlier line numbers hold
            formatted = mdedit.format_table(lines[start:end])
            if formatted == lines[start:end]:
                continue
            changed = True
            _found, first = buffer.get_iter_at_line(start)
            _found, last = buffer.get_iter_at_line(end - 1)
            if not last.ends_line():
                last.forward_to_line_end()
            buffer.delete(first, last)
            buffer.insert(first, "\n".join(formatted))
        buffer.end_user_action()
        if changed:
            _found, it = buffer.get_iter_at_line(line)
            end = it.copy()
            if not end.ends_line():
                end.forward_to_line_end()
            it.set_line_offset(min(column, end.get_line_offset()))
            buffer.place_cursor(it)

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
        self._html_shown = False
        self.webview.get_settings().set_enable_javascript_markup(True)
        base = self.file.get_parent().get_uri() + "/" if self.file and self.file.get_parent() else "file:///"
        self._base_uri = base
        self.webview.load_html(render.build_page(), base)

    def _on_load_changed(self, _webview, event):
        if event != WebKit.LoadEvent.FINISHED:
            return
        self._preview_ready = True
        self._schedule_spelling()
        if self._html_shown:
            if self._html_scroll:
                self._run_js(f"window.scrollTo(0, {float(self._html_scroll)})")
            # An HTML file is shown as itself; reload only if it changed while this load ran.
            if self._change_serial != self._html_serial:
                self._schedule_preview()
            return
        self._apply_theme()
        self._apply_preview_options()
        self._schedule_preview(immediate=True)

    def _apply_preview_options(self):
        if self._preview_ready and not self._html_shown:
            prefs = settings.get()
            options = {
                "theme": prefs.get_string("preview-theme"),
                "width": prefs.get_string("preview-width"),
                "outline": self.outline_visible,  # the sidebar replaces the preview's own table of contents
            }
            self._run_js(f"window.verso.setOptions({json.dumps(options)})")

    def _show_html(self, text):
        """HTML files are rendered as real pages, with JavaScript off (opening a file must not run code)."""
        if self._html_shown and self._preview_ready:
            # Remember the scroll position first, then reload and restore it in _on_load_changed.
            self.webview.evaluate_javascript(
                "window.scrollY", -1, None, None, None, lambda w, r, _u: self._reload_html(text, w, r), None
            )
            self._preview_ready = False
            return
        self._reload_html(text)

    def _reload_html(self, text, webview=None, result=None):
        if result is not None:
            try:
                self._html_scroll = webview.evaluate_javascript_finish(result).to_double()
            except GLib.Error:
                self._html_scroll = 0
        self._preview_ready = False
        self._html_shown = True
        self._html_serial = self._change_serial
        self.webview.get_settings().set_enable_javascript_markup(False)  # page scripts off; our API calls still work
        base = self.file.get_parent().get_uri() + "/" if self.file and self.file.get_parent() else "file:///"
        self._base_uri = base
        self.webview.load_html(text, base)

    def _schedule_spelling(self, delay=0):
        """Makes the spell checker when the main loop is idle, after delay ms; an earlier request wins."""
        if self.spelling is not None:
            return
        if self._spelling_idle:
            if delay or self._spelling_delay == 0:
                return
            GLib.source_remove(self._spelling_idle)
        self._spelling_delay = delay
        if delay:
            self._spelling_idle = GLib.timeout_add(delay, self._create_spelling, priority=GLib.PRIORITY_LOW)
        else:
            self._spelling_idle = GLib.idle_add(self._create_spelling, priority=GLib.PRIORITY_LOW)

    def _create_spelling(self):
        self._spelling_idle = 0
        self.spelling = Spelling.TextBufferAdapter.new(self.buffer, Spelling.Checker.get_default())
        self.view.set_extra_menu(self.spelling.get_menu_model())
        self.view.insert_action_group("spelling", self.spelling)
        self._update_spelling()
        return GLib.SOURCE_REMOVE

    def _update_spelling(self):
        # JSON and code need no dictionary; Markdown and plain text do.
        if self.spelling is not None:
            self.spelling.set_enabled(self.kind not in render.DATA_KINDS and settings.get().get_boolean("spellcheck"))

    def _on_buffer_changed(self, *_):
        self._change_serial += 1
        if not self._loading:
            self._schedule_autosave()
            self._schedule_preview()
            self._schedule_outline()

    def _schedule_preview(self, immediate=False):
        if self._preview_timer:
            GLib.source_remove(self._preview_timer)
            self._preview_timer = 0
        if immediate:
            self._push_preview()
        else:
            self._preview_timer = GLib.timeout_add(PREVIEW_DELAY_MS, self._push_preview)

    def _push_preview(self, force=False):
        self._preview_timer = 0
        if not self._preview_ready or not self.kind or (self.mode == "edit" and not force):
            return GLib.SOURCE_REMOVE
        text = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)
        if self.kind == "html":
            self._show_html(text)
            return GLib.SOURCE_REMOVE
        # Relative images and links resolve against the file's folder; leaving HTML mode needs our page back.
        expected = self.file.get_parent().get_uri() + "/" if self.file and self.file.get_parent() else "file:///"
        if expected != self._base_uri or self._html_shown:
            self._load_page()
            return GLib.SOURCE_REMOVE
        name = self.file.get_basename() if self.file else ""
        payload = render.preview_payload(self.kind, text, name)
        self._async_features = any((payload.get("features") or {}).values())
        self._run_js(f"window.verso.update({json.dumps(payload, ensure_ascii=False)})")
        return GLib.SOURCE_REMOVE

    # ---------- printing and PDF ----------

    def print_document(self, export_file=None, on_done=None):
        """Prints (or, with export_file, writes a PDF). Files with a preview print the rendered preview."""
        window = self.get_root()
        if self.kind and not self._preview_ready and not self._html_shown:
            # The preview was never shown or its page is still loading: print once it has loaded.
            self._after_preview_loads("print", lambda: self.print_document(export_file, on_done))
            return
        if not (self.kind and self._preview_ready):
            self._print_source(window, export_file, on_done)
            return
        self._push_preview(force=True)  # the preview may be stale while the editor alone is shown
        self._when_preview_settled(lambda: self._print_preview(window, export_file, on_done))

    def _after_preview_loads(self, key, callback):
        """Makes the preview if needed and runs callback once its page has loaded. A second request
        with the same key while the first one waits is dropped (Ctrl+P pressed twice)."""
        if key in self._load_waiters:
            return
        self._load_waiters[key] = callback
        self._ensure_preview()
        if self._load_handler:
            return

        def loaded(_webview, event):
            if event == WebKit.LoadEvent.FINISHED:
                self.webview.disconnect(self._load_handler)
                self._load_handler = 0
                waiters, self._load_waiters = self._load_waiters, {}
                for waiter in waiters.values():
                    GLib.idle_add(lambda waiter=waiter: waiter() and False)

        self._load_handler = self.webview.connect("load-changed", loaded)

    def export_html(self, target, on_done):
        """Writes the rendered Markdown preview as a standalone HTML file; on_done(True/False)."""
        if self.kind != "markdown":
            on_done(False)
            return
        if not self._preview_ready:
            self._after_preview_loads("export-html", lambda: self.export_html(target, on_done))
            return
        self._push_preview(force=True)  # the preview may be stale while the editor alone is shown
        self._when_preview_settled(lambda: self._write_html(target, on_done))

    def _write_html(self, target, on_done):
        script = ("JSON.stringify({classes: document.documentElement.className,"
                  " content: (document.getElementById('content') || {}).outerHTML || '',"
                  " toc: (document.getElementById('toc') || {}).outerHTML || '',"
                  " math: !!document.querySelector('#content .katex')})")

        def written(file, result):
            try:
                file.replace_contents_finish(result)
            except GLib.Error:
                on_done(False)
                return
            on_done(True)

        def rendered(webview, result, _data):
            try:
                data = json.loads(webview.evaluate_javascript_finish(result).to_string())
            except (GLib.Error, ValueError):
                on_done(False)
                return
            page = render.export_page(self.title.rsplit(".", 1)[0], data["classes"], data["content"], data["toc"], data["math"])
            target.replace_contents_bytes_async(
                GLib.Bytes.new(page.encode("utf-8")), None, False, Gio.FileCreateFlags.REPLACE_DESTINATION, None, written
            )

        self.webview.evaluate_javascript(script, -1, None, None, None, rendered, None)

    def _when_preview_settled(self, callback):
        """Runs callback once the preview shows the current text: after an HTML reload has finished,
        and with extra time when math or diagrams render asynchronously."""
        if self._html_shown and not self._preview_ready:
            handler = None

            def finished(_webview, event):
                if event == WebKit.LoadEvent.FINISHED:
                    self.webview.disconnect(handler)
                    GLib.timeout_add(100, lambda: callback() and False)

            handler = self.webview.connect("load-changed", finished)
            return
        delay = 900 if self._async_features else 200
        GLib.timeout_add(delay, lambda: callback() and False)

    @staticmethod
    def _file_printer_name():
        # GTK's file backend names its printer with the translated "Print to File"; asking for it by
        # name avoids enumerating printers, which blocks until network printers have answered.
        name = "Print to File"  # GTK's own string, translated with GTK's catalog (not ours)
        return gettext.dgettext("gtk40", name)

    def _print_settings(self, export_file, printer=None):
        print_settings = Gtk.PrintSettings()
        print_settings.set(Gtk.PRINT_SETTINGS_OUTPUT_BASENAME, self.title.rsplit(".", 1)[0])
        if export_file is not None:
            if printer:
                print_settings.set_printer(printer)
            print_settings.set(Gtk.PRINT_SETTINGS_OUTPUT_FILE_FORMAT, "pdf")
            print_settings.set(Gtk.PRINT_SETTINGS_OUTPUT_URI, export_file.get_uri())
        return print_settings

    def _print_preview(self, window, export_file, on_done):
        operation = WebKit.PrintOperation.new(self.webview)
        if export_file is None:
            operation.set_print_settings(self._print_settings(None))
            operation.run_dialog(window)
            return
        operation.set_print_settings(self._print_settings(export_file, self._file_printer_name()))
        state = {"failed": False}

        def failed(_op, _error):
            # No usable file printer: let the print dialog do it ("Print to File" is offered there).
            state["failed"] = True
            dialog_operation = WebKit.PrintOperation.new(self.webview)
            dialog_operation.set_print_settings(self._print_settings(export_file))
            if on_done is not None:
                on_done(None)
            dialog_operation.run_dialog(window)

        def finished(_op):
            # WebKit emits "finished" after "failed" too; report success only when nothing failed.
            if not state["failed"] and on_done is not None:
                on_done(export_file.query_exists(None))

        operation.connect("failed", failed)
        operation.connect("finished", finished)
        operation.print_()

    def _print_source(self, window, export_file, on_done):
        compositor = GtkSource.PrintCompositor.new_from_view(self.view)
        compositor.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        compositor.set_print_line_numbers(1 if settings.get().get_boolean("show-line-numbers") else 0)
        compositor.set_print_header(True)
        compositor.set_header_format(True, self.title, None, _("Page %N of %Q"))
        operation = Gtk.PrintOperation(allow_async=True)
        operation.set_print_settings(self._print_settings(export_file))

        def begin(op, context):
            while not compositor.paginate(context):
                pass
            op.set_n_pages(compositor.get_n_pages())

        operation.connect("begin-print", begin)
        operation.connect("draw-page", lambda _op, context, page: compositor.draw_page(context, page))
        if on_done is not None:
            operation.connect("done", lambda _op, result: on_done(result == Gtk.PrintOperationResult.APPLY))
        if export_file is not None:
            operation.set_export_filename(export_file.get_path())
            operation.run(Gtk.PrintOperationAction.EXPORT, window)
        else:
            operation.run(Gtk.PrintOperationAction.PRINT_DIALOG, window)

    # ---------- synchronized scrolling (split view, Markdown) ----------

    def _sync_active(self):
        return self.mode == "split" and self.kind == "markdown" and settings.get().get_boolean("sync-scroll") and self._preview_ready

    def _lock_scroll(self):
        self._scroll_lock_until = GLib.get_monotonic_time() + 150_000

    def _on_editor_scroll(self, adjustment):
        self._update_outline_current()
        if not self._sync_active() or GLib.get_monotonic_time() < self._scroll_lock_until:
            return
        y = adjustment.get_value()
        it, _top = self.view.get_line_at_y(int(y))
        line_y, height = self.view.get_line_yrange(it)
        line = it.get_line() + (min(max((y - line_y) / height, 0.0), 1.0) if height else 0.0)
        if adjustment.get_value() + adjustment.get_page_size() >= adjustment.get_upper() - 1:
            line = -1  # at the very bottom: show the end of the preview too
        self._lock_scroll()
        self._run_js(f"window.verso.scrollToLine({line:.3f})")

    def _on_preview_message(self, _manager, value):
        try:
            message = json.loads(value.to_string())  # the page posts a JSON string
        except (TypeError, ValueError):
            return
        if not isinstance(message, dict) or message.get("type") != "scroll":
            return
        line = message.get("line", 0)
        if isinstance(line, (int, float)) and not isinstance(line, bool):
            self._preview_line = float(line)
            if self.mode == "preview":
                self._update_outline_current()
        if not self._sync_active():
            return
        if GLib.get_monotonic_time() < self._scroll_lock_until:
            return
        adjustment = self.source_scroller.get_vadjustment()
        if line < 0:
            target = adjustment.get_upper() - adjustment.get_page_size()
        else:
            whole = int(line)
            _found, it = self.buffer.get_iter_at_line(min(whole, self.buffer.get_line_count() - 1))
            line_y, height = self.view.get_line_yrange(it)
            target = line_y + (line - whole) * height
        self._lock_scroll()
        adjustment.set_value(max(0, target))

    # ---------- outline sidebar (Markdown) ----------

    @property
    def outline_visible(self):
        return self.kind == "markdown" and settings.get().get_boolean("show-outline")

    def _update_outline(self):
        visible = self.outline_visible
        self.split_view.set_show_sidebar(visible)
        if visible:
            self._refresh_outline()
        self._apply_preview_options()

    def _schedule_outline(self):
        if self._outline_timer or not self.outline_visible:
            return
        self._outline_timer = GLib.timeout_add(OUTLINE_DELAY_MS, self._refresh_outline)

    def _refresh_outline(self):
        self._outline_timer = 0
        if self.outline_visible:
            text = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)
            self.outline.set_entries(md.outline(text))
            self._update_outline_current()
        return GLib.SOURCE_REMOVE

    def _update_outline_current(self):
        """Highlights the section at the top of the editor, or of the preview when only the preview is shown."""
        if not self.outline_visible:
            return
        if self.mode == "preview":
            line = self.buffer.get_line_count() if self._preview_line < 0 else self._preview_line
        else:
            adjustment = self.source_scroller.get_vadjustment()
            it, _top = self.view.get_line_at_y(int(adjustment.get_value()) + 8)
            line = it.get_line()
            if adjustment.get_value() + adjustment.get_page_size() >= adjustment.get_upper() - 1:
                # At the end the last headings cannot reach the top: follow the cursor when it is in view.
                cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
                cursor_y, _height = self.view.get_line_yrange(cursor)
                if cursor_y >= adjustment.get_value():
                    line = max(line, cursor.get_line())
        self.outline.set_current_line(int(line))

    def focus_outline(self):
        """Keyboard access to the sidebar: the highlighted heading (or the first) takes the focus."""
        if self.outline_visible:
            row = self.outline.list.get_selected_row() or self.outline.list.get_row_at_index(0)
            if row is not None:
                row.grab_focus()

    def focus_text(self):
        self._focus_main()
        return True

    def _focus_main(self):
        (self.webview if self.mode == "preview" and self.webview is not None else self.view).grab_focus()

    def go_to_heading(self, line):
        if self.mode in ("edit", "split"):
            _found, it = self.buffer.get_iter_at_line(min(line, self.buffer.get_line_count() - 1))
            self.buffer.place_cursor(it)
            self.view.scroll_to_mark(self.buffer.get_insert(), 0.0, True, 0.0, 0.0)
            self.view.grab_focus()
        if self.mode == "preview" or (self.mode == "split" and not self._sync_active()):
            self._preview_line = float(line)  # the page does not report scrolls it was told to make
            self._run_js(f"window.verso.scrollToLine({int(line)})")
        if self.mode == "preview":
            self._focus_main()

    def _run_js(self, script):
        if self.webview is None:
            return
        self.webview.evaluate_javascript(script, -1, None, None, None, None, None)

    def _on_decide_policy(self, _webview, decision, decision_type):
        if decision_type not in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        action = decision.get_navigation_action()
        uri = action.get_request().get_uri()
        page = uri.split("#", 1)[0]
        if page in (self._base_uri, "about:blank") and ("#" in uri or not self._preview_ready):
            return False  # loading our page or the HTML file itself, or an anchor in it
        decision.ignore()
        if not action.is_user_gesture():
            return True  # iframes, redirects and meta refresh from a file are simply blocked
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
        if self.webview is not None:
            self.webview.get_find_controller().search_finish()
        self._focus_main()

    def _search_in_preview(self):
        return self.mode == "preview" and self.webview is not None

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
        if not self.buffer.get_modified() and not self._saving:
            self.discard_draft()  # reverted or reloaded: the draft is no longer needed (saves decide in done())
        self.emit("state-changed")

    def _schedule_autosave(self):
        if not settings.get().get_boolean("restore-session"):
            return  # drafts would be deleted unread at the next start
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
        session.write_draft_async(self.draft_id, text, self._on_draft_error)

    def _on_draft_error(self, error):
        self._toast(_("Could not keep a backup of unsaved changes: {error}").format(error=error))

    def discard_draft(self):
        if self.draft_id is not None:
            session.delete_draft(self.draft_id)
            self.draft_id = None

    def restore_draft(self, file, text, draft_id, encoding=None, newline=None, etag=None):
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
            # Compare with the file as it was when the draft was written: changes made on disk in
            # the meantime must be reported, not silently overwritten by the next save.
            current = self._query_etag(file)
            self._etag = etag or current
            if etag is not None and current != etag:
                self.banner.set_revealed(True)
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
            "etag": self._etag if self.file else None,
        }

    def shutdown(self):
        """Sprzątanie po zamknięciu karty: monitor pliku, timery i proces WebKit."""
        if self._autosave_timer:
            GLib.source_remove(self._autosave_timer)
            self._autosave_timer = 0
        if self._spelling_idle:
            GLib.source_remove(self._spelling_idle)
            self._spelling_idle = 0
        settings.get().disconnect(self._settings_handler)
        self.statusbar.shutdown()
        Adw.StyleManager.get_default().disconnect(self._style_handler)
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        if self._preview_timer:
            GLib.source_remove(self._preview_timer)
            self._preview_timer = 0
        if self._outline_timer:
            GLib.source_remove(self._outline_timer)
            self._outline_timer = 0
        if self.webview is not None:
            self.webview.terminate_web_process()

    # ---------- pomocnicze ----------

    def _toast(self, message):
        window = self.get_root()
        if hasattr(window, "toast"):
            window.toast(message)
