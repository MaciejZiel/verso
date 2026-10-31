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
    assert [(level, name) for level, _, name in toc] == [(1, "Tytuł"), (2, "Sekcja")]
    assert all(anchor for _, anchor, _ in toc)


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
    assert preview_payload("json", '{"a": 1}') == {"kind": "json", "tree": {"o": [["a", {"v": 1}]]}}
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
    assert [name for _, _, name in toc] == ["A & B", "x < y"]


def test_jsonc_has_no_json_preview():
    assert kind_of("settings.jsonc") is None


def test_shorten_home():
    from verso.render import shorten_home

    assert shorten_home("/home/ja/Rinvo", "/home/ja") == "~/Rinvo"
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
    assert toc == [(1, "h", "H")]


def test_markdown_autolink_and_strikethrough():
    html, _, features = render_markdown("~~stare~~ https://example.org")
    assert "<s>stare</s>" in html and '<a href="https://example.org">' in html
    assert features == {"math": False, "mermaid": False}
