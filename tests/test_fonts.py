import xml.etree.ElementTree as ET

import pytest

from verso.fonts import write_config

FLATPAK_CONF = """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
        <cachedir>/usr/lib/fontconfig/cache</cachedir>
        <dir>/run/host/fonts</dir>
        <cachedir prefix="xdg">fontconfig</cachedir>
        <cachedir>/run/host/fonts-cache</cachedir><cachedir>/run/host/user-fonts-cache</cachedir>
</fontconfig>
"""


def make_etc(tmp_path, main_include="conf.d"):
    etc = tmp_path / "etc"
    (etc / "conf.d").mkdir(parents=True)
    (etc / "fonts.conf").write_text(
        f'<?xml version="1.0"?>\n<fontconfig>\n<dir>/usr/share/fonts</dir>\n'
        f'<include ignore_missing="yes">{main_include}</include>\n<cachedir>~/.fontconfig</cachedir>\n</fontconfig>\n'
    )
    (etc / "conf.d" / "50-flatpak.conf").write_text(FLATPAK_CONF)
    (etc / "conf.d" / "10-hinting.conf").write_text("<fontconfig/>")
    (etc / "conf.d" / "README").write_text("not a config")
    (etc / "conf.d" / "local.conf").write_text("<fontconfig/>")  # fontconfig only loads [0-9]*.conf
    return etc


def test_write_config_drops_host_caches_and_keeps_order(tmp_path):
    etc = make_etc(tmp_path)
    target = write_config(str(tmp_path / "out"), str(etc))
    root = ET.parse(target).getroot()
    assert [el.tag for el in root] == ["dir", "include", "include", "cachedir"]
    includes = [el.text for el in root.iter("include")]
    assert includes == [str(etc / "conf.d" / "10-hinting.conf"), str(tmp_path / "out" / "50-flatpak.conf")]
    flatpak = ET.parse(tmp_path / "out" / "50-flatpak.conf").getroot()
    assert [el.text for el in flatpak.iter("cachedir")] == ["/usr/lib/fontconfig/cache", "fontconfig"]
    assert [el.text for el in flatpak.iter("dir")] == ["/run/host/fonts"]  # host fonts are still used


def test_write_config_requires_conf_d(tmp_path):
    with pytest.raises(ValueError):
        write_config(str(tmp_path / "out"), str(make_etc(tmp_path, main_include="other.d")))
