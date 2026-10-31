"""Verso: a GNOME text editor with live previews."""

import gi

# One place for all typelib versions, so any module can be imported first (tests import single modules).
for _namespace, _version in (("Gtk", "4.0"), ("Gdk", "4.0"), ("Adw", "1"), ("GtkSource", "5"), ("WebKit", "6.0"), ("Spelling", "1")):
    gi.require_version(_namespace, _version)
