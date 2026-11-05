import pytest

from verso.render import format_json, kind_of, parse_json, preview_payload, render_markdown


def test_kind_of():
    assert kind_of("README.md") == "markdown"
    assert kind_of("/a/b/NOTES.Markdown") == "markdown"
    assert kind_of("analiza.json") == "json"
    assert kind_of("plik.txt") is None
    assert kind_of(None) is None


def test_markdown_table_and_toc():
    html, toc, _features = render_markdown("# Tytuł\n\n## Sekcja\n\n| a | b |\n| - | - |\n| 1 | 2 |\n")
    assert "<table data-line=" in html
    assert "<td>1</td>" in html
    assert [(level, name) for level, _, name, _line in toc] == [(1, "Tytuł"), (2, "Sekcja")]
    assert all(anchor for _, anchor, _, _ in toc)


def test_markdown_task_list():
    html, _, _ = render_markdown("- [ ] do zrobienia\n- [x] zrobione\n- zwykły\n")
    assert html.count('type="checkbox"') == 2
    assert html.count("disabled checked>") == 1
    assert "task-list-item" in html
    assert "[ ]" not in html


def test_markdown_code_highlight():
    html, _, _ = render_markdown("```python\nx = 1\n```\n")
    assert 'class="highlight"' in html


def test_parse_json_error_has_line():
    data, error = parse_json('{\n  "a": 1\n  "b": 2\n}')
    assert data is None
    assert error is not None and error.startswith("Line 3")


def test_format_json_keeps_polish():
    assert format_json('{"a":"żółć","b":[1,2]}') == '{\n  "a": "żółć",\n  "b": [\n    1,\n    2\n  ]\n}\n'
    assert format_json("{zły") is None


def test_preview_payload():
    assert preview_payload("json", '{"a": 1}') == {"kind": "tree", "format": "json", "tree": {"o": [["a", {"v": 1}]]}}
    assert "error" in preview_payload("json", "[")
    assert preview_payload(None, "x") == {"kind": "none"}
    assert preview_payload("markdown", "# A")["toc"][0][2] == "A"


def test_build_page_has_nonce_csp():
    from verso.render import build_page

    page = build_page()
    assert "script-src 'nonce-" in page
    assert "window.verso" in page
    assert "html.dark .highlight" in page


def test_toc_names_are_plain_text():
    _, toc, _ = render_markdown("# A &amp; B\n\n# x < y\n")
    assert [name for _, _, name, _ in toc] == ["A & B", "x < y"]


def test_shorten_home():
    from verso.render import shorten_home

    assert shorten_home("/home/ja/Projects", "/home/ja") == "~/Projects"
    assert shorten_home("/home/ja", "/home/ja") == "~"
    assert shorten_home("/home/ja2/x", "/home/ja") == "/home/ja2/x"


def test_tree_keeps_key_order():
    from verso.render import to_tree

    tree = to_tree({"b": 1, "10": [True, None], "a": {}})
    assert [key for key, _ in tree["o"]] == ["b", "10", "a"]
    assert tree["o"][1][1] == {"a": [{"v": True}, {"v": None}]}


def test_build_page_injects_strings_safely(monkeypatch):
    from verso import render

    monkeypatch.setattr(render, "preview_strings", lambda: {"raw": "</script><b>"})
    page = render.build_page()
    assert "window.VERSO_I18N" in page
    assert "</script><b>" not in page


def test_parse_goto():
    from verso.statusbar import parse_goto

    assert parse_goto("12", 100) == (12, 0)
    assert parse_goto(" 7:3 ", 100) == (7, 3)
    assert parse_goto("500", 40) == (40, 0)
    assert parse_goto("0", 40) == (1, 0)
    assert parse_goto("abc", 40) == (None, None)
    assert parse_goto("", 40) == (None, None)


def test_count_words():
    from verso.statusbar import count_words

    assert count_words("Zażółć gęślą jaźń") == 3
    assert count_words("don't stop — well-known  42") == 4
    assert count_words("") == 0


def test_markdown_lines_math_mermaid_front_matter():
    text = "---\ntitle: T\n---\n# H\n\nCena 5$ i 6$, wzór $a_1 * b_2$.\n\n$$\nx^2\n$$\n\n```mermaid\ngraph TD; A-->B\n```\n"
    html, toc, features = render_markdown(text)
    assert '<h1 data-line="3" id="h">' in html
    assert '<table class="front-matter"' in html
    assert '<span class="math math-inline">a_1 * b_2</span>' in html
    assert "5$ i 6$" in html  # currency is not math
    assert '<div class="math math-display" data-line="7">x^2</div>' in html
    assert '<pre class="mermaid">' in html
    assert features == {"math": True, "mermaid": True}
    assert toc == [(1, "h", "H", 3)]


def test_markdown_autolink_and_strikethrough():
    pytest.importorskip("linkify_it")
    html, _, features = render_markdown("~~stare~~ https://example.org")
    assert "<s>stare</s>" in html and '<a href="https://example.org">' in html
    assert features == {"math": False, "mermaid": False}


def test_kinds_for_new_formats():
    assert [kind_of(n) for n in ("a.yml", "b.toml", "c.xml", "d.csv", "e.tsv", "f.html", "g.svg", "h.txt")] == [
        "yaml", "toml", "xml", "csv", "csv", "html", "svg", None]


def test_yaml_tree_keeps_order_and_dates():
    payload = preview_payload("yaml", "zeta: 1\nalfa:\n  - 2026-10-31\n  - true\n")
    assert payload["kind"] == "tree" and payload["format"] == "yaml"
    assert payload["tree"] == {"o": [["zeta", {"v": 1}], ["alfa", {"a": [{"v": "2026-10-31"}, {"v": True}]}]]}


def test_yaml_error_has_position():
    payload = preview_payload("yaml", "a: [1, 2\nb: 3\n")
    assert payload["error"].startswith("Invalid YAML. Line ")


def test_toml_tree():
    payload = preview_payload("toml", 'title = "x"\n[owner]\nname = "M"\n')
    assert payload["tree"] == {"o": [["title", {"v": "x"}], ["owner", {"o": [["name", {"v": "M"}]]}]]}
    assert preview_payload("toml", "a = ")["error"].startswith("Invalid TOML.")


def test_xml_tree_attributes_text_and_repeats():
    payload = preview_payload("xml", '<root id="1"><item>a</item><item>b</item><note lang="pl">tak</note></root>')
    root = payload["tree"]["o"][0]
    assert root[0] == "root"
    assert root[1]["o"] == [
        ["@id", {"v": "1"}], ["item", {"v": "a"}], ["item", {"v": "b"}],
        ["note", {"o": [["@lang", {"v": "pl"}], ["#text", {"v": "tak"}]]}],
    ]
    assert preview_payload("xml", "<a><b></a>")["error"].startswith("Invalid XML. Line 1")


def test_csv_sniffs_semicolon_and_limits_rows(monkeypatch):
    from verso import render

    payload = preview_payload("csv", "imię;wiek\nAla;30\nOla;25\n", "ludzie.csv")
    assert payload["header"] == ["imię", "wiek"] and payload["rows"] == [["Ala", "30"], ["Ola", "25"]]
    assert payload["delimiter"] == "semicolon" and payload["total"] == 2
    tsv = preview_payload("csv", "a\tb\n1\t2\n", "x.tsv")
    assert tsv["header"] == ["a", "b"] and tsv["delimiter"] == "tab"
    monkeypatch.setattr(render, "TABLE_ROW_LIMIT", 3)
    big = preview_payload("csv", "n\n" + "\n".join(str(i) for i in range(10)), "n.csv")
    assert len(big["rows"]) == 3 and big["total"] == 10


def test_svg_payload():
    assert preview_payload("svg", "<svg/>") == {"kind": "image", "svg": "<svg/>"}


def test_yaml_recursive_alias_and_bomb():
    from verso import render

    payload = preview_payload("yaml", "a: &x [1, *x]\n")
    assert payload["tree"]["o"][0][1]["a"][1] == {"v": "*recursive*"}
    bomb = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(
        f"{chr(98 + i)}: &{chr(98 + i)} [*{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}, *{chr(97 + i)}]\n"
        for i in range(8))
    assert "too large" in preview_payload("yaml", bomb)["error"]
    assert render.PLAIN_NODE_LIMIT >= 100_000


def test_csv_reader_error_is_reported(monkeypatch):
    import csv

    monkeypatch.setattr(csv, "field_size_limit", csv.field_size_limit)
    old = csv.field_size_limit(10)
    try:
        payload = preview_payload("csv", "a,b\n" + "x" * 50 + ",1\n", "big.csv")
    finally:
        csv.field_size_limit(old)
    assert payload["kind"] == "table" and payload["error"].startswith("Could not read the table")


def test_outline_lines_match_the_source():
    from verso.md import outline

    text = "---\ntitle: x\n---\n# One\n\ntext\n\nTwo\n===\n\n```\n# not a heading\n```\n### Three\n"
    assert [(level, title, line) for level, _, title, line in outline(text)] == [(1, "One", 3), (1, "Two", 7), (3, "Three", 13)]


def test_footnotes_and_definition_lists():
    pytest.importorskip("mdit_py_plugins")
    html, _toc, _features = render_markdown("Tekst[^1].\n\nTermin\n: Opis\n\n[^1]: Przypis.\n")
    assert '<sup class="footnote-ref"><a href="#fn1" id="fnref1">' in html
    assert '<li id="fn1" class="footnote-item">' in html
    assert "<dt" in html and ">Termin</dt>" in html and ">Opis</dd>" in html


def test_footnote_headings_and_ids_stay_out_of_the_way():
    pytest.importorskip("mdit_py_plugins")
    from verso.md import outline

    text = "Tekst[^1].\n\n[^1]: # Notka\n\n# fn1\n\n# B\n"
    assert [(title, line) for _, _, title, line in outline(text)] == [("fn1", 4), ("B", 6)]
    html, _toc, _features = render_markdown(text)
    assert html.count('id="fn1"') == 1  # the heading got another anchor


def test_new_kinds():
    from verso.render import kind_of

    assert kind_of(".env") == kind_of(".env.local") == kind_of("prod.env") == "ini"
    assert kind_of(".editorconfig") == kind_of("verso.service") == kind_of("app.desktop") == "ini"
    assert kind_of("events.jsonl") == kind_of("x.ndjson") == "jsonl"
    assert kind_of("settings.jsonc") == "json"
    assert kind_of(".environment.ini") == "ini"
    assert kind_of(".bashrc") is None


def test_json_with_comments():
    from verso.render import format_json, parse_json, strip_jsonc

    text = '{\n  // comment\n  "url": "http://x/*y*/", /* block */\n  "list": [1, 2,],\n}\n'
    assert preview_payload("json", text, "tsconfig.json")["tree"]
    assert "jsonc" in preview_payload("json", text, "package.json")["error"]  # plain JSON stays strict
    assert preview_payload("json", "[,]", "a.jsonc")["error"]  # a comma without a value is no trailing comma
    assert parse_json(text, allow_comments=True) == ({"url": "http://x/*y*/", "list": [1, 2]}, None)
    assert parse_json(text)[1]  # strict JSON still rejects it
    assert format_json(text) is None  # formatting must not silently drop the comments
    assert strip_jsonc('"a,]"') == '"a,]"'
    assert len(strip_jsonc(text)) == len(text) and strip_jsonc(text).count("\n") == text.count("\n")
    _data, error = parse_json('{\n  // ok\n  "a": 1\n  "b": 2\n}', allow_comments=True)
    assert error.startswith("Line 4,")  # the real mistake, not the comment


def test_json_lines():
    from verso import render

    payload = preview_payload("jsonl", '{"a": 1}\n\n[2]\n')
    assert payload["tree"] == {"a": [{"o": [["a", {"v": 1}]]}, {"a": [{"v": 2}]}]}
    assert "Line 2," in preview_payload("jsonl", '1\n{x}\n')["error"]
    assert preview_payload("jsonl", '{"a": "x\u2028y"}\r\n')["tree"]  # only \n separates records
    long = "\n".join("{}" for _ in range(render.JSONL_LIMIT + 3))
    payload = preview_payload("jsonl", long)
    assert len(payload["tree"]["a"]) == render.JSONL_LIMIT and payload["note"]


def test_ini_and_env():
    from verso.render import parse_ini

    assert parse_ini("[Unit]\nDescription=X\n[Service]\nExecStart=/bin/x a=b\n", "x.service") == (
        {"Unit": {"Description": "X"}, "Service": {"ExecStart": "/bin/x a=b"}}, None)
    assert parse_ini("root = true\n[*]\nindent_size = 4\n", ".editorconfig") == ({"root": "true", "*": {"indent_size": "4"}}, None)
    assert parse_ini("export A=1\nB='two words'\nC=x # note\n# comment\n", ".env") == ({"A": "1", "B": "two words", "C": "x"}, None)
    assert parse_ini("[a]\nk=1\nbroken\n", "x.ini") == (None, "Line 3: broken")
    assert parse_ini("[Service]\nEnvironment=A=1\nEnvironment=B=2\n", "x.service") == (
        {"Service": {"Environment": ["A=1", "B=2"]}}, None)
    assert parse_ini("[DEFAULT]\na=1\n[s]\nb=2\n", "x.ini") == ({"DEFAULT": {"a": "1"}, "s": {"b": "2"}}, None)
    assert parse_ini("key: v\n; note\nother: w\n", "x.cfg") == ({"key": "v", "other": "w"}, None)
    assert parse_ini("[s]\na=1\n", ".environment.ini") == ({"s": {"a": "1"}}, None)
    assert parse_ini("TOKEN='abc' # prod\n", ".env") == ({"TOKEN": "abc"}, None)
    payload = preview_payload("ini", "[s]\nk=v\n", "x.ini")
    assert payload["tree"] == {"o": [["s", {"o": [["k", {"v": "v"}]]}]]}


def test_markdown_renders_without_linkify(monkeypatch):
    # Fedora installs markdown-it without linkify-it-py: bare URLs stay text,
    # everything else renders (it used to raise on every document)
    from verso import md

    monkeypatch.setattr(md, "LINKIFY", False)
    monkeypatch.setattr(md, "_MD", None)
    html, toc, _ = render_markdown("# T\n\n~~stare~~ https://example.org [a](https://b.org)\n")
    assert "<s>stare</s>" in html
    assert "https://example.org" in html and '<a href="https://example.org">' not in html
    assert '<a href="https://b.org">a</a>' in html
    assert toc and toc[0][2] == "T"
