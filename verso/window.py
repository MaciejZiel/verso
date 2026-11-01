"""Okno w układzie Edytora tekstu GNOME: pasek z „Otwórz”, karty, menu."""

from gettext import gettext as _
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import render, settings
from .document import Document

TEXT_FILTER_SUFFIXES = ("*.md", "*.markdown", "*.json", "*.txt", "*.yaml", "*.yml", "*.py", "*.csv", "*.toml", "*.html", "*.css", "*.js")


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        prefs = settings.get()
        super().__init__(
            application=app,
            default_width=prefs.get_int("window-width"),
            default_height=prefs.get_int("window-height"),
            maximized=prefs.get_boolean("window-maximized"),
            title="Verso",
        )
        self.set_icon_name(app.get_application_id())

        self.tab_view = Adw.TabView()
        self.tab_view.connect("notify::selected-page", self._on_page_selected)
        self.tab_view.connect("close-page", self._on_close_page)
        self.tab_view.connect("notify::n-pages", self._on_pages_changed)
        self.tab_bar = Adw.TabBar(view=self.tab_view, autohide=True)

        self.window_title = Adw.WindowTitle(title="Verso")
        header = Adw.HeaderBar(title_widget=self.window_title)
        header.pack_start(self._build_open_button())
        header.pack_start(Gtk.Button(icon_name="tab-new-symbolic", tooltip_text=_("New Tab (Ctrl+T)"), action_name="win.new-tab"))
        header.pack_end(self._build_menu_button())
        self.mode_group = self._build_mode_group()
        header.pack_end(self.mode_group)

        self.toasts = Adw.ToastOverlay(child=self.tab_view)
        toolbar = Adw.ToolbarView(content=self.toasts)
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(self.tab_bar)
        self.set_content(toolbar)

        self._install_actions()
        self._install_drop_target()
        self.connect("close-request", self._on_close_request)
        self._closing = False

    # ---------- pasek ----------

    def _build_open_button(self):
        self.recent_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["navigation-sidebar"])
        self.recent_list.connect("row-activated", self._on_recent_activated)
        self.recent_search = Gtk.SearchEntry(placeholder_text=_("Search Recent Documents"))
        self.recent_search.connect("search-changed", lambda *_: self.recent_list.invalidate_filter())
        self.recent_search.connect("activate", lambda *_: self._activate_first_recent())
        self.recent_list.set_filter_func(self._filter_recent)
        self.recent_empty = Gtk.Label(label=_("No Recent Documents"), css_classes=["dim-label"], margin_top=24, margin_bottom=24)
        self.recent_list.set_placeholder(self.recent_empty)

        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=420, min_content_width=340)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.recent_list)
        browse = Gtk.Button(label=_("Browse Files…"), action_name="win.open", css_classes=["flat"])

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self.recent_search)
        box.append(scroller)
        box.append(Gtk.Separator())
        box.append(browse)
        self.open_popover = Gtk.Popover(child=box)
        self.open_popover.connect("show", lambda *_: self._fill_recent())

        button = Gtk.MenuButton(popover=self.open_popover, tooltip_text=_("Open a File (Ctrl+O)"))
        # Jak w Edytorze tekstu: napis, a strzałka po prawej.
        label_box = Gtk.Box(spacing=6)
        label_box.append(Gtk.Label(label=_("Open")))
        label_box.append(Gtk.Image(icon_name="pan-down-symbolic"))
        button.set_child(label_box)
        return button

    def _build_menu_button(self):
        menu = Gio.Menu()
        section = Gio.Menu()
        section.append(_("New Window"), "app.new-window")
        section.append(_("Save"), "win.save")
        section.append(_("Save As…"), "win.save-as")
        section.append(_("Print…"), "win.print")
        section.append(_("Export as PDF…"), "win.export-pdf")
        section.append(_("Export as HTML…"), "win.export-html")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append(_("Find…"), "win.find")
        section.append(_("Find and Replace…"), "win.replace")
        section.append(_("Go to Line…"), "win.goto-line")
        section.append(_("Format"), "win.format-json")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append(_("Check Spelling"), "win.spellcheck")
        section.append(_("Wrap Lines"), "win.wrap")
        section.append(_("Show Outline"), "win.outline")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append(_("Preferences"), "app.preferences")
        section.append(_("Keyboard Shortcuts"), "app.shortcuts")
        section.append(_("About Verso"), "app.about")
        menu.append_section(None, section)
        return Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text=_("Main Menu"), primary=True)

    def _build_mode_group(self):
        group = Adw.ToggleGroup(css_classes=["flat", "round"], valign=Gtk.Align.CENTER)
        for name, icon in (("edit", "document-edit-symbolic"), ("split", "view-dual-symbolic"), ("preview", "view-reveal-symbolic")):
            tip = {"edit": _("Edit"), "split": _("Edit and Preview Side by Side"), "preview": _("Preview")}[name]
            group.add(Adw.Toggle(name=name, icon_name=icon, tooltip=_("{mode} (Ctrl+E to switch)").format(mode=tip)))
        group.connect("notify::active-name", self._on_mode_toggled)
        return group

    # ---------- akcje ----------

    def _install_actions(self):
        def add(name, callback, state=None):
            if state is None:
                action = Gio.SimpleAction.new(name, None)
                action.connect("activate", lambda *_: callback())
            else:
                action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(state))
                action.connect("change-state", lambda a, value: callback(a, value))
            self.add_action(action)
            return action

        add("open", self.choose_file)
        add("new-tab", lambda: self.new_document())
        add("save", lambda: self.save_document(self.current))
        add("save-as", lambda: self.save_document_as(self.current))
        add("close-tab", self._close_current_tab)
        add("find", lambda: self.current and self.current.show_search(False))
        add("replace", lambda: self.current and self.current.show_search(True))
        add("toggle-view", lambda: self.current and self.current.cycle_mode())
        add("format-json", lambda: self.current and self.current.format_document())
        add("goto-line", lambda: self.current and self.current.show_goto())
        add("print", lambda: self.current and self.current.print_document())
        add("export-pdf", self.export_pdf)
        add("export-html", self.export_html)
        add("bold", lambda: self.current and self.current.wrap_selection("**"))
        add("italic", lambda: self.current and self.current.wrap_selection("*"))
        add("code", lambda: self.current and self.current.wrap_selection("`"))
        add("link", lambda: self.current and self.current.insert_link())
        add("zoom-in", lambda: self.get_application().change_zoom(+1))
        add("zoom-out", lambda: self.get_application().change_zoom(-1))
        add("zoom-reset", lambda: self.get_application().change_zoom(0))
        add("next-tab", lambda: self.tab_view.select_next_page() or self._wrap_tabs(True))
        add("prev-tab", lambda: self.tab_view.select_previous_page() or self._wrap_tabs(False))
        self.spell_action = add("spellcheck", self._on_spellcheck, state=settings.get().get_boolean("spellcheck"))
        self.wrap_action = add("wrap", self._on_wrap, state=settings.get().get_boolean("wrap-lines"))
        self.outline_action = add("outline", self._on_outline, state=settings.get().get_boolean("show-outline"))
        # Keep the menu checkmarks in sync when the Preferences dialog changes the same settings.
        self._settings_handlers = [
            settings.get().connect("changed::wrap-lines", lambda s, k: self.wrap_action.set_state(GLib.Variant.new_boolean(s.get_boolean(k)))),
            settings.get().connect("changed::spellcheck", lambda s, k: self.spell_action.set_state(GLib.Variant.new_boolean(s.get_boolean(k)))),
            settings.get().connect("changed::show-outline", lambda s, k: self.outline_action.set_state(GLib.Variant.new_boolean(s.get_boolean(k)))),
        ]

    def _wrap_tabs(self, forward):
        n = self.tab_view.get_n_pages()
        if n > 1:
            self.tab_view.set_selected_page(self.tab_view.get_nth_page(0 if forward else n - 1))

    def _on_spellcheck(self, action, value):
        # Global setting: every document applies it (JSON stays without spell checking).
        action.set_state(value)
        settings.get().set_boolean("spellcheck", value.get_boolean())

    def _on_wrap(self, action, value):
        action.set_state(value)
        settings.get().set_boolean("wrap-lines", value.get_boolean())

    def _on_outline(self, action, value):
        action.set_state(value)
        settings.get().set_boolean("show-outline", value.get_boolean())
        if value.get_boolean() and self.current:
            self.current.focus_outline()  # F9 also brings keyboard users to the headings
        elif self.current and self.get_focus() is not None and self.get_focus().is_ancestor(self.current.outline):
            self.current.focus_text()  # the focused row is being hidden

    def _install_drop_target(self):
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.connect("drop", lambda _t, value, _x, _y: [self.open_file(f) for f in value.get_files()] or True)
        self.add_controller(target)

    # ---------- dokumenty ----------

    @property
    def current(self):
        page = self.tab_view.get_selected_page()
        return page.get_child() if page else None

    @property
    def documents(self):
        return [self.tab_view.get_nth_page(i).get_child() for i in range(self.tab_view.get_n_pages())]

    def _add_document(self, doc):
        doc.connect("state-changed", self._on_doc_state)
        doc.connect("state-changed", lambda *_: self.get_application() and self.get_application().schedule_session_save())
        doc.connect("open-file", lambda _d, f: self.open_file(f))
        doc.set_zoom(self.get_application().zoom)
        page = self.tab_view.append(doc)
        self.tab_view.set_selected_page(page)
        self._on_doc_state(doc)
        return doc

    def restore_draft(self, file, text, draft_id, encoding=None, newline=None, etag=None):
        current = self.current
        doc = current if current is not None and current.is_blank else self._add_document(Document())
        doc.restore_draft(file, text, draft_id, encoding, newline, etag)
        return doc

    def new_document(self):
        doc = self._add_document(Document())
        doc.view.grab_focus()
        return doc

    def open_file(self, file):
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            doc = page.get_child()
            if doc.file is not None and doc.file.equal(file):
                self.tab_view.set_selected_page(page)
                return doc
        current = self.current
        if current is not None and current.is_blank:
            doc = current  # pusty szkic zastępujemy, jak w Edytorze tekstu
        else:
            doc = self._add_document(Document())
        doc.load(file)
        Gtk.RecentManager.get_default().add_item(file.get_uri())
        return doc

    def choose_file(self):
        dialog = Gtk.FileDialog(title=_("Open File"))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        text_filter = Gtk.FileFilter(name=_("Text Files"))
        text_filter.add_mime_type("text/*")
        text_filter.add_mime_type("application/json")
        for pattern in TEXT_FILTER_SUFFIXES:
            text_filter.add_pattern(pattern)
        all_filter = Gtk.FileFilter(name=_("All Files"))
        all_filter.add_pattern("*")
        filters.append(text_filter)
        filters.append(all_filter)
        dialog.set_filters(filters)
        if self.current and self.current.file and self.current.file.get_parent():
            dialog.set_initial_folder(self.current.file.get_parent())

        def done(d, result):
            try:
                files = d.open_multiple_finish(result)
            except GLib.Error:
                return
            for i in range(files.get_n_items()):
                self.open_file(files.get_item(i))

        dialog.open_multiple(self, None, done)

    def export_pdf(self):
        doc = self.current
        if doc is None:
            return
        dialog = Gtk.FileDialog(title=_("Export as PDF"))
        dialog.set_initial_name(doc.title.rsplit(".", 1)[0] + ".pdf")
        if doc.file and doc.file.get_parent():
            dialog.set_initial_folder(doc.file.get_parent())

        def done(d, result):
            try:
                target = d.save_finish(result)
            except GLib.Error:
                return
            doc.print_document(
                export_file=target,
                # ok is None when the print dialog took over (no direct PDF printer available).
                on_done=lambda ok: ok is not None
                and self.toast(_("Saved as {name}").format(name=target.get_basename()) if ok else _("Could not export the PDF")),
            )

        dialog.save(self, None, done)

    def export_html(self):
        doc = self.current
        if doc is None or doc.kind != "markdown":
            return
        dialog = Gtk.FileDialog(title=_("Export as HTML"))
        dialog.set_initial_name(doc.title.rsplit(".", 1)[0] + ".html")
        if doc.file and doc.file.get_parent():
            # The same folder keeps relative images and links working.
            dialog.set_initial_folder(doc.file.get_parent())

        def done(d, result):
            try:
                target = d.save_finish(result)
            except GLib.Error:
                return
            doc.export_html(target, lambda ok: self.toast(
                _("Saved as {name}").format(name=target.get_basename()) if ok else _("Could not export the HTML file")))

        dialog.save(self, None, done)

    def save_document(self, doc, callback=None):
        if doc is None:
            return
        if doc.file is None:
            self.save_document_as(doc, callback)
        else:
            doc.save(callback=callback)

    def save_document_as(self, doc, callback=None):
        if doc is None:
            return
        dialog = Gtk.FileDialog(title=_("Save As"))
        dialog.set_initial_name(doc.file.get_basename() if doc.file else _("New Document") + ".md")
        if doc.file and doc.file.get_parent():
            dialog.set_initial_folder(doc.file.get_parent())

        def done(d, result):
            try:
                file = d.save_finish(result)
            except GLib.Error:
                if callback:
                    callback(False)
                return
            doc.save(file, callback)

        dialog.save(self, None, done)

    # ---------- zamykanie ----------

    def _close_current_tab(self):
        page = self.tab_view.get_selected_page()
        if page:
            self.tab_view.close_page(page)

    def _on_close_page(self, view, page):
        doc = page.get_child()

        def finish(ok):
            if ok:
                doc.discard_draft()  # closing the tab on purpose: its unsaved text is gone
                doc.shutdown()
            view.close_page_finish(page, ok)
            if ok and self.get_application() is not None:
                self.get_application().schedule_session_save()

        if not doc.modified:
            finish(True)
        else:
            self._confirm_save(doc, finish)
        return True

    def _on_pages_changed(self, *_):
        if self.tab_view.get_n_pages() == 0 and not self._closing:
            self.close()

    def _on_close_request(self, *_):
        if self._closing:
            return False
        pending = [d for d in self.documents if d.modified]
        # Like GNOME Text Editor: with session restore, closing the last window keeps unsaved
        # documents as drafts and brings them back next time, so there is nothing to ask.
        if pending and settings.get().get_boolean("restore-session") and self._is_last_window():
            for doc in pending:
                doc.flush_draft()
            pending = []
        if not pending:
            self._finish_close()
            return False

        def step(ok):
            if not ok:
                return
            if pending:
                doc = pending.pop(0)
                self.tab_view.set_selected_page(self.tab_view.get_page(doc))
                self._confirm_save(doc, lambda ok, doc=doc: (ok and doc.discard_draft(), step(ok)))
            else:
                self._finish_close()
                self.close()

        step(True)
        return True

    def _finish_close(self):
        """Everything that must happen exactly once before the window goes away."""
        self._closing = True
        self._save_state()
        for handler in self._settings_handlers:
            settings.get().disconnect(handler)
        self._settings_handlers = []
        for doc in self.documents:
            doc.shutdown()

    def _save_state(self):
        prefs = settings.get()
        prefs.set_boolean("window-maximized", self.is_maximized())
        if not self.is_maximized():
            width, height = self.get_default_size()
            prefs.set_int("window-width", width)
            prefs.set_int("window-height", height)
        app = self.get_application()
        if app is not None and self._is_last_window():
            app.save_session_now(closing=self)

    def is_closing(self):
        return self._closing

    def _is_last_window(self):
        app = self.get_application()
        return app is None or not [w for w in app.get_windows() if w is not self and isinstance(w, Window)]

    def _confirm_save(self, doc, callback):
        """Pyta „Zapisać zmiany?”; callback(True) gdy można zamknąć dokument."""
        dialog = Adw.AlertDialog(
            heading=_("Save Changes?"),
            body=_("Unsaved changes to “{title}” will be lost if you don’t save them.").format(title=doc.title),
        )
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("discard", _("_Discard"))
        dialog.add_response("save", _("_Save"))
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response == "save":
                self.save_document(doc, callback)
            else:
                callback(response == "discard")

        dialog.connect("response", on_response)
        dialog.present(self)

    # ---------- stan ----------

    def _on_page_selected(self, *_):
        doc = self.current
        if doc is None:
            return
        self._on_doc_state(doc)

    def _on_doc_state(self, doc):
        page = self.tab_view.get_page(doc)
        if page is None:
            return
        title = ("• " if doc.modified else "") + doc.title
        page.set_title(title)
        page.set_tooltip(doc.file.get_path() if doc.file and doc.file.get_path() else doc.title)
        page.set_icon(Gio.ThemedIcon.new("text-x-generic-symbolic"))
        if doc is not self.current:
            return
        self.window_title.set_title(title)
        self.window_title.set_subtitle(doc.subtitle)
        self.set_title(f"{doc.title} – Verso")
        self.mode_group.set_visible(doc.kind is not None)
        self._syncing_mode = True
        self.mode_group.set_active_name(doc.mode)
        self._syncing_mode = False
        # Formatting goes through strict JSON and would drop the comments of JSONC files.
        jsonc = doc.file is not None and render.json_allows_comments(doc.file.get_basename())
        self.lookup_action("format-json").set_enabled((doc.kind == "json" and not jsonc) or doc.kind == "markdown")
        self.lookup_action("export-html").set_enabled(doc.kind == "markdown")

    def _on_mode_toggled(self, group, _pspec):
        if getattr(self, "_syncing_mode", False):
            return
        doc = self.current
        name = group.get_active_name()
        if doc is not None and name and name != doc.mode:
            doc.set_mode(name)

    def toast(self, message):
        self.toasts.add_toast(Adw.Toast(title=message, timeout=3))

    # ---------- ostatnie pliki ----------

    def _fill_recent(self):
        self.recent_list.remove_all()
        self.recent_search.set_text("")
        items = [i for i in Gtk.RecentManager.get_default().get_items() if i.get_uri().startswith("file://") and i.exists()]
        items.sort(key=lambda i: i.get_modified().to_unix(), reverse=True)
        shown = 0
        for item in items:
            path = Path(GLib.filename_from_uri(item.get_uri())[0])
            mime = item.get_mime_type() or ""
            if not (mime.startswith("text/") or mime == "application/json" or render.kind_of(path.name)):
                continue
            row = Adw.ActionRow(title=GLib.markup_escape_text(path.name), activatable=True)
            row.set_subtitle(GLib.markup_escape_text(render.shorten_home(str(path.parent))))
            row.uri = item.get_uri()
            row.search_text = str(path).lower()
            self.recent_list.append(row)
            shown += 1
            if shown >= 50:
                break
        GLib.idle_add(lambda: self.recent_search.grab_focus() and False)

    def _filter_recent(self, row):
        query = self.recent_search.get_text().strip().lower()
        return not query or query in row.search_text

    def _activate_first_recent(self):
        # Wśród dzieci listy jest też etykieta „Brak ostatnich plików”: bierzemy tylko wiersze plików.
        row = next((r for r in self._rows() if hasattr(r, "uri") and self._filter_recent(r)), None)
        if row:
            self._on_recent_activated(self.recent_list, row)

    def _rows(self):
        child = self.recent_list.get_first_child()
        while child:
            yield child
            child = child.get_next_sibling()

    def _on_recent_activated(self, _list, row):
        self.open_popover.popdown()
        self.open_file(Gio.File.new_for_uri(row.uri))
