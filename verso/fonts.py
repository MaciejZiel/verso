"""Flatpak: fontconfig without the host's font caches, and the font caches shared with the preview.

The runtime reads the host's font caches from /run/host/*fonts-cache. Some host programs (snaps,
another fontconfig version) leave caches there that the runtime accepts but that do not match the
fonts: font matching goes wrong and the WebKit web process loops forever, the preview stays blank.
Verso writes the runtime's own configuration without those cache directories at each start, so it
follows runtime updates, and points FONTCONFIG_FILE at it. The host fonts are still used.
"""

import glob
import os
import sys
import xml.etree.ElementTree as ET

from gi.repository import GLib, PangoCairo

HOST_PREFIX = "/run/host/"


def in_flatpak():
    return os.path.exists("/.flatpak-info")


def _drop_host_caches(root):
    """Removes <cachedir> elements under /run/host from a parsed configuration; True if any were."""
    found = [el for el in root.iter("cachedir") if (el.text or "").strip().startswith(HOST_PREFIX)]
    for parent in root.iter():
        for el in list(parent):
            if el in found:
                parent.remove(el)
    return bool(found)


def write_config(dest, etc="/etc/fonts"):
    """Writes dest/fonts.conf: etc/fonts.conf with its conf.d directory listed file by file (in
    fontconfig's order, [0-9]*.conf) and, for files naming host caches, filtered copies. Returns the path."""
    os.makedirs(dest, exist_ok=True)
    tree = ET.parse(os.path.join(etc, "fonts.conf"))
    root = tree.getroot()
    _drop_host_caches(root)
    for index, el in enumerate(list(root)):
        if el.tag != "include" or (el.text or "").strip() != "conf.d":
            continue
        root.remove(el)
        for offset, path in enumerate(sorted(glob.glob(os.path.join(etc, "conf.d", "[0-9]*.conf")))):
            try:
                part = ET.parse(path)
            except (ET.ParseError, OSError):
                continue  # fontconfig would skip or reject it as well
            if _drop_host_caches(part.getroot()):
                path = os.path.join(dest, os.path.basename(path))
                part.write(path, encoding="utf-8", xml_declaration=True)
            include = ET.Element("include", {"ignore_missing": "yes"})
            include.text = path
            root.insert(index + offset, include)
        break
    else:
        raise ValueError(f"{etc}/fonts.conf does not include conf.d")
    target = os.path.join(dest, "fonts.conf")
    tree.write(target, encoding="utf-8", xml_declaration=True)
    return target


def _config_dir():
    return os.path.join(GLib.get_user_cache_dir(), "fontconfig-conf")


def _cache_dir():
    return os.path.join(GLib.get_user_cache_dir(), "fontconfig")  # the configuration's xdg cachedir


def setup():
    """At start, before anything has used fontconfig: in the Flatpak, use the configuration above."""
    if not in_flatpak():
        return
    try:
        os.environ["FONTCONFIG_FILE"] = write_config(_config_dir())
    except (OSError, ET.ParseError, ValueError) as error:
        print(f"Verso: keeping the runtime's font configuration: {error}", file=sys.stderr)


def share_with_web_process(context):
    """In the Flatpak the web process runs in a sandbox of its own that sees neither the configuration
    nor the app's font cache: without the cache it would scan every font at each start. Fills the
    cache first (the app process can write it, the web process cannot) and shares both read-only.
    Has to happen before the context starts its first web process."""
    if not in_flatpak() or "FONTCONFIG_FILE" not in os.environ:
        return
    PangoCairo.FontMap.get_default().list_families()  # scans the fonts missing from the cache
    for path in (_config_dir(), _cache_dir()):
        if os.path.isdir(path):
            context.add_path_to_sandbox(path, True)
