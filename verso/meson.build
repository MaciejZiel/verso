conf = configuration_data()
conf.set('PYTHON', py.full_path())
conf.set('VERSION', meson.project_version())
conf.set('pkgdatadir', pkgdatadir)
conf.set('localedir', localedir)

configure_file(
  input: 'verso.in',
  output: 'verso',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

install_data(
  '__init__.py',
  '__main__.py',
  'app.py',
  'document.py',
  'fonts.py',
  'md.py',
  'mdedit.py',
  'outline.py',
  'preferences.py',
  'render.py',
  'session.py',
  'settings.py',
  'statusbar.py',
  'window.py',
  install_dir: moduledir,
)

install_data(
  'data/preview.css',
  'data/preview.js',
  install_dir: moduledir / 'data',
)

install_subdir(
  'data/vendor',
  install_dir: moduledir / 'data',
)

# Every module in verso/ has to be in the list above; a missing one breaks only the installed app.
test(
  'install-list-complete',
  py,
  args: [meson.project_source_root() / 'tools' / 'check_install_list.py', meson.current_source_dir()],
)
