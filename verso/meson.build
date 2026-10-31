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
  'app.py',
  'document.py',
  'render.py',
  'settings.py',
  'preferences.py',
  'window.py',
  install_dir: moduledir,
)

install_data(
  'data/preview.css',
  'data/preview.js',
  install_dir: moduledir / 'data',
)
