"""Outline sidebar: the headings of a Markdown document, with the current section highlighted."""

from gettext import gettext as _

from gi.repository import GObject, Gtk, Pango


class Outline(Gtk.Box):
    __gsignals__ = {
        # A heading was chosen; the argument is its source line.
        "heading-activated": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["verso-outline"])
        self._entries = []
        self._rows = []
        self._current = -1

        title = Gtk.Label(label=_("Outline"), xalign=0, css_classes=["heading", "dim-label"])
        title.set_margin_start(12)
        title.set_margin_top(10)
        title.set_margin_bottom(6)
        self.append(title)

        self.list = Gtk.ListBox(css_classes=["navigation-sidebar"], selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.update_property([Gtk.AccessibleProperty.LABEL], [_("Outline")])
        self.list.connect("row-activated", self._on_row_activated)
        self._scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self._scroller.set_child(self.list)
        self.append(self._scroller)

        self.empty = Gtk.Label(
            label=_("No headings"), css_classes=["dim-label"], vexpand=True, valign=Gtk.Align.START, margin_top=12
        )
        self.append(self.empty)

    def set_entries(self, entries):
        """entries: (level, anchor, title, line) from md.outline. Rebuilds only when something changed."""
        entries = [tuple(entry) for entry in entries]
        if entries == self._entries:
            return
        if [entry[:3] for entry in entries] == [entry[:3] for entry in self._entries]:
            self._entries = entries  # only line numbers moved (typing above a heading): keep the rows and the scroll
            return
        self._entries = entries
        self._current = -1
        self.list.remove_all()
        self._rows = []
        top = min((level for level, *_rest in entries), default=1)
        for level, _anchor, title, _line in entries:
            label = Gtk.Label(label=title or "…", xalign=0, ellipsize=Pango.EllipsizeMode.END, tooltip_text=title)
            label.set_margin_start(6 + (level - top) * 14)
            if level == top:
                label.add_css_class("heading")
            row = Gtk.ListBoxRow(child=label)
            self.list.append(row)
            self._rows.append(row)
        self.empty.set_visible(not entries)
        self._scroller.set_visible(bool(entries))

    def set_current_line(self, line):
        """Highlights the last heading at or above the given source line."""
        index = -1
        for i, entry in enumerate(self._entries):
            if entry[3] <= line:
                index = i
            else:
                break
        if index == self._current:
            return
        self._current = index
        if index < 0:
            self.list.unselect_all()
        else:
            self.list.select_row(self._rows[index])
            self._scroll_to(self._rows[index])

    def _scroll_to(self, row):
        """Keeps the highlighted heading visible in a long outline (without moving the keyboard focus)."""
        found, bounds = row.compute_bounds(self.list)
        if not found:
            return
        adjustment = self._scroller.get_vadjustment()
        top, bottom = bounds.get_y(), bounds.get_y() + bounds.get_height()
        if top < adjustment.get_value():
            adjustment.set_value(top)
        elif bottom > adjustment.get_value() + adjustment.get_page_size():
            adjustment.set_value(bottom - adjustment.get_page_size())

    def _on_row_activated(self, _list, row):
        self._current = row.get_index()
        self.list.select_row(row)
        self.emit("heading-activated", self._entries[self._current][3])
