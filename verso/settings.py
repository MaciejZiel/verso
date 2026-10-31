"""Shared Gio.Settings instance for the application schema."""

from gi.repository import Gio

APP_ID = "io.github.maciejziel.Verso"
MODES = ("edit", "split", "preview")  # also the <choices> of the default-mode-* keys
_settings = None


def schema_exists(schema_id):
    """Gio.Settings.new aborts the process for a missing schema, so check before creating one."""
    source = Gio.SettingsSchemaSource.get_default()
    return source is not None and source.lookup(schema_id, True) is not None


def get():
    global _settings
    if _settings is None:
        if not schema_exists(APP_ID):
            raise RuntimeError(f"GSettings schema {APP_ID} is not installed; run Verso via bin/verso or install it with Meson.")
        _settings = Gio.Settings.new(APP_ID)
    return _settings
