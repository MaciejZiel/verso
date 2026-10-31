"""Czyste funkcje bez GTK: rozpoznanie rodzaju pliku i zamiana treści na dane podglądu."""

import html as html_lib
from gettext import gettext as _
import json
import secrets
from functools import cache
from pathlib import Path

from pygments.formatters import HtmlFormatter

from . import md

DATA_DIR = Path(__file__).parent / "data"

MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd"}
JSON_SUFFIXES = {".json", ".geojson", ".webmanifest"}  # bez .jsonc: komentarzy json.loads nie przyjmie



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


def render_markdown(text):
    """Returns (html, toc, features); see md.render."""
    return md.render(text)


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
        # Translators: count of search results in the JSON preview, e.g. "Matches: 3".
        "matches": _("Matches: {count}"),
        "invalidJson": _("Invalid JSON."),
        "noPreview": _("No preview for this file type."),
    }


def preview_payload(kind, text):
    """Słownik wysyłany do strony podglądu (preview.js)."""
    if kind == "markdown":
        html, toc, features = render_markdown(text)
        return {"kind": "markdown", "html": html, "toc": toc, "features": features}
    if kind == "json":
        data, error = parse_json(text)
        if error:
            return {"kind": "json", "error": error}
        return {"kind": "json", "tree": to_tree(data)}
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
    # Vendored KaTeX/Mermaid are loaded on demand from file: URIs; content inserted with innerHTML
    # never runs scripts, so allowing file: scripts only lets our own loader in.
    csp = (
        "default-src 'none'; img-src file: data: https: http:; media-src file: data: https:; "
        f"style-src 'unsafe-inline' file:; font-src file: data:; script-src 'nonce-{nonce}' file:"
    )
    vendor = json.dumps((DATA_DIR / "vendor").as_uri() + "/")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        f"<style>{css}\n{_pygments_css()}</style></head>"
        f"<body><div id='root'></div><script nonce='{nonce}'>window.VERSO_I18N = {strings};\nwindow.VERSO_VENDOR = {vendor};\n{js}</script></body></html>"
    )
