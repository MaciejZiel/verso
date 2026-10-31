"""UI smoke test: drives a real Verso window (needs a display).

Run: GSETTINGS_SCHEMA_DIR=.dev/schemas GSETTINGS_BACKEND=memory tools/headless.sh python3 tests/ui_smoke.py
(bin/verso creates .dev/schemas on first start.)
"""
import os, sys, tempfile
# Drafts and session.json go to a throwaway folder, never to the real ~/.local/share/verso.
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="verso-ui-data-")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk
import traceback
from verso.app import Application

S = tempfile.mkdtemp(prefix="verso-ui-")
os.makedirs(f"{S}/e2e")
Path(f"{S}/e2e/test.md").write_text("# Test\n\nAla ma kota.\n\n- [ ] zadanie\n")
Path(f"{S}/e2e/data.json").write_text('{"a":1,"b":[1,2,{"c":"ala"}]}')
results = []
app = Application()
app.set_application_id("io.github.maciejziel.VersoTest")  # nie koliduje z działającą instancją

def check(name, cond):
    results.append((name, bool(cond)))

def shot(win, path):
    paintable = Gtk.WidgetPaintable.new(win)
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, win.get_width(), win.get_height())
    win.get_renderer().render_texture(snap.to_node(), None).save_to_png(path)

def steps(win):
    md = win.open_file(Gio.File.new_for_path(f"{S}/e2e/test.md"))
    yield 1200
    check("md opens in preview", md.mode == "preview" and md.kind == "markdown")
    check("title", win.window_title.get_title() == "test.md")
    md.set_mode("split")
    md.buffer.insert(md.buffer.get_end_iter(), "\n## Nowa sekcja\n")
    yield 600
    check("modified marker", win.window_title.get_title().startswith("• "))
    res = {}
    md.webview.evaluate_javascript("document.querySelector('#content h2') ? document.querySelector('#content h2').textContent : ''", -1, None, None, None,
        lambda w, r, _u: res.setdefault("h2", w.evaluate_javascript_finish(r).to_string()), None)
    yield 400
    check("live preview shows new heading", res.get("h2") == "Nowa sekcja")
    md.show_search(replace=True)
    md.search_entry.set_text("kota")
    yield 400
    check("search count", md.search_label.get_text() == "1 of 1")
    md.replace_entry.set_text("psa")
    md.replace_all()
    yield 200
    win.lookup_action("save").activate(None)
    yield 600
    text = open(f"{S}/e2e/test.md").read()
    check("saved with replacement", "Ala ma psa." in text and "## Nowa sekcja" in text)
    check("not modified after save", not md.modified)
    md.hide_search()
    # zewnętrzna zmiana pliku przy niezmodyfikowanym buforze: automatyczne wczytanie
    with open(f"{S}/e2e/test.md", "a") as f:
        f.write("\nDopisane z zewnątrz.\n")
    yield 1500
    check("external change reloaded", "Dopisane z zewnątrz." in md.buffer.get_property("text"))
    # zapis atomowy innego programu: plik tymczasowy + rename
    import os as _os
    tmp = f"{S}/e2e/.test.md.tmp"
    with open(tmp, "w") as f:
        f.write("# Test\n\nPodmienione atomowo.\n")
    _os.replace(tmp, f"{S}/e2e/test.md")
    yield 1500
    check("atomic replace reloaded", "Podmienione atomowo." in md.buffer.get_property("text"))
    md.set_mode("edit")
    md.show_search()
    md.search_entry.set_text("atomowo")
    yield 300
    md.hide_search()
    md.buffer.place_cursor(md.buffer.get_start_iter())
    md.show_search()
    md.find_next()
    yield 300
    check("search works after reopen", md.buffer.get_has_selection())
    md.hide_search()
    # pasek stanu: liczba słów, pozycja, przejdź do linii, język, wcięcia
    md.set_mode("edit")
    sb = md.statusbar
    md.buffer.set_text("jeden dwa trzy\ncztery\npięć sześć")
    yield 500
    check("word count", sb.words.get_text() == "6 words")
    md.buffer.select_range(md.buffer.get_start_iter(), md.buffer.get_iter_at_offset(9))
    yield 500
    check("selection word count", sb.words.get_text().startswith("2 words selected"))
    win.lookup_action("goto-line").activate(None)
    yield 300
    sb.goto_entry.set_text("3:6")
    sb.goto_entry.emit("activate")
    yield 200
    cursor = md.buffer.get_iter_at_mark(md.buffer.get_insert())
    check("go to line", cursor.get_line() == 2 and cursor.get_line_offset() == 5 and sb.position.get_child().get_text() == "Ln 3, Col 6")
    from gi.repository import GtkSource as _GS
    sb._set_language(_GS.LanguageManager.get_default().get_language("python3"))
    yield 100
    check("language from status bar", md.buffer.get_language().get_id() == "python3" and sb.language.get_child().get_text() == "Python")
    sb._syncing = True; sb.tabs_toggle.set_active(True); sb.width_spin.set_value(8); sb._syncing = False
    sb._on_indent_changed()
    check("indent override", not md.view.get_insert_spaces_instead_of_tabs() and md.view.get_tab_width() == 8
          and sb.indent.get_child().get_text() == "Tab Width: 8")
    md.set_mode("preview")
    check("status bar hidden in preview", not sb.get_visible())
    md.set_mode("edit")
    md.buffer.set_modified(False)
    # pliki z Windows: cp1250 + CRLF wczytane poprawnie i zapisane bez zmiany kodowania i końców linii
    cp = f"{S}/e2e/windows.txt"
    Path(cp).write_bytes("Zażółć gęślą jaźń\r\ndruga linia\r\n".encode("cp1250"))
    win_doc = win.open_file(Gio.File.new_for_path(cp))
    yield 800
    check("cp1250 decoded", win_doc.buffer.get_property("text").startswith("Zażółć gęślą jaźń") and win_doc.encoding.get_charset() == "WINDOWS-1250")
    win_doc.buffer.insert(win_doc.buffer.get_end_iter(), "\ntrzecia")
    win.lookup_action("save").activate(None)
    yield 600
    check("save keeps cp1250 and CRLF", Path(cp).read_bytes() == "Zażółć gęślą jaźń\r\ndruga linia\r\ntrzecia\r\n".encode("cp1250"))
    from gi.repository import GtkSource
    win_doc.set_newline_type(GtkSource.NewlineType.LF)
    win_doc.set_save_encoding(GtkSource.Encoding.get_utf8())
    check("changing line endings marks modified", win_doc.modified)
    win.lookup_action("save").activate(None)
    yield 600
    check("save as UTF-8 + LF", Path(cp).read_bytes() == "Zażółć gęślą jaźń\ndruga linia\ntrzecia\n".encode("utf-8"))
    win.tab_view.close_page(win.tab_view.get_page(win_doc))
    yield 300
    js = win.open_file(Gio.File.new_for_path(f"{S}/e2e/data.json"))
    yield 1200
    check("json in second tab", win.tab_view.get_n_pages() == 2 and js.kind == "json")
    js.format_json()
    check("format json", js.buffer.get_property("text").startswith('{\n  "a": 1'))
    js.buffer.undo()
    check("format json undo", js.buffer.get_property("text") == '{"a":1,"b":[1,2,{"c":"ala"}]}')
    js.format_json()
    js.set_mode("edit")
    win.lookup_action("toggle-view").activate(None)
    check("ctrl+e toggles", js.mode == "preview")
    yield 300
    win.lookup_action("close-tab").activate(None)  # zmodyfikowany: ma zapytać
    yield 500
    check("unsaved dialog shown", win.get_visible_dialog() is not None)
    dialog = win.get_visible_dialog()
    if dialog:
        dialog.close()  # Anuluj
    yield 400
    check("cancel keeps tab", win.tab_view.get_n_pages() == 2)
    from verso import settings as _settings
    _settings.get().set_boolean("restore-session", False)  # without drafts, quitting must ask
    app.activate_action("quit", None)
    yield 500
    check("ctrl+q asks about unsaved", win.get_visible_dialog() is not None and win.get_visible())
    if win.get_visible_dialog():
        win.get_visible_dialog().close()
    yield 300
    _settings.get().set_boolean("restore-session", True)
    js.buffer.set_modified(False)
    from gi.repository import Gdk
    # automatyczne nawiasy: "(" wstawia "()", ")" przeskakuje, Backspace usuwa parę
    scratch = win.new_document()
    scratch._on_editor_key(None, Gdk.KEY_parenleft, 0, 0)
    check("auto-pair inserts pair", scratch.buffer.get_property("text") == "()")
    scratch._on_editor_key(None, Gdk.KEY_parenright, 0, 0)
    check("auto-pair steps over", scratch.buffer.get_property("text") == "()" and scratch.buffer.get_property("cursor-position") == 2)
    scratch.buffer.place_cursor(scratch.buffer.get_iter_at_offset(1))
    scratch._on_editor_key(None, Gdk.KEY_BackSpace, 0, 0)
    check("backspace removes pair", scratch.buffer.get_property("text") == "")
    scratch.buffer.insert_at_cursor("don")
    scratch._on_editor_key(None, Gdk.KEY_apostrophe, 0, 0)
    check("apostrophe inside word stays single", scratch.buffer.get_property("text") == "don")
    scratch.buffer.set_text("hello")
    scratch.buffer.select_range(scratch.buffer.get_start_iter(), scratch.buffer.get_end_iter())
    scratch._on_editor_key(None, Gdk.KEY_quotedbl, 0, 0)
    check("quote wraps selection", scratch.buffer.get_property("text") == '"hello"')
    # ustawienia działają od razu
    _settings.get().set_boolean("show-map", True)
    _settings.get().set_uint("tab-width", 2)
    _settings.get().set_string("style-scheme", "solarized-light")
    yield 200
    dark = Adw.StyleManager.get_default().get_dark()
    check("settings apply live", scratch.map is not None and scratch.view.get_tab_width() == 2
          and scratch.buffer.get_style_scheme().get_id() == ("solarized-dark" if dark else "solarized-light"))
    scratch.buffer.set_text("alfa beta alfa")
    scratch.buffer.place_cursor(scratch.buffer.get_start_iter())
    scratch.show_search()
    scratch.search_entry.set_text("alfa")
    yield 400
    scratch.find_next()
    yield 300
    check("search keeps selection with map shown", scratch.buffer.get_has_selection())
    scratch.hide_search()
    scratch.buffer.set_modified(False)
    # surowy JSON zachowuje kolejność kluczy z pliku
    js.buffer.set_text('{"z": 1, "10": [true], "a": {}}')
    js.set_mode("preview")
    yield 600
    raw = {}
    js.webview.evaluate_javascript(
        "[...document.querySelectorAll('#json-bar button')].pop().click(); document.getElementById('json-raw').textContent",
        -1, None, None, None, lambda w, r, _u: raw.setdefault("text", w.evaluate_javascript_finish(r).to_string()), None)
    yield 400
    check("raw json keeps order", raw.get("text") == '{\n  "z": 1,\n  "10": [\n    true\n  ],\n  "a": {}\n}')
    # szkice: zamknięcie okna z niezapisanymi zmianami nie pyta, a sesja wraca z tekstem
    import json as _json
    from verso import session as _session
    win.tab_view.set_selected_page(win.tab_view.get_page(js))
    scratch.buffer.set_text("niezapisany szkic")  # nowy dokument bez pliku
    yield 1800  # autozapis
    check("draft written", scratch.draft_id is not None and _session.read_draft(scratch.draft_id) == "niezapisany szkic")
    app.hold()
    win.close()
    yield 1500  # longer than the session save delay: a late timer must not wipe the session
    check("closing last window does not ask", win.get_visible_dialog() is None and not win.get_visible())
    data = _json.loads((_session.data_dir() / "session.json").read_text())
    entries = data["documents"]
    js_entry = next((e for e in entries if e["uri"] and e["uri"].endswith("data.json")), None)
    check("session has drafts", js_entry is not None and js_entry["draft"] and any(e["uri"] is None and e["draft"] for e in entries))
    new_win = app._new_window()
    app._restore_session(new_win)
    yield 800
    docs = new_win.documents
    restored_js = next((d for d in docs if d.file and d.file.get_basename() == "data.json"), None)
    restored_draft = next((d for d in docs if d.file is None), None)
    check("drafts restored", restored_js is not None and restored_js.modified
          and restored_js.buffer.get_property("text") == '{"z": 1, "10": [true], "a": {}}'
          and restored_draft is not None and restored_draft.buffer.get_property("text") == "niezapisany szkic")
    check("selected tab restored", new_win.current is restored_js)
    # zapis szkicu usuwa plik szkicu
    restored_js.save()
    yield 600
    check("saving removes draft", restored_js.draft_id is None and not restored_js.modified
          and Path(f"{S}/e2e/data.json").read_text().startswith('{"z": 1'))
    for doc in new_win.documents:
        doc.buffer.set_modified(False)
    app.release()
    app.quit()

def on_activate(app):
    state = {}
    def tick():
        try:
            if "gen" not in state:
                state["gen"] = steps(app.get_active_window())
            delay = next(state["gen"])
        except StopIteration:
            return False
        except Exception:
            traceback.print_exc()
            app.quit()
            return False
        GLib.timeout_add(delay, tick)
        return False
    GLib.timeout_add(500, tick)

app.connect("activate", on_activate)
GLib.timeout_add_seconds(60, lambda: app.quit() or False)
app.run([])
for name, ok in results:
    print(("OK  " if ok else "FAIL"), name)

sys.exit(0 if results and all(ok for _, ok in results) else 1)
