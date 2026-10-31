project(
  'verso',
  version: '0.9.0',
  meson_version: '>= 1.0.0',
  license: 'MIT',
)

i18n = import('i18n')
gnome = import('gnome')
python = import('python')

app_id = 'io.github.maciejziel.Verso'
py = python.find_installation('python3')

prefix = get_option('prefix')
pkgdatadir = prefix / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'verso'
localedir = prefix / get_option('localedir')

subdir('data')
subdir('verso')
subdir('po')

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
