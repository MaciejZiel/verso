"""UI smoke test: drives a real Verso window (needs a display).

Run: GSETTINGS_SCHEMA_DIR=.dev/schemas GSETTINGS_BACKEND=memory tools/headless.sh python3 tests/ui_smoke.py
(bin/verso creates .dev/schemas on first start.)
"""
import os, sys, tempfile
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
    app.activate_action("quit", None)
    yield 500
    check("ctrl+q asks about unsaved", win.get_visible_dialog() is not None and win.get_visible())
    if win.get_visible_dialog():
        win.get_visible_dialog().close()
    yield 300
    js.buffer.set_modified(False)
    from verso import settings as _settings
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
    # zamknięcie okna z niezapisanym dokumentem: po „Odrzuć” sesja i tak się zapisuje
    win.tab_view.set_selected_page(win.tab_view.get_page(js))
    app.hold()
    win.close()
    yield 400
    dialog = win.get_visible_dialog()
    check("close asks about unsaved", dialog is not None)
    while dialog is not None:
        dialog.emit("response", "discard")
        dialog.force_close()
        yield 300
        dialog = win.get_visible_dialog()
    yield 300
    uris = _settings.get().get_strv("session-uris")
    check("session saved after discard", len(uris) == 2 and uris[1].endswith("data.json") and _settings.get().get_int("session-selected") == 1)
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
