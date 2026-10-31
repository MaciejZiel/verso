"""Markdown → HTML with markdown-it-py.

On top of CommonMark: tables, strikethrough, autolinks, task lists, heading anchors with a table
of contents, YAML front matter, math ($…$, $$…$$) for KaTeX, ```mermaid diagrams, highlighted code.
Every block element carries data-line (its first source line) for synchronized scrolling."""

import html
import re
import unicodedata

from markdown_it import MarkdownIt
from markdown_it.token import Token
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

_FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.S)
_TASK = re.compile(r"^\[([ xX])\]\s")
_FORMATTER = HtmlFormatter(nowrap=True)


# ---------- math ----------

def _math_inline(state, silent):
    """$x$ — not "$ x$", not "$5 and $6" (a digit right after the closing $ means currency)."""
    pos = state.pos
    if state.src[pos] != "$":
        return False
    if state.src.startswith("$$", pos):
        end = state.src.find("$$", pos + 2)
        if end == -1:
            return False
        content, after = state.src[pos + 2 : end], end + 2
        display = True
    else:
        if pos + 1 >= state.posMax or state.src[pos + 1].isspace():
            return False
        end = pos + 1
        while True:
            end = state.src.find("$", end)
            if end == -1:
                return False
            if state.src[end - 1] == "\\":
                end += 1
                continue
            break
        content, after = state.src[pos + 1 : end], end + 1
        if not content or content[-1].isspace() or (after < len(state.src) and state.src[after].isdigit()):
            return False
        display = False
    if not silent:
        token = state.push("math_inline", "", 0)
        token.content = content
        token.meta = {"display": display}
    state.pos = after
    return True


def _math_block(state, start_line, end_line, silent):
    begin = state.bMarks[start_line] + state.tShift[start_line]
    first = state.src[begin : state.eMarks[start_line]]
    if not first.startswith("$$"):
        return False
    rest = first[2:]
    if rest.rstrip().endswith("$$") and len(rest.rstrip()) >= 2:
        content, last = rest.rstrip()[:-2], start_line  # $$ … $$ on one line
    else:
        lines, line = [rest], start_line + 1
        while line < end_line:
            text = state.src[state.bMarks[line] + state.tShift[line] : state.eMarks[line]]
            if text.rstrip().endswith("$$"):
                lines.append(text.rstrip()[:-2])
                break
            lines.append(text)
            line += 1
        else:
            return False
        content, last = "\n".join(lines), line
    if silent:
        return True
    token = state.push("math_block", "div", 0)
    token.block = True
    token.content = content.strip()
    token.map = [start_line, last + 1]
    state.line = last + 1
    return True


# ---------- rendering ----------

def _slug(text, used):
    text = unicodedata.normalize("NFKC", text).strip().lower()
    slug = re.sub(r"[^\w\- ]", "", text).replace(" ", "-") or "section"
    candidate, n = slug, 1
    while candidate in used:
        candidate = f"{slug}-{n}"
        n += 1
    used.add(candidate)
    return candidate


def _line_attr(token):
    return f' data-line="{token.map[0]}"' if token.map else ""


def _render_fence(self, tokens, idx, options, env):
    token = tokens[idx]
    lang = (token.info or "").strip().split()[0] if token.info else ""
    code = token.content
    if lang.lower() == "mermaid":
        return f'<div class="mermaid-diagram"{_line_attr(token)}><pre class="mermaid">{html.escape(code)}</pre></div>\n'
    try:
        lexer = get_lexer_by_name(lang) if lang else None
    except ClassNotFound:
        lexer = None
    body = highlight(code, lexer, _FORMATTER) if lexer else html.escape(code)
    label = f' data-lang="{html.escape(lang)}"' if lang else ""
    return f'<div class="highlight"{_line_attr(token)}{label}><pre><code>{body}</code></pre></div>\n'


def _render_math_inline(self, tokens, idx, options, env):
    token = tokens[idx]
    cls = "math math-display" if token.meta.get("display") else "math math-inline"
    return f'<span class="{cls}">{html.escape(token.content)}</span>'


def _render_math_block(self, tokens, idx, options, env):
    token = tokens[idx]
    return f'<div class="math math-display"{_line_attr(token)}>{html.escape(token.content)}</div>\n'


def _decorate(state):
    """Core rule: line numbers on blocks, heading ids + TOC, task list checkboxes."""
    tokens = state.tokens
    used, toc = set(), []
    for i, token in enumerate(tokens):
        if token.block and token.nesting == 1 and token.map:
            token.attrSet("data-line", str(token.map[0]))
        if token.type == "heading_open" and i + 1 < len(tokens):
            title = "".join(child.content for child in (tokens[i + 1].children or []) if child.type in ("text", "code_inline"))
            anchor = _slug(title, used)
            token.attrSet("id", anchor)
            toc.append((int(token.tag[1]), anchor, title))
        if token.type == "list_item_open" and i + 2 < len(tokens) and tokens[i + 2].type == "inline":
            inline = tokens[i + 2]
            match = _TASK.match(inline.content)
            if match and inline.children and inline.children[0].type == "text":
                checked = " checked" if match.group(1).lower() == "x" else ""
                first = inline.children[0]
                first.content = first.content[match.end():]
                box = Token("html_inline", "", 0)
                box.content = f'<input type="checkbox" disabled{checked}> '
                inline.children.insert(0, box)
                token.attrSet("class", "task-list-item")
    state.env["toc"] = toc


def _build():
    md = MarkdownIt("commonmark", {"html": True, "linkify": True, "typographer": False})
    md.enable(["table", "strikethrough", "linkify"])
    md.inline.ruler.before("escape", "math_inline", _math_inline)
    md.block.ruler.before("fence", "math_block", _math_block, {"alt": ["paragraph", "reference", "blockquote", "list"]})
    md.core.ruler.push("verso_decorate", _decorate)
    md.add_render_rule("fence", _render_fence)
    md.add_render_rule("code_block", _render_fence)
    md.add_render_rule("math_inline", _render_math_inline)
    md.add_render_rule("math_block", _render_math_block)
    return md


_MD = None


def _front_matter_html(text):
    try:
        import yaml

        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 — any YAML problem: show the raw block instead
        data = None
    if isinstance(data, dict) and data:
        rows = "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>" for key, value in data.items()
        )
        return f'<table class="front-matter" data-line="0">{rows}</table>\n'
    return f'<pre class="front-matter" data-line="0">{html.escape(text)}</pre>\n'


def render(text):
    """Returns (html, toc, features) — features tells the page which scripts it needs (math, mermaid)."""
    global _MD
    if _MD is None:
        _MD = _build()
    prefix, offset = "", 0
    match = _FRONT_MATTER.match(text)
    if match:
        prefix = _front_matter_html(match.group(1))
        # Keep line numbers aligned with the source: replace the block with empty lines.
        offset = match.group(0).count("\n")
        text = "\n" * offset + text[match.end():]
    env = {}
    body = _MD.render(text, env)
    features = {"math": 'class="math ' in body, "mermaid": 'class="mermaid"' in body}
    return prefix + body, env.get("toc", []), features
