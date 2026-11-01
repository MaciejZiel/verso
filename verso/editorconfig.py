""".editorconfig support (https://editorconfig.org): the indentation a project asks for.

The files are read from the file's folder upwards until one says root = true; closer files and later
sections win. Verso applies indent_style, indent_size and tab_width; a choice made in the status bar
still wins over them.
"""

import os
import re

MAX_WIDTH = 16  # the status bar and the tab-width preference go up to 16


def parse(text):
    """[(section, {key: value})] in file order; keys before any section go under None.

    Lines that are neither a section, a key = value pair nor a comment are skipped, as the spec says.
    """
    sections, current = [(None, {})], None
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            current = {}
            sections.append((line[1:-1], current))
        elif "=" in line:
            key, value = (part.strip() for part in line.split("=", 1))
            if key:
                (current if current is not None else sections[0][1])[key.lower()] = value
    return sections


def _glob_regex(pattern):
    """(regex, ranges) for an EditorConfig glob: * (not /), ** (anything), ?, [seq], [!seq], {a,b}, {1..10}.

    Each {low..high} becomes a capture group; ranges lists their bounds in group order.
    """
    out, ranges, i, depth = [], [], 0, 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if char == "*":
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i + 1)
            if end < 0:
                out.append(r"\[")
            else:
                body = pattern[i + 1 : end]
                negate = body.startswith("!")
                body = body[1:] if negate else body
                body = "\\^" + body[1:] if body.startswith("^") else body  # only ! negates here
                out.append(("[^" if negate else "[") + body.replace("\\", "\\\\") + "]")
                i = end
        elif char == "{":
            end = pattern.find("}", i + 1)
            numbers = re.fullmatch(r"([+-]?\d+)\.\.([+-]?\d+)", pattern[i + 1 : end]) if end > 0 else None
            if numbers:
                ranges.append(sorted((int(numbers[1]), int(numbers[2]))))
                out.append(r"([+-]?\d+)")  # checked against the range after matching
                i = end
            elif end > 0 and "," in pattern[i + 1 : end]:
                out.append("(?:")
                depth += 1
            else:
                out.append(r"\{")
        elif char == "," and depth:
            out.append("|")
        elif char == "}" and depth:
            out.append(")")
            depth -= 1
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out), ranges


def glob_match(glob, target):
    try:
        regex, ranges = _glob_regex(glob)
        found = re.fullmatch(regex, target)
    except re.error:
        return False
    if found is None:
        return False
    for number, (low, high) in zip(found.groups(), ranges):
        if number is not None and not low <= int(number) <= high:
            return False
    return True


def matches(section, path, folder):
    """Whether a [section] glob of the .editorconfig in folder applies to path."""
    if "/" in section:
        glob = section.lstrip("/")
        target = os.path.relpath(path, folder)
    else:
        glob, target = section, os.path.basename(path)  # a plain name matches in any folder
    return glob_match(glob, target)


def properties(path):
    """The EditorConfig properties for a file path, keys and values lowercased."""
    files = []
    folder = os.path.dirname(os.path.abspath(path))
    while True:
        candidate = os.path.join(folder, ".editorconfig")
        try:
            with open(candidate, encoding="utf-8-sig") as handle:  # -sig: a BOM must not hide "root"
                sections = parse(handle.read())
        except (OSError, UnicodeDecodeError):
            sections = None
        if sections:
            files.append((folder, sections))
            if sections[0][1].get("root", "").lower() == "true":
                break
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    result = {}
    for folder, sections in reversed(files):  # the farthest first, so closer files win
        for section, values in sections:
            if section is not None and matches(section, os.path.abspath(path), folder):
                result.update((key, value.lower()) for key, value in values.items())
    return result


def indentation(props):
    """(insert_spaces, width) from EditorConfig properties; either may be None when not set."""
    style = props.get("indent_style")
    spaces = {"space": True, "tab": False}.get(style)
    def number(key):
        value = props.get(key, "")
        return int(value) if value.isdigit() and 0 < int(value) <= MAX_WIDTH else None

    tab_width = number("tab_width")
    size = tab_width if props.get("indent_size") == "tab" else number("indent_size")
    # Verso shows one width: for tabs the width a tab is drawn with, for spaces the indent size.
    width = (tab_width or size) if spaces is False else (size or tab_width)
    return spaces, width
