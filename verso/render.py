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

# File kind by extension. .jsonc is left out: json.loads does not accept comments.
KINDS = {
    "markdown": {".md", ".markdown", ".mdown", ".mkd"},
    "json": {".json", ".geojson", ".webmanifest"},
    "yaml": {".yaml", ".yml"},
    "toml": {".toml"},
    "xml": {".xml", ".xsd", ".xsl", ".xslt", ".rss", ".atom", ".plist", ".csproj", ".ui"},
    "csv": {".csv", ".tsv", ".tab"},
    "html": {".html", ".htm", ".xhtml"},
    "svg": {".svg"},
}
TREE_KINDS = {"json", "yaml", "toml", "xml"}  # shown with the collapsible tree
DATA_KINDS = TREE_KINDS | {"csv"}  # no spell checking or word count
TABLE_ROW_LIMIT = 5000



def shorten_home(path, home=None):
    """/home/ja/x → ~/x, jak w tytułach okien GNOME."""
    home = home or str(Path.home())
    if path == home or path.startswith(home + "/"):
        return "~" + path[len(home) :]
    return path


def kind_of(name):
    """Preview kind for a file name ('markdown', 'json', 'csv', …) or None when there is no preview."""
    suffix = Path(name or "").suffix.lower()
    return next((kind for kind, suffixes in KINDS.items() if suffix in suffixes), None)


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
        "noPreview": _("No preview for this file type."),
        "searchTable": _("Filter rows…"),
        # Translators: e.g. "Rows: 120"
        "rows": _("Rows: {count}"),
        # Translators: the table preview shows only the start of very long files.
        "truncated": _("Showing the first {shown} of {total} rows"),
        "emptyTable": _("The file has no rows."),
    }


def _plain(value):
    """YAML/TOML values that JSON cannot carry (dates, sets, bytes…) become text."""
    if isinstance(value, dict):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def parse_yaml(text):
    import yaml

    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = _("Line {line}, column {column}: ").format(line=mark.line + 1, column=mark.column + 1) if mark else ""
        return None, where + str(getattr(error, "problem", None) or error)
    data = documents[0] if len(documents) == 1 else documents
    return _plain(data), None


def parse_toml(text):
    import tomllib

    try:
        return _plain(tomllib.loads(text)), None
    except tomllib.TOMLDecodeError as error:
        return None, str(error)


def xml_tree(text):
    """XML as tree nodes: attributes as "@name", text as "#text", children by tag (repeats allowed)."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        line, column = error.position
        return None, _("Line {line}, column {column}: {message}").format(line=line, column=column + 1, message=str(error).split(":")[0])

    def node(element):
        pairs = [[f"@{name}", {"v": value}] for name, value in element.attrib.items()]
        text_value = (element.text or "").strip()
        children = list(element)
        if text_value and not children and not pairs:
            return {"v": text_value}
        if text_value:
            pairs.append(["#text", {"v": text_value}])
        pairs.extend([child.tag, node(child)] for child in children)
        return {"o": pairs}

    return {"o": [[root.tag, node(root)]]}, None


def table_payload(text, name=""):
    """CSV/TSV as header + rows; the delimiter is sniffed unless the extension says tab."""
    import csv
    import io

    sample = text[:20000]
    if Path(name).suffix.lower() in (".tsv", ".tab"):
        dialect = csv.excel_tab
    else:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [row for row in rows if row]
    if not rows:
        return {"kind": "table", "header": [], "rows": [], "total": 0}
    header, body = rows[0], rows[1:]
    return {
        "kind": "table",
        "header": header,
        "rows": body[:TABLE_ROW_LIMIT],
        "total": len(body),
        "delimiter": {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}.get(dialect.delimiter, dialect.delimiter),
    }


def preview_payload(kind, text, name=""):
    """Data for the preview page (preview.js)."""
    if kind == "markdown":
        html, toc, features = render_markdown(text)
        return {"kind": "markdown", "html": html, "toc": toc, "features": features}
    if kind in TREE_KINDS:
        if kind == "xml":
            tree, error = xml_tree(text)
        else:
            data, error = {"json": parse_json, "yaml": parse_yaml, "toml": parse_toml}[kind](text)
            tree = None if error else to_tree(data)
        label = {"json": "JSON", "yaml": "YAML", "toml": "TOML", "xml": "XML"}[kind]
        if error:
            return {"kind": "tree", "format": kind, "error": _("Invalid {format}. {error}").format(format=label, error=error)}
        return {"kind": "tree", "format": kind, "tree": tree}
    if kind == "csv":
        return table_payload(text, name)
    if kind == "svg":
        return {"kind": "image", "svg": text}
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
