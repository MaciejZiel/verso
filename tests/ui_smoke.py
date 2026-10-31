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
from gi.repository import Adw, Gio, GLib, Gtk, GtkSource
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
    # synchroniczne przewijanie w trybie obok siebie
    long_md = "".join(f"## Sekcja {i}\n\nAkapit numer {i} z odrobiną tekstu.\n\n" for i in range(120))
    md.buffer.set_text(long_md)
    md.set_mode("split")
    yield 1200
    _found, it = md.buffer.get_iter_at_line(200)  # around "Sekcja 50"
    y, _h = md.view.get_line_yrange(it)
    md.source_scroller.get_vadjustment().set_value(y)
    yield 600
    top = {}
    md.webview.evaluate_javascript(
        "(() => { const b = [...document.querySelectorAll('#content h2')].find(e => e.getBoundingClientRect().bottom > 0); return b ? b.textContent : ''; })()",
        -1, None, None, None, lambda w, r, _u: top.setdefault("h", w.evaluate_javascript_finish(r).to_string()), None)
    yield 300
    check("editor scroll moves preview", top.get("h") in ("Sekcja 49", "Sekcja 50", "Sekcja 51"))
    yield 300
    md.webview.evaluate_javascript("document.querySelectorAll('#content h2')[90].scrollIntoView()", -1, None, None, None, None, None)
    yield 700
    adj = md.source_scroller.get_vadjustment()
    it, _t = md.view.get_line_at_y(int(adj.get_value()))
    check("preview scroll moves editor", 355 <= it.get_line() <= 365)
    # konspekt nagłówków (F9): lista sekcji, bieżąca sekcja, skok do sekcji
    win.lookup_action("outline").activate(None)
    yield 600
    rows = md.outline.list.observe_children().get_n_items()
    check("outline shows headings", md.split_view.get_show_sidebar() and rows == 120)
    selected = md.outline.list.get_selected_row()
    check("outline highlights section in view", selected is not None and 88 <= selected.get_index() <= 92)
    md.outline.list.emit("row-activated", md.outline.list.get_row_at_index(10))
    yield 700
    cursor = md.buffer.get_iter_at_mark(md.buffer.get_insert())
    js_top = {}
    md.webview.evaluate_javascript(
        "[document.documentElement.classList.contains('outline-shown'), [...document.querySelectorAll('#content h2')].find(e => e.getBoundingClientRect().bottom > 0).textContent].join('|')",
        -1, None, None, None, lambda w, r, _u: js_top.setdefault("v", w.evaluate_javascript_finish(r).to_string()), None)
    yield 300
    check("outline jumps editor to heading", cursor.get_line() == 40)
    check("outline: preview follows and hides its toc", js_top.get("v") in ("true|Sekcja 9", "true|Sekcja 10"))
    md.set_mode("preview")
    md.outline.list.emit("row-activated", md.outline.list.get_row_at_index(70))
    yield 700
    selected = md.outline.list.get_selected_row()
    check("outline in preview mode", selected is not None and selected.get_index() in (69, 70))
    md.buffer.set_text(long_md + "# Koniec\n")
    yield 700
    check("outline updates while typing", md.outline.list.observe_children().get_n_items() == 121)
    win.lookup_action("outline").activate(None)
    yield 300
    check("outline hides", not md.split_view.get_show_sidebar())
    md.buffer.set_text("[skok](#sekcja-100)\n\n" + long_md)
    yield 700
    jump = {}
    md.webview.evaluate_javascript("window.scrollTo(0, 0); document.querySelector('#content a').click()", -1, None, None, None, None, None)
    yield 1200
    md.webview.evaluate_javascript(
        "String(Math.round(document.getElementById('sekcja-100').getBoundingClientRect().top))",
        -1, None, None, None, lambda w, r, _u: jump.setdefault("top", w.evaluate_javascript_finish(r).to_string()), None)
    yield 300
    check("link to a heading scrolls the preview", jump.get("top") is not None and 0 <= int(jump["top"]) <= 40)
    md.set_mode("split")
    md.buffer.set_modified(False)
    # podglądy formatów: CSV, YAML, XML, HTML (bez skryptów), SVG
    def js_value(view, script, store, key):
        view.evaluate_javascript(script, -1, None, None, None,
                                 lambda w, r, _u: store.setdefault(key, w.evaluate_javascript_finish(r).to_string()), None)
    files = {
        "dane.csv": "miasto;ludność\nKraków;800000\nŁódź;650000\nGdańsk;480000\n",
        "konf.yaml": "serwer:\n  port: 8080\n  hosty: [a, b]\n",
        "feed.xml": "<rss><channel><title>Wiadomości</title></channel></rss>",
        "strona.html": "<h1 id='t'>Nagłówek</h1><script>document.getElementById('t').textContent='SKRYPT'</script><iframe id='f' src='inna.html'></iframe>",
        "rss.xml": "<rss><channel><item><t>A</t></item><item><t>B</t></item></channel></rss>",
        "liczby.csv": "nazwa;kwota\na;1,500\nb;20\nc;300\n",
        "ikona.svg": "<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'><rect width='10' height='10'/></svg>",
    }
    opened = {}
    Path(f"{S}/e2e/inna.html").write_text("<p>RAMKA</p>")
    for name, content in files.items():
        Path(f"{S}/e2e/{name}").write_text(content)
        opened[name] = win.open_file(Gio.File.new_for_path(f"{S}/e2e/{name}"))
    yield 1500
    out = {}
    csv_doc = opened["dane.csv"]
    js_value(csv_doc.webview, "document.getElementById('json-count').textContent", out, "rows")
    js_value(csv_doc.webview, "document.querySelector('.data-table th:nth-child(2)').click(); document.querySelector('.data-table tbody td').textContent", out, "sorted")
    js_value(opened["konf.yaml"].webview, "[...document.querySelectorAll('.jkey')].map(e => e.textContent).join(',')", out, "yaml")
    js_value(opened["feed.xml"].webview, "document.querySelector('#json-tree').textContent", out, "xml")
    js_value(opened["strona.html"].webview, "document.getElementById('t').textContent", out, "html")
    js_value(opened["ikona.svg"].webview, "document.querySelector('.image-preview img') ? 'img' : ''", out, "svg")
    js_value(opened["strona.html"].webview, "(() => { const d = document.getElementById('f').contentDocument; return d && d.body ? d.body.textContent : ''; })()", out, "iframe")
    js_value(opened["rss.xml"].webview,
             "(() => { const items = [...document.querySelectorAll('.jnode')].filter(n => n.querySelector(':scope > .jrow .jkey')?.textContent === '\"item\"');"
             " items[0].querySelector('.jtoggle').click(); return items.map(n => n.classList.contains('jopen')).join(','); })()", out, "xml_items")
    js_value(opened["liczby.csv"].webview,
             "document.querySelector('.data-table th:nth-child(2)').click(); [...document.querySelectorAll('.data-table tbody td:nth-child(2)')].map(td => td.textContent).join(' ')", out, "numbers")
    yield 800
    check("html iframe blocked", out.get("iframe") == "")
    check("xml repeated tags open separately", out.get("xml_items") in ("false,true", "true,false"))
    check("thousands separator sorts as number", out.get("numbers") == "20 300 1,500")
    check("csv table", out.get("rows") == "Rows: 3" and out.get("sorted") == "Gdańsk")
    check("yaml tree", out.get("yaml") == '"serwer","port","hosty",0,1')
    check("xml tree", "Wiadomości" in out.get("xml", ""))
    check("html preview without scripts", out.get("html") == "Nagłówek")
    check("svg preview", out.get("svg") == "img")
    check("no spell check in data files", not csv_doc.spelling.get_enabled() and csv_doc.statusbar.words.get_text() == "")
    for doc in opened.values():
        win.tab_view.close_page(win.tab_view.get_page(doc))
    yield 400
    # eksport do PDF: z podglądu (Markdown) i ze źródła (zwykły tekst)
    pdf_results = {}
    md.print_document(export_file=Gio.File.new_for_path(f"{S}/e2e/preview.pdf"), on_done=lambda ok: pdf_results.setdefault("md", ok))
    plain = win.new_document()
    plain.buffer.set_text("def f():\n    return 42\n" * 30)
    plain.print_document(export_file=Gio.File.new_for_path(f"{S}/e2e/source.pdf"), on_done=lambda ok: pdf_results.setdefault("plain", ok))
    yield 3000
    def is_pdf(path):
        return Path(path).exists() and Path(path).read_bytes()[:5] == b"%PDF-" and Path(path).stat().st_size > 1000
    # With a "Print to File" printer the PDF is written directly; without one the print dialog opens.
    direct = pdf_results.get("md") and is_pdf(f"{S}/e2e/preview.pdf")
    check("export preview to pdf (direct or via dialog)", direct or pdf_results.get("md", "missing") is None)
    for dialog_window in [w for w in Gtk.Window.list_toplevels() if w is not win and isinstance(w, Gtk.Window) and w.get_visible()]:
        dialog_window.close()
    check("export source to pdf", pdf_results.get("plain") and is_pdf(f"{S}/e2e/source.pdf"))
    plain.buffer.set_modified(False)
    win.tab_view.close_page(win.tab_view.get_page(plain))
    # motywy podglądu
    from verso import settings as _s0
    _s0.get().set_string("preview-theme", "reader")
    yield 200
    theme = {}
    md.webview.evaluate_javascript("document.documentElement.className", -1, None, None, None,
                                   lambda w, r, _u: theme.setdefault("c", w.evaluate_javascript_finish(r).to_string()), None)
    yield 300
    check("preview theme applies", "theme-reader" in theme.get("c", ""))
    _s0.get().set_string("preview-theme", "github")
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
    _session.wait_for_writes()
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
    # szkic pliku zmienionego na dysku w międzyczasie: pasek z ostrzeżeniem zamiast cichego nadpisania
    stale = new_win.restore_draft(Gio.File.new_for_path(f"{S}/e2e/test.md"), "stary szkic", _session.new_draft_id(), etag="0:0")
    check("draft of changed file shows banner", stale.banner.get_revealed())
    # ponowne otwarcie z innym kodowaniem pyta, gdy są niezapisane zmiany
    stale.reopen_with_encoding(GtkSource.Encoding.get_utf8())
    yield 300
    dialog = new_win.get_visible_dialog()
    check("reopen with encoding asks", dialog is not None)
    if dialog:
        dialog.force_close()
    yield 200
    check("cancel keeps unsaved text", stale.buffer.get_property("text") == "stary szkic")
    # Ctrl+L działa przy wyłączonym pasku stanu
    _settings.get().set_boolean("show-statusbar", False)
    stale.set_mode("edit")
    check("status bar off", not stale.statusbar.get_visible())
    new_win.lookup_action("goto-line").activate(None)
    yield 300
    popover = stale.statusbar.position.get_popover()
    check("go to line with status bar off", popover.get_visible())
    popover.popdown()
    yield 300
    check("status bar hidden again", not stale.statusbar.get_visible())
    _settings.get().set_boolean("show-statusbar", True)
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
