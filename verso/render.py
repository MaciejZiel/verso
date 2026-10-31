"""Czyste funkcje bez GTK: rozpoznanie rodzaju pliku i zamiana treści na dane podglądu."""

import html as html_lib
from gettext import gettext as _
import json
import re
import secrets
import xml.etree.ElementTree as etree
from functools import cache
from pathlib import Path

import markdown
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor
from pygments.formatters import HtmlFormatter

DATA_DIR = Path(__file__).parent / "data"

MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd"}
JSON_SUFFIXES = {".json", ".geojson", ".webmanifest"}  # bez .jsonc: komentarzy json.loads nie przyjmie

_TASK = re.compile(r"^\[([ xX])\]\s+")


def shorten_home(path, home=None):
    """/home/ja/x → ~/x, jak w tytułach okien GNOME."""
    home = home or str(Path.home())
    if path == home or path.startswith(home + "/"):
        return "~" + path[len(home) :]
    return path


def kind_of(name):
    """Zwraca 'markdown', 'json' albo None (plik bez podglądu)."""
    suffix = Path(name or "").suffix.lower()
    if suffix in MARKDOWN_SUFFIXES:
        return "markdown"
    if suffix in JSON_SUFFIXES:
        return "json"
    return None


class _TaskListProcessor(Treeprocessor):
    """`- [ ] zadanie` i `- [x] zadanie` jako pola wyboru, jak na GitHubie."""

    def run(self, root):
        for li in root.iter("li"):
            target = li
            # Lista „luźna” trzyma tekst w <p> wewnątrz <li>.
            if not (li.text or "").strip() and len(li) and li[0].tag == "p":
                target = li[0]
            text = target.text or ""
            match = _TASK.match(text)
            if not match:
                continue
            box = etree.Element("input", {"type": "checkbox", "disabled": "disabled"})
            if match.group(1).lower() == "x":
                box.set("checked", "checked")
            box.tail = " " + text[match.end() :]
            target.text = ""
            target.insert(0, box)
            li.set("class", "task-list-item")


class TaskListExtension(Extension):
    def extendMarkdown(self, md):
        md.treeprocessors.register(_TaskListProcessor(md), "tasklist", 5)


def render_markdown(text):
    """Zwraca (html, toc), gdzie toc to lista (poziom, id, tytuł) nagłówków."""
    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "codehilite",
            "toc",
            "sane_lists",
            "nl2br",
            TaskListExtension(),
        ],
        extension_configs={
            "codehilite": {"guess_lang": False, "css_class": "highlight"},
            "toc": {"permalink": False},
        },
        output_format="html",
    )
    html = md.convert(text)
    toc = []

    def walk(tokens):
        for token in tokens:
            # „name” jest już zakodowane w HTML, a spis treści wstawia zwykły tekst.
            toc.append((token["level"], token["id"], html_lib.unescape(token["name"])))
            walk(token["children"])

    walk(getattr(md, "toc_tokens", []))  # atrybut dodaje rozszerzenie toc
    return html, toc


def parse_json(text):
    """Zwraca (dane, None) albo (None, opis błędu z numerem linii)."""
    try:
        return json.loads(text), None
    except json.JSONDecodeError as error:
        return None, _("Line {line}, column {column}: {message}").format(line=error.lineno, column=error.colno, message=error.msg)


def format_json(text, indent=2):
    """Ładnie sformatowany JSON albo None, gdy tekst nie jest poprawnym JSON-em."""
    data, error = parse_json(text)
    if error:
        return None
    return json.dumps(data, ensure_ascii=False, indent=indent) + "\n"


def to_tree(value):
    """Data for the JS tree that keeps key order (JS objects put numeric-looking keys first)."""
    if isinstance(value, dict):
        return {"o": [[key, to_tree(child)] for key, child in value.items()]}
    if isinstance(value, list):
        return {"a": [to_tree(child) for child in value]}
    return {"v": value}


def preview_strings():
    """Translated strings for preview.js."""
    return {
        "contents": _("Contents"),
        "searchJson": _("Search keys and values…"),
        "expandAll": _("Expand All"),
        "collapseAll": _("Collapse All"),
        "raw": _("Raw"),
        "invalidJson": _("Invalid JSON."),
        "noPreview": _("No preview for this file type."),
    }


def preview_payload(kind, text):
    """Słownik wysyłany do strony podglądu (preview.js)."""
    if kind == "markdown":
        html, toc = render_markdown(text)
        return {"kind": "markdown", "html": html, "toc": toc}
    if kind == "json":
        data, error = parse_json(text)
        if error:
            return {"kind": "json", "error": error}
        raw = json.dumps(data, ensure_ascii=False, indent=2)
        return {"kind": "json", "tree": to_tree(data), "raw": raw}
    return {"kind": "none"}


@cache
def _pygments_css():
    light = HtmlFormatter(style="default").get_style_defs("html:not(.dark) .highlight")
    dark = HtmlFormatter(style="github-dark").get_style_defs("html.dark .highlight")
    return light + "\n" + dark


def build_page():
    """Pełna strona podglądu do WebKit.load_html (styl i skrypt wbudowane)."""
    nonce = secrets.token_urlsafe(16)
    css = (DATA_DIR / "preview.css").read_text(encoding="utf-8")
    js = (DATA_DIR / "preview.js").read_text(encoding="utf-8")
    # "</" would end the <script> element early.
    strings = json.dumps(preview_strings(), ensure_ascii=False).replace("</", "<\\/")
    # Skrypty z treści pliku (surowy HTML w Markdownie) nie wykonają się: CSP wpuszcza tylko nasz nonce.
    csp = (
        "default-src 'none'; img-src file: data: https: http:; media-src file: data: https:; "
        f"style-src 'unsafe-inline'; font-src file: data:; script-src 'nonce-{nonce}'"
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        f"<style>{css}\n{_pygments_css()}</style></head>"
        f"<body><div id='root'></div><script nonce='{nonce}'>window.VERSO_I18N = {strings};\n{js}</script></body></html>"
    )
