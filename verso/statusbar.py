"""Status bar under the editor: word count, cursor position (go to line), language, indentation,
encoding and line endings. Every item is a button that changes the setting for this document."""

import re
from gettext import gettext as _
from gettext import ngettext

from gi.repository import Gio, GLib, Gtk, GtkSource

from . import render

COUNT_DELAY_MS = 300
COUNT_LIMIT = 2_000_000  # characters; above this the word count would slow typing down
_WORD = re.compile(r"\w+(?:[-'’]\w+)*")

# Encodings offered in the menus; the full list would be overwhelming for most people.
COMMON_ENCODINGS = (
    "UTF-8", "UTF-16", "WINDOWS-1250", "ISO-8859-2", "WINDOWS-1252", "ISO-8859-1", "ISO-8859-15",
    "WINDOWS-1251", "KOI8-R", "WINDOWS-1253", "WINDOWS-1254", "SHIFT_JIS", "EUC-JP", "GB18030", "BIG5", "EUC-KR",
)
NEWLINES = (
    (GtkSource.NewlineType.LF, "LF", lambda: _("Unix and macOS (LF)")),
    (GtkSource.NewlineType.CR_LF, "CRLF", lambda: _("Windows (CRLF)")),
    (GtkSource.NewlineType.CR, "CR", lambda: _("Classic Mac OS (CR)")),
)


def count_words(text):
    return len(_WORD.findall(text))


def _flat_button(tooltip):
    button = Gtk.MenuButton(css_classes=["flat", "verso-status-button"], tooltip_text=tooltip, always_show_arrow=False)
    button.set_child(Gtk.Label(css_classes=["caption", "numeric"]))
    return button


def _set_label(button, text):
    button.get_child().set_text(text)


class StatusBar(Gtk.Box):
    def __init__(self, document):
        super().__init__(css_classes=["verso-statusbar"], spacing=2)
        self.doc = document
        self._count_timer = 0

        self.words = Gtk.Label(css_classes=["caption", "dim-label", "numeric"], margin_start=10, xalign=0)
        self.append(self.words)
        self.append(Gtk.Box(hexpand=True))

        self.position = _flat_button(_("Go to Line (Ctrl+L)"))
        self.position.set_popover(self._goto_popover())
        self.language = _flat_button(_("Syntax Highlighting"))
        # Built on first use: ~150 rows per document would slow down restoring many tabs.
        self.language.set_create_popup_func(lambda button: button.get_popover() or button.set_popover(self._language_popover()))
        self.indent = _flat_button(_("Indentation"))
        self.indent.set_popover(self._indent_popover())
        self.encoding = _flat_button(_("Encoding"))
        self.encoding.set_menu_model(self._encoding_menu())
        self.newline = _flat_button(_("Line Endings"))
        self.newline.set_menu_model(self._newline_menu())
        for widget in (self.position, self.language, self.indent, self.encoding, self.newline):
            self.append(widget)

        self._install_actions()
        buffer = document.buffer
        buffer.connect("notify::cursor-position", lambda *_: self._update_position())
        self._had_selection = False
        buffer.connect("mark-set", self._on_mark_set)
        buffer.connect("changed", lambda *_: self._schedule_count())
        buffer.connect("notify::language", lambda *_: self.update())
        document.connect("state-changed", lambda *_: self.update())
        self.update()

    # ---------- updates ----------

    def update(self):
        self._update_position()
        language = self.doc.buffer.get_language()
        _set_label(self.language, language.get_name() if language else _("Plain Text"))
        view = self.doc.view
        if view.get_insert_spaces_instead_of_tabs():
            label = _("Spaces: {width}").format(width=view.get_tab_width())
        else:
            label = _("Tab Width: {width}").format(width=view.get_tab_width())
        _set_label(self.indent, label)
        _set_label(self.encoding, self.doc.encoding.get_charset())
        newline = self.doc.newline_type
        _set_label(self.newline, next(short for kind, short, _label in NEWLINES if kind == newline))
        self.newline_action.set_state(GLib.Variant.new_string(next(short for kind, short, _l in NEWLINES if kind == newline)))
        self._schedule_count()

    def _update_position(self):
        buffer = self.doc.buffer
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        line = cursor.get_line() + 1
        column = self.doc.view.get_visual_column(cursor) + 1
        _set_label(self.position, _("Ln {line}, Col {column}").format(line=line, column=column))

    def _on_mark_set(self, buffer, _iter, mark):
        # Plain cursor movement does not change the word count; only selections do.
        if mark.get_name() in ("insert", "selection_bound"):
            has_selection = buffer.get_has_selection()
            if has_selection or self._had_selection:
                self._schedule_count()
            self._had_selection = has_selection

    def _schedule_count(self, immediate=False):
        if self._count_timer:
            GLib.source_remove(self._count_timer)
            self._count_timer = 0
        if immediate:
            self._update_count()
        else:
            self._count_timer = GLib.timeout_add(COUNT_DELAY_MS, self._update_count)

    def _update_count(self):
        self._count_timer = 0
        buffer = self.doc.buffer
        if buffer.get_char_count() > COUNT_LIMIT or self.doc.kind in render.DATA_KINDS:
            self.words.set_text("")
            return GLib.SOURCE_REMOVE
        bounds = buffer.get_selection_bounds()
        if bounds:
            selected = count_words(buffer.get_text(*bounds, False))
            chars = bounds[1].get_offset() - bounds[0].get_offset()
            text = ngettext("{count} word selected", "{count} words selected", selected).format(count=selected)
            text += " · " + ngettext("{count} character", "{count} characters", chars).format(count=chars)
        else:
            total = count_words(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False))
            text = ngettext("{count} word", "{count} words", total).format(count=total)
        self.words.set_text(text)
        return GLib.SOURCE_REMOVE

    def shutdown(self):
        if self._count_timer:
            GLib.source_remove(self._count_timer)
            self._count_timer = 0

    # ---------- go to line ----------

    def _goto_popover(self):
        self.goto_entry = Gtk.Entry(placeholder_text=_("Line or line:column"), width_chars=18, input_purpose=Gtk.InputPurpose.DIGITS)
        self.goto_entry.connect("activate", self._on_goto)
        box = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        box.append(self.goto_entry)
        go = Gtk.Button(label=_("Go"), css_classes=["suggested-action"])
        go.connect("clicked", self._on_goto)
        box.append(go)
        popover = Gtk.Popover(child=box)
        popover.connect("show", lambda *_: (self.goto_entry.set_text(""), self.goto_entry.grab_focus()))
        return popover

    def show_goto(self):
        if not self.get_visible():
            # Ctrl+L works with the status bar turned off: show it just while the popover is open.
            self.set_visible(True)
            popover = self.position.get_popover()
            handler = None

            def restore(*_):
                popover.disconnect(handler)
                self.doc._update_statusbar_visibility()

            handler = popover.connect("closed", restore)
        self.position.popup()

    def _on_goto(self, *_):
        line, column = parse_goto(self.goto_entry.get_text(), self.doc.buffer.get_line_count())
        if line is None:
            self.goto_entry.add_css_class("error")
            return
        self.goto_entry.remove_css_class("error")
        self.position.popdown()
        buffer = self.doc.buffer
        _found, target = buffer.get_iter_at_line(line - 1)
        if column:
            # Columns count visually (tabs expand), like the status label.
            while not target.ends_line() and self.doc.view.get_visual_column(target) < column - 1:
                target.forward_char()
        buffer.place_cursor(target)
        self.doc.view.scroll_to_iter(target, 0.25, True, 0, 0.5)
        self.doc.view.grab_focus()

    # ---------- language ----------

    def _language_popover(self):
        self.language_search = Gtk.SearchEntry(placeholder_text=_("Search Languages"))
        self.language_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["navigation-sidebar"])
        manager = GtkSource.LanguageManager.get_default()
        languages = [manager.get_language(i) for i in manager.get_language_ids()]
        languages = sorted((lang for lang in languages if lang and not lang.get_hidden()), key=lambda l: l.get_name().lower())
        for language in [None, *languages]:
            row = Gtk.ListBoxRow()
            row.set_child(Gtk.Label(label=language.get_name() if language else _("Plain Text"), xalign=0, margin_start=6))
            row.language = language
            row.search_text = (f"{language.get_name()} {language.get_id()} {language.get_section()}" if language else _("Plain Text")).lower()
            self.language_list.append(row)
        self.language_list.set_filter_func(
            lambda row: self.language_search.get_text().lower() in row.search_text
        )
        self.language_search.connect("search-changed", lambda *_: self.language_list.invalidate_filter())
        self.language_search.connect("activate", lambda *_: self._pick_first_language())
        self.language_list.connect("row-activated", lambda _l, row: self._set_language(row.language))
        scroller = Gtk.ScrolledWindow(min_content_height=320, min_content_width=240, child=self.language_list)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self.language_search)
        box.append(scroller)
        popover = Gtk.Popover(child=box)
        popover.connect("show", lambda *_: (self.language_search.set_text(""), self.language_search.grab_focus()))
        return popover

    def _pick_first_language(self):
        child = self.language_list.get_first_child()
        while child is not None:
            if child.get_child_visible():
                self._set_language(child.language)
                return
            child = child.get_next_sibling()

    def _set_language(self, language):
        self.language.popdown()
        self.doc.buffer.set_language(language)

    # ---------- indentation ----------

    def _indent_popover(self):
        self.spaces_toggle = Gtk.CheckButton(label=_("Spaces"))
        self.tabs_toggle = Gtk.CheckButton(label=_("Tabs"), group=self.spaces_toggle)
        self.width_spin = Gtk.SpinButton.new_with_range(1, 16, 1)
        width_row = Gtk.Box(spacing=12)
        width_row.append(Gtk.Label(label=_("Width"), hexpand=True, xalign=0))
        width_row.append(self.width_spin)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        for widget in (self.spaces_toggle, self.tabs_toggle, Gtk.Separator(), width_row):
            box.append(widget)
        popover = Gtk.Popover(child=box)
        popover.connect("show", self._sync_indent_popover)
        self.spaces_toggle.connect("toggled", self._on_indent_changed)
        self.width_spin.connect("value-changed", self._on_indent_changed)
        return popover

    def _sync_indent_popover(self, *_):
        self._syncing = True
        view = self.doc.view
        (self.spaces_toggle if view.get_insert_spaces_instead_of_tabs() else self.tabs_toggle).set_active(True)
        self.width_spin.set_value(view.get_tab_width())
        self._syncing = False

    def _on_indent_changed(self, *_):
        if getattr(self, "_syncing", False):
            return
        self.doc.set_indent_override(self.spaces_toggle.get_active(), int(self.width_spin.get_value()))
        self.update()

    # ---------- encoding and line endings ----------

    def _install_actions(self):
        group = Gio.SimpleActionGroup()
        save_as = Gio.SimpleAction.new("save-encoding", GLib.VariantType.new("s"))
        save_as.connect("activate", lambda _a, value: self.doc.set_save_encoding(GtkSource.Encoding.get_from_charset(value.get_string())))
        reopen = Gio.SimpleAction.new("reopen-encoding", GLib.VariantType.new("s"))
        reopen.connect("activate", lambda _a, value: self.doc.reopen_with_encoding(GtkSource.Encoding.get_from_charset(value.get_string())))
        self.newline_action = Gio.SimpleAction.new_stateful("newline", GLib.VariantType.new("s"), GLib.Variant.new_string("LF"))
        self.newline_action.connect("change-state", self._on_newline)
        for action in (save_as, reopen, self.newline_action):
            group.add_action(action)
        self.insert_action_group("status", group)

    def _encoding_menu(self):
        menu = Gio.Menu()
        names = {e.get_charset(): e for e in GtkSource.Encoding.get_all()}
        for title, action in ((_("Save With Encoding"), "status.save-encoding"), (_("Reopen With Encoding"), "status.reopen-encoding")):
            section = Gio.Menu()
            for charset in COMMON_ENCODINGS:
                encoding = names.get(charset)
                if encoding is None:
                    continue
                item = Gio.MenuItem.new(f"{encoding.get_name()} ({charset})", None)
                item.set_action_and_target_value(action, GLib.Variant.new_string(charset))
                section.append_item(item)
            menu.append_section(title, section)
        return menu

    def _newline_menu(self):
        menu = Gio.Menu()
        for _kind, short, label in NEWLINES:
            item = Gio.MenuItem.new(label(), None)
            item.set_action_and_target_value("status.newline", GLib.Variant.new_string(short))
            menu.append_item(item)
        return menu

    def _on_newline(self, action, value):
        action.set_state(value)
        kind = next(kind for kind, short, _l in NEWLINES if short == value.get_string())
        self.doc.set_newline_type(kind)


def parse_goto(text, line_count):
    """'12' or '12:5' → (line, column); clamps the line to the document; (None, None) if invalid."""
    match = re.fullmatch(r"\s*(\d+)\s*(?:[:,.]\s*(\d+))?\s*", text or "")
    if not match:
        return None, None
    line = max(1, min(int(match.group(1)), line_count))
    column = int(match.group(2)) if match.group(2) else 0
    return line, column

