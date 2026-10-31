"""Shared Gio.Settings instance for the application schema."""

from gi.repository import Gio

APP_ID = "io.github.maciejziel.Verso"
_settings = None


def get():
    global _settings
    if _settings is None:
        source = Gio.SettingsSchemaSource.get_default()
        if source is None or source.lookup(APP_ID, True) is None:
            # Gio.Settings.new would abort the whole process; fail with a readable message instead.
            raise RuntimeError(f"GSettings schema {APP_ID} is not installed; run Verso via bin/verso or install it with Meson.")
        _settings = Gio.Settings.new(APP_ID)
    return _settings
