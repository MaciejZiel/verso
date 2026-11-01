""".editorconfig support (https://editorconfig.org): the indentation a project asks for.

The files are read from the file's folder upwards until one says root = true; closer files and later
sections win. Verso applies indent_style, indent_size and tab_width; a choice made in the status bar
still wins over them.
"""

import os
import re

from .render import parse_ini


def _glob_regex(pattern):
    """EditorConfig globs: * (not /), ** (anything), ?, [seq], [!seq], {a,b} and {1..10}."""
    out, i, depth = [], 0, 0
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
                out.append(("[^" if negate else "[") + body.replace("\\", "\\\\") + "]")
                i = end
        elif char == "{":
            end = pattern.find("}", i + 1)
            numbers = re.fullmatch(r"([+-]?\d+)\.\.([+-]?\d+)", pattern[i + 1 : end]) if end > 0 else None
            if numbers:
                low, high = sorted((int(numbers[1]), int(numbers[2])))
                out.append("(?:" + "|".join(str(n) for n in range(low, high + 1)) + ")")
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
    return "".join(out)


def matches(section, path, folder):
    """Whether a [section] glob of the .editorconfig in folder applies to path."""
    if "/" in section:
        glob = section.lstrip("/")
        target = os.path.relpath(path, folder)
    else:
        glob, target = section, os.path.basename(path)  # a plain name matches in any folder
    try:
        return re.fullmatch(_glob_regex(glob), target) is not None
    except re.error:
        return False


def properties(path):
    """The EditorConfig properties for a file path, keys and values lowercased."""
    files = []
    folder = os.path.dirname(os.path.abspath(path))
    while True:
        candidate = os.path.join(folder, ".editorconfig")
        try:
            with open(candidate, encoding="utf-8") as handle:
                data, error = parse_ini(handle.read(), ".editorconfig")
        except (OSError, UnicodeDecodeError):
            data, error = None, None
        if data and not error:
            files.append((folder, data))
            if str(data.get("root", "")).lower() == "true":
                break
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    result = {}
    for folder, data in reversed(files):  # the farthest first, so closer files win
        for section, values in data.items():
            if isinstance(values, dict) and matches(section, os.path.abspath(path), folder):
                for key, value in values.items():
                    value = value[-1] if isinstance(value, list) else value
                    result[key.lower()] = value.lower()
    return result


def indentation(props):
    """(insert_spaces, width) from EditorConfig properties; either may be None when not set."""
    style = props.get("indent_style")
    spaces = {"space": True, "tab": False}.get(style)
    size = props.get("indent_size")
    width = None
    if size == "tab":
        size = props.get("tab_width")
    if size and size.isdigit() and 0 < int(size) <= 32:
        width = int(size)
    elif props.get("tab_width", "").isdigit() and 0 < int(props["tab_width"]) <= 32:
        width = int(props["tab_width"])
    return spaces, width
