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


def in_literal_block(lines):
    """True when the line after these lines is inside a fenced code block or the front matter,
    where list markers are just text."""
    fence = None
    front = bool(lines) and lines[0].rstrip() == "---"
    for index, line in enumerate(lines):
        if front:
            if index > 0 and line.rstrip() in ("---", "..."):
                front = False
            continue
        match = _FENCE.match(line)
        if match is None:
            continue
        mark = match.group(1)
        if fence is None:
            fence = mark
        elif mark[0] == fence[0] and len(mark) >= len(fence) and not line.strip().strip(mark[0]):
            fence = None
    return front or fence is not None


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
