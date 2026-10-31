"""Jedna karta: edytor GtkSourceView, podgląd WebKit, pasek wyszukiwania i obsługa pliku."""

import json
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")
gi.require_version("WebKit", "6.0")
gi.require_version("Spelling", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, GtkSource, Spelling, WebKit  # noqa: E402

from . import render  # noqa: E402

MODES = ("edit", "split", "preview")
PREVIEW_DELAY_MS = 250


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

        self._build_banner()
        self._build_search()
        self._build_editor()
        self._build_preview()

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True, hexpand=True)
        self.paned.set_start_child(self.source_scroller)
        self.paned.set_end_child(self.webview)
        self.paned.set_resize_start_child(True)
        self.paned.set_resize_end_child(True)
        self.paned.set_shrink_start_child(False)
        self.paned.set_shrink_end_child(False)
        self.append(self.paned)

        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", lambda *_: self._apply_theme())
        self._apply_theme()
        self.set_mode("edit")

    # ---------- budowa ----------

    def _build_banner(self):
        self.banner = Adw.Banner(title="Plik zmienił się na dysku.", button_label="Wczytaj ponownie")
        self.banner.connect("button-clicked", lambda *_: self.reload())
        self.append(self.banner)

    def _build_editor(self):
        self.buffer = GtkSource.Buffer()
        self.buffer.set_highlight_matching_brackets(True)
        self.view = GtkSource.View(buffer=self.buffer)
        self.view.set_show_line_numbers(True)
        self.view.set_highlight_current_line(True)
        self.view.set_auto_indent(True)
        self.view.set_smart_backspace(True)
        self.view.set_tab_width(4)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.set_top_margin(10)
        self.view.set_bottom_margin(120)
        self.view.set_left_margin(6)
        self.view.set_right_margin(12)
        self.view.add_css_class("podglad-source")

        self.source_scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.source_scroller.set_child(self.view)

        self.buffer.connect("modified-changed", lambda *_: self.emit("state-changed"))
        self.buffer.connect("changed", self._on_buffer_changed)

        # Sprawdzanie pisowni jak w Edytorze tekstu (libspelling, słownik z systemu).
        self.spelling = Spelling.TextBufferAdapter.new(self.buffer, Spelling.Checker.get_default())
        self.view.set_extra_menu(self.spelling.get_menu_model())
        self.view.insert_action_group("spelling", self.spelling)
        self.spelling.set_enabled(True)

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
        self.search_entry = Gtk.SearchEntry(hexpand=True, placeholder_text="Znajdź")
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda *_: self.find_next())
        self.search_entry.connect("stop-search", lambda *_: self.hide_search())
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_search_key)
        self.search_entry.add_controller(key)

        self.search_label = Gtk.Label(css_classes=["dim-label", "numeric"], width_chars=7)
        prev_button = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Poprzednie (Shift+Enter)")
        prev_button.connect("clicked", lambda *_: self.find_prev())
        next_button = Gtk.Button(icon_name="go-down-symbolic", tooltip_text="Następne (Enter)")
        next_button.connect("clicked", lambda *_: self.find_next())
        nav = Gtk.Box(css_classes=["linked"])
        nav.append(prev_button)
        nav.append(next_button)

        self.replace_toggle = Gtk.ToggleButton(icon_name="edit-find-replace-symbolic", tooltip_text="Zamień (Ctrl+H)")
        self.case_toggle = Gtk.ToggleButton(label="Aa", tooltip_text="Uwzględniaj wielkość liter")
        self.case_toggle.connect("toggled", lambda b: self._set_case(b.get_active()))
        close_button = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "circular"])
        close_button.connect("clicked", lambda *_: self.hide_search())

        row = Gtk.Box(spacing=6)
        for widget in (self.search_entry, self.search_label, nav, self.case_toggle, self.replace_toggle, close_button):
            row.append(widget)

        self.replace_entry = Gtk.Entry(hexpand=True, placeholder_text="Zamień na")
        self.replace_entry.connect("activate", lambda *_: self.replace_one())
        replace_button = Gtk.Button(label="Zamień")
        replace_button.connect("clicked", lambda *_: self.replace_one())
        replace_all_button = Gtk.Button(label="Zamień wszystkie")
        replace_all_button.connect("clicked", lambda *_: self.replace_all())
        replace_row = Gtk.Box(spacing=6)
        for widget in (self.replace_entry, replace_button, replace_all_button):
            replace_row.append(widget)
        self.replace_revealer = Gtk.Revealer(child=replace_row)
        self.replace_toggle.bind_property("active", self.replace_revealer, "reveal-child", GObject.BindingFlags.SYNC_CREATE)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["podglad-search"])
        box.append(row)
        box.append(self.replace_revealer)
        self.search_revealer = Gtk.Revealer(child=box, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.append(self.search_revealer)

    # ---------- plik ----------

    @property
    def title(self):
        if self.file is None:
            return f"Nowy dokument {self.untitled_number}" if self.untitled_number > 1 else "Nowy dokument"
        return self.file.get_basename()

    @property
    def subtitle(self):
        if self.file is None:
            return "Szkic"
        parent = self.file.get_parent()
        path = parent.get_path() if parent else None
        if not path:
            return self.file.get_uri()
        home = str(Path.home())
        return "~" + path[len(home) :] if path == home or path.startswith(home + "/") else path

    @property
    def modified(self):
        return self.buffer.get_modified()

    @property
    def is_blank(self):
        return self.file is None and not self.modified and self.buffer.get_char_count() == 0

    def load(self, file):
        self.file = file
        self._loading = True
        self.emit("state-changed")
        file.load_contents_async(None, self._on_loaded, None)

    def _on_loaded(self, file, result, _data):
        try:
            _ok, contents, etag = file.load_contents_finish(result)
        except GLib.Error as error:
            self._loading = False
            self._toast(f"Nie udało się otworzyć pliku: {error.message}")
            return
        text = self._decode(bytes(contents))
        self._etag = etag
        offset = self.buffer.get_property("cursor-position")
        self.buffer.begin_irreversible_action()
        self.buffer.set_text(text)
        self.buffer.end_irreversible_action()
        self.buffer.set_modified(False)
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(min(offset, self.buffer.get_char_count())))
        self._loading = False
        self.banner.set_revealed(False)
        first_load = self.kind is None and self._monitor is None
        self._set_kind_from_file()
        if first_load:
            self.set_mode("preview" if self.kind else "edit")
            self.buffer.place_cursor(self.buffer.get_start_iter())
        self._watch_file()
        self._schedule_preview(immediate=True)
        self.emit("state-changed")

    @staticmethod
    def _decode(data):
        for encoding in ("utf-8", "utf-16"):
            try:
                text = data.decode(encoding)
                return text[1:] if text.startswith("﻿") else text
            except UnicodeDecodeError:
                continue
        return data.decode("cp1250", errors="replace")

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
        self.spelling.set_enabled(self.kind != "json")

    def save(self, file=None, callback=None):
        target = file or self.file
        if target is None:
            return
        text = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)
        data = GLib.Bytes.new(text.encode("utf-8"))

        def done(f, result, _data):
            try:
                _ok, etag = f.replace_contents_finish(result)
            except GLib.Error as error:
                self._toast(f"Nie udało się zapisać: {error.message}")
                if callback:
                    callback(False)
                return
            self._etag = etag
            renamed = file is not None and (self.file is None or not file.equal(self.file))
            self.file = target
            self.buffer.set_modified(False)
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

        target.replace_contents_bytes_async(data, None, False, Gio.FileCreateFlags.NONE, None, done, None)

    def _watch_file(self):
        if self._monitor is not None:
            self._monitor.cancel()
        try:
            self._monitor = self.file.monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error:
            self._monitor = None
            return
        self._monitor.connect("changed", self._on_file_changed)

    def _on_file_changed(self, _monitor, _file, _other, event):
        if event not in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED):
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

    # ---------- widok ----------

    def set_mode(self, mode):
        if mode != "edit" and not self.kind:
            mode = "edit"
        self.mode = mode
        self.source_scroller.set_visible(mode in ("edit", "split"))
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
        scheme = GtkSource.StyleSchemeManager.get_default().get_scheme("Adwaita-dark" if dark else "Adwaita")
        if scheme is not None:
            self.buffer.set_style_scheme(scheme)
        self.webview.set_background_color(_rgba("#1e1e1e" if dark else "#ffffff"))
        if self._preview_ready:
            self._run_js(f"window.podglad.setDark({json.dumps(dark)})")

    def format_json(self):
        if self.kind != "json":
            self._toast("Formatowanie działa tylko dla plików JSON.")
            return
        text = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)
        formatted = render.format_json(text)
        if formatted is None:
            _data, error = render.parse_json(text)
            self._toast(f"Niepoprawny JSON. {error}")
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
        self._run_js(f"window.podglad.update({json.dumps(payload, ensure_ascii=False)})")
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
        self.search_label.set_text(f"{max(position, 0)} z {total}" if total else "Brak")

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
            self._toast(f"Zamieniono: {count}")

    # ---------- pomocnicze ----------

    def _toast(self, message):
        window = self.get_root()
        if hasattr(window, "toast"):
            window.toast(message)
