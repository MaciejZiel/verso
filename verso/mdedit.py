"""Markdown writing helpers without GTK: list continuation, inline formatting, links from pasted URLs."""

import re

# Indent, then a bullet or a number with its delimiter, an optional task box, then the text.
_ITEM = re.compile(r"^(?P<indent>[ \t]*)(?:(?P<bullet>[-*+])|(?P<number>\d{1,9})(?P<delim>[.)]))"
                   r"(?P<space>[ \t]+)(?P<task>\[[ xX]\][ \t]+)?(?P<text>.*)$")
_QUOTE = re.compile(r"^(?P<prefix>[ \t]*(?:>[ \t]?)+)(?P<text>.*)$")
_URL = re.compile(r"^(?:https?|ftp|mailto):\S+$|^www\.\S+\.\S+$")
_RULE = re.compile(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")


def continue_line(line):
    """What Enter at the end of this line should do in a list or quote:
    (None, None) for an ordinary line, ("end", None) to remove an empty item's marker,
    or ("insert", prefix) to start the next line with prefix."""
    quote = _QUOTE.match(line)
    prefix, rest = (quote["prefix"], quote["text"]) if quote else ("", line)
    if _RULE.match(rest):
        return (None, None)  # a thematic break such as "* * *", not a list item
    item = _ITEM.match(rest)
    if item:
        if not item["text"].strip():
            return ("end", None)
        if item["number"]:
            marker = f"{int(item['number']) + 1}{item['delim']}"
        else:
            marker = item["bullet"]
        task = "[ ] " if item["task"] else ""
        return ("insert", f"{prefix}{item['indent']}{marker}{item['space']}{task}")
    if quote:
        if not rest.strip():
            return ("end", None)
        return ("insert", prefix)
    return (None, None)


def literal_lines(lines):
    """For each line, whether it is inside a fenced code block or the front matter (one pass)."""
    flags = []
    fence = None
    front = bool(lines) and lines[0].rstrip() == "---"
    for index, line in enumerate(lines):
        if front:
            flags.append(True)
            if index > 0 and line.rstrip() in ("---", "..."):
                front = False
            continue
        match = _FENCE.match(line)
        flags.append(fence is not None or match is not None)
        if match is None:
            continue
        mark = match.group(1)
        if fence is None:
            fence = mark
        elif mark[0] == fence[0] and len(mark) >= len(fence) and not line.strip().strip(mark[0]):
            fence = None
    return flags


def in_literal_block(lines):
    """True when the line after these lines is inside a fenced code block or the front matter,
    where list markers are just text."""
    return bool(lines) and literal_lines(lines + [""])[-1]


def toggle_wrap(text, marker):
    """Wraps text in marker (** for bold, * or _ for italic, ` for code), or unwraps it when it
    already is. A single * does not mistake bold (**x**) for italic: ***x*** is both."""
    char = marker[0]
    run = min(len(text) - len(text.lstrip(char)), len(text) - len(text.rstrip(char)))
    if len(text) > 2 * len(marker) and run >= len(marker):
        wrapped = run % 2 == 1 if len(marker) == 1 and char in "*_" else True
        if wrapped:
            return text[len(marker):-len(marker)]
    return f"{marker}{text}{marker}"


def is_url(text):
    return bool(_URL.match(text.strip()))


def escape_label(text):
    """Brackets in a link's text would end or nest the label."""
    return re.sub(r"([\[\]])", r"\\\1", text)


def link(text, url):
    return f"[{escape_label(text)}]({url.strip()})"


# ---------- tables ----------

_DELIMITER_CELL = re.compile(r"^:?-+:?$")


def split_row(line):
    """Cells of a table row: outer pipes removed, escaped pipes (\\|) kept inside cells."""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", text)]


def _is_delimiter(cells):
    return bool(cells) and all(_DELIMITER_CELL.match(cell) for cell in cells)


def is_table_start(lines, index):
    """A header row followed by a delimiter row with as many cells (| --- | :---: |), as in GFM."""
    if index + 1 >= len(lines) or "|" not in lines[index] or "|" not in lines[index + 1]:
        return False
    delimiter = split_row(lines[index + 1])
    return _is_delimiter(delimiter) and len(delimiter) == len(split_row(lines[index]))


def _indented_code(lines, index):
    """Four spaces or a tab after a blank line start an indented code block, not a table."""
    line = lines[index]
    indent = len(line) - len(line.lstrip(" "))
    return (indent >= 4 or line.startswith("\t")) and (index == 0 or not lines[index - 1].strip())


def find_tables(lines):
    """(start, end) line ranges, end exclusive, of the tables outside code blocks and front matter."""
    literal = literal_lines(lines)
    tables, index = [], 0
    while index < len(lines):
        if not literal[index] and is_table_start(lines, index) and not _indented_code(lines, index):
            end = index + 2
            while end < len(lines) and "|" in lines[end] and lines[end].strip():
                end += 1
            tables.append((index, end))
            index = end
        else:
            index += 1
    return tables


def _width(text):
    import unicodedata

    return sum(0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1
               for char in text)


def format_table(lines):
    """The table's lines with aligned columns; the delimiter row keeps each column's alignment.
    Cells past the header's count (GFM ignores them) stay at the end of their row as they were."""
    indent = lines[0][: len(lines[0]) - len(lines[0].lstrip())]
    rows = [split_row(line) for line in lines]
    count = len(rows[0])
    extra = [row[count:] for row in rows]
    rows = [row[:count] + [""] * (count - len(row)) for row in rows]
    aligns = []
    for cell in rows[1]:
        left, right = cell.startswith(":"), cell.endswith(":") and len(cell) > 1
        aligns.append("center" if left and right else "right" if right else "left" if left else None)
    widths = [max([3] + [_width(row[col]) for i, row in enumerate(rows) if i != 1]) for col in range(count)]

    def pad(text, width, align):
        space = width - _width(text)
        if align == "right":
            return " " * space + text
        if align == "center":
            return " " * (space // 2) + text + " " * (space - space // 2)
        return text + " " * space

    def delimiter(width, align):
        dashes = "-" * width
        if align == "center":
            return ":" + dashes[2:] + ":"
        if align == "right":
            return dashes[1:] + ":"
        if align == "left":
            return ":" + dashes[1:]
        return dashes

    result = []
    for i, row in enumerate(rows):
        cells = [delimiter(w, a) if i == 1 else pad(c, w, a) for c, w, a in zip(row, widths, aligns)]
        result.append(f"{indent}| " + " | ".join(cells + extra[i]) + " |")
    return result


def _cell_bounds(line):
    """(start, end) of each cell's span between the row's pipes (outer pipes excluded)."""
    pipes = [match.start() for match in re.finditer(r"(?<!\\)\|", line)]
    edges = [-1] + pipes + [len(line)]
    spans = [(edges[i] + 1, edges[i + 1]) for i in range(len(edges) - 1)]
    if pipes and not line[: pipes[0]].strip():
        spans = spans[1:]  # nothing before a leading pipe
    if pipes and len(spans) > 1 and not line[pipes[-1] + 1 :].strip():
        spans = spans[:-1]  # nor after a trailing one
    return spans


def cell_at(line, column):
    """(cell index, characters of the cell's text before the column) for a column in a table row."""
    for index, (start, end) in enumerate(_cell_bounds(line)):
        if column <= end:
            return index, len(line[start:max(start, column)].lstrip())
    return max(len(_cell_bounds(line)) - 1, 0), 0


def column_of(line, cell, offset):
    """The column offset characters into the text of a cell: the inverse of cell_at."""
    spans = _cell_bounds(line)
    if not spans:
        return len(line)
    start, end = spans[min(cell, len(spans) - 1)]
    span = line[start:end]
    text_start = start + len(span) - len(span.lstrip())
    text_end = start + len(span.rstrip())
    return min(text_start + offset, max(text_start, text_end))
