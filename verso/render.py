"""Czyste funkcje bez GTK: rozpoznanie rodzaju pliku i zamiana treści na dane podglądu."""

import html as html_lib
from gettext import gettext as _
import json
import re
import secrets
from functools import cache
from pathlib import Path

from pygments.formatters import HtmlFormatter

from . import md

DATA_DIR = Path(__file__).parent / "data"

# File kind by extension (dot files such as .env are recognised by name in kind_of).
KINDS = {
    "markdown": {".md", ".markdown", ".mdown", ".mkd"},
    "json": {".json", ".jsonc", ".geojson", ".webmanifest"},
    "jsonl": {".jsonl", ".ndjson"},
    "ini": {".ini", ".cfg", ".env", ".desktop", ".service", ".timer", ".socket"},
    "yaml": {".yaml", ".yml"},
    "toml": {".toml"},
    "xml": {".xml", ".xsd", ".xsl", ".xslt", ".rss", ".atom", ".plist", ".csproj", ".ui"},
    "csv": {".csv", ".tsv", ".tab"},
    "html": {".html", ".htm", ".xhtml"},
    "svg": {".svg"},
}
TREE_KINDS = {"json", "jsonl", "yaml", "toml", "xml", "ini"}  # shown with the collapsible tree
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
    base = Path(name or "").name.lower()
    if is_env_file(base) or base == ".editorconfig":  # dot files have no suffix
        return "ini"
    suffix = Path(base).suffix
    return next((kind for kind, suffixes in KINDS.items() if suffix in suffixes), None)


def is_env_file(name):
    """.env, .env.local, production.env: KEY=value lines rather than INI sections."""
    base = Path(name or "").name.lower()
    return base == ".env" or base.startswith(".env.") or base.endswith(".env")


def json_allows_comments(name):
    """JSON dialects that allow comments and trailing commas (JSONC). Plain .json files stay strict:
    npm, browsers and most tools reject comments there."""
    base = Path(name or "").name.lower()
    return (base.endswith(".jsonc") or base in ("devcontainer.json", ".devcontainer.json")
            or (base.endswith(".json") and base.startswith(("tsconfig", "jsconfig"))))


def render_markdown(text):
    """Returns (html, toc, features); see md.render."""
    return md.render(text)


def _json_error(error, line=None):
    return _("Line {line}, column {column}: {message}").format(line=line or error.lineno, column=error.colno, message=error.msg)


def parse_json(text, allow_comments=False):
    """Zwraca (dane, None) albo (None, opis błędu z numerem linii). With allow_comments, also JSON with
    comments and trailing commas (tsconfig.json, VS Code settings, .jsonc)."""
    try:
        return json.loads(text), None
    except json.JSONDecodeError as error:
        if allow_comments:
            try:
                return json.loads(strip_jsonc(text)), None
            except json.JSONDecodeError as relaxed:
                if relaxed.lineno != error.lineno or relaxed.colno != error.colno:
                    return None, _json_error(relaxed)  # the comments were fine; this is the real problem
        return None, _json_error(error)


def strip_jsonc(text):
    """Blanks out // and /* */ comments and trailing commas, outside strings. Everything else keeps
    its place, so error positions still point at the original text."""
    out = list(text)
    i, n = 0, len(text)
    in_string = False
    last_comma = None  # index of a comma not yet followed by anything but space and comments
    previous = ""  # the last character outside comments and space, before the current one
    while i < n:
        char = text[i]
        if in_string:
            if char == "\\":
                i += 2
                continue
            if char == '"':
                in_string = False
        elif char == '"':
            in_string, last_comma = True, None
        elif char == "/" and text.startswith("//", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            out[i:end] = " " * (end - i)
            i = end
            continue
        elif char == "/" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out[i:end] = [c if c == "\n" else " " for c in text[i:end]]
            i = end
            continue
        elif char == ",":
            # A comma right after [ { or another comma has no value before it: an error to keep.
            last_comma = i if previous not in ("[", "{", ",", "") else None
        elif char in "}]":
            if last_comma is not None:
                out[last_comma] = " "
            last_comma = None
        elif not char.isspace():
            last_comma = None
        if not in_string and not char.isspace() or char == '"':
            previous = char
        i += 1
    return "".join(out)


JSONL_LIMIT = 5000


def jsonl_payload(text):
    """JSON Lines: one JSON value per line, shown as a list; very long files show their start."""
    # Only \n separates records: JSON strings may hold U+2028 and other characters splitlines() breaks on.
    lines = [(number, line.rstrip("\r")) for number, line in enumerate(text.split("\n"), 1) if line.strip()]
    records = []
    for number, line in lines[:JSONL_LIMIT]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            message = _json_error(error, line=number)
            return {"kind": "tree", "format": "jsonl", "error": _("Invalid {format}. {error}").format(format="JSON Lines", error=message)}
    payload = {"kind": "tree", "format": "jsonl", "tree": to_tree(records)}
    if len(lines) > JSONL_LIMIT:
        payload["note"] = _("Showing the first {shown} of {total} rows").format(shown=JSONL_LIMIT, total=len(lines))
    return payload


_INI_PAIR = re.compile(r"^([^=:]+?)\s*[=:]\s*(.*)$")


def _add(mapping, key, value):
    # Repeated keys are normal in systemd units (Environment=, After=…): keep every value.
    if key not in mapping:
        mapping[key] = value
    elif isinstance(mapping[key], list):
        mapping[key].append(value)
    else:
        mapping[key] = [mapping[key], value]


def parse_ini(text, name=""):
    """INI files: [section] headers, key = value or key: value, # and ; comments, keys before the
    first section, repeated keys as lists. .env files go to parse_env."""
    if is_env_file(name):
        return parse_env(text), None
    data, section = {}, None
    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            title = line[1:-1].strip()
            section = data[title] if isinstance(data.get(title), dict) else data.setdefault(title, {})
            continue
        pair = _INI_PAIR.match(line)
        if pair is None:
            return None, _("Line {line}: {text}").format(line=number, text=line)
        _add(data if section is None else section, pair[1].strip(), pair[2].strip())
    return data, None


def parse_env(text):
    """KEY=value lines, optionally with "export", quotes and # comments after the value."""
    values = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _sep, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if value[:1] in ("'", '"'):
            closing = value.find(value[0], 1)
            value = value[1:closing] if closing > 0 else value[1:]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


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


PLAIN_NODE_LIMIT = 200_000


class TooLarge(ValueError):
    pass


def _plain(value, _stack=None, _budget=None):
    """YAML/TOML values that JSON cannot carry (dates, sets, bytes…) become text.

    YAML aliases can point back at their parents (a: &x [*x]) or multiply into billions of nodes,
    so cycles are cut and the total number of nodes is capped."""
    stack = set() if _stack is None else _stack
    budget = [PLAIN_NODE_LIMIT] if _budget is None else _budget
    budget[0] -= 1
    if budget[0] < 0:
        raise TooLarge(_("The document is too large or has too many repeated aliases to preview."))
    if isinstance(value, (dict, list, tuple)):
        if id(value) in stack:
            return "*recursive*"
        stack.add(id(value))
        try:
            if isinstance(value, dict):
                return {str(key): _plain(child, stack, budget) for key, child in value.items()}
            return [_plain(child, stack, budget) for child in value]
        finally:
            stack.discard(id(value))
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
    try:
        return _plain(data), None
    except TooLarge as error:
        return None, str(error)


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
    try:
        rows = list(csv.reader(io.StringIO(text), dialect))
    except csv.Error as error:
        return {"kind": "table", "header": [], "rows": [], "total": 0, "error": _("Could not read the table: {error}").format(error=error)}
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


def _parse_json_preview(text, name):
    if json_allows_comments(name):
        return parse_json(text, allow_comments=True)
    data, error = parse_json(text)
    if error and len(text) < 200_000 and parse_json(text, allow_comments=True)[1] is None:
        # Valid JSONC: say why it is still an error here.
        error += " " + _("Comments and trailing commas are allowed only in .jsonc files.")
    return data, error


def preview_payload(kind, text, name=""):
    """Data for the preview page (preview.js)."""
    if kind == "markdown":
        html, toc, features = render_markdown(text)
        return {"kind": "markdown", "html": html, "toc": toc, "features": features}
    if kind in TREE_KINDS:
        if kind == "jsonl":
            return jsonl_payload(text)
        if kind == "xml":
            tree, error = xml_tree(text)
        else:
            parsers = {"json": lambda t: _parse_json_preview(t, name), "yaml": parse_yaml, "toml": parse_toml,
                       "ini": lambda t: parse_ini(t, name)}
            data, error = parsers[kind](text)
            tree = None if error else to_tree(data)
        label = {"json": "JSON", "yaml": "YAML", "toml": "TOML", "xml": "XML", "ini": "INI"}[kind]
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
