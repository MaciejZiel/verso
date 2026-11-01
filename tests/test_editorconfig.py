from verso.editorconfig import _glob_regex, indentation, matches, properties


def test_globs():
    import re

    def ok(glob, name):
        return re.fullmatch(_glob_regex(glob), name) is not None

    assert ok("*.py", "a.py") and not ok("*.py", "dir/a.py")
    assert ok("**.py", "dir/a.py")
    assert ok("*.{js,ts}", "a.ts") and not ok("*.{js,ts}", "a.py")
    assert ok("file{1..3}.txt", "file2.txt") and not ok("file{1..3}.txt", "file4.txt")
    assert ok("[Mm]akefile", "makefile") and ok("[!a]bc", "xbc") and not ok("[!a]bc", "abc")
    assert ok("a?c", "abc") and ok("\\*", "*")


def test_matches_relative_to_its_folder():
    assert matches("*.md", "/p/docs/x.md", "/p")  # a plain name matches in any folder
    assert matches("docs/*.md", "/p/docs/x.md", "/p") and not matches("docs/*.md", "/p/other/x.md", "/p")
    assert matches("/docs/**", "/p/docs/a/b.md", "/p")


def test_properties_closer_files_and_later_sections_win(tmp_path):
    (tmp_path / ".editorconfig").write_text("root = true\n[*]\nindent_style = space\nindent_size = 4\n[*.go]\nindent_style = tab\n")
    sub = tmp_path / "web"
    sub.mkdir()
    (sub / ".editorconfig").write_text("[*.js]\nindent_size = 2\n")
    assert properties(str(sub / "app.js")) == {"indent_style": "space", "indent_size": "2"}
    assert properties(str(tmp_path / "main.go"))["indent_style"] == "tab"
    outside = tmp_path / "x.txt"
    assert properties(str(outside)) == {"indent_style": "space", "indent_size": "4"}


def test_root_stops_the_search(tmp_path):
    (tmp_path / ".editorconfig").write_text("[*]\nindent_size = 8\n")
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / ".editorconfig").write_text("root = true\n[*.md]\nindent_style = space\n")
    assert properties(str(inner / "a.md")) == {"indent_style": "space"}


def test_indentation():
    assert indentation({"indent_style": "tab", "indent_size": "tab", "tab_width": "8"}) == (False, 8)
    assert indentation({"indent_style": "space", "indent_size": "2"}) == (True, 2)
    assert indentation({"tab_width": "4"}) == (None, 4)
    assert indentation({"indent_size": "999"}) == (None, None)
    assert indentation({}) == (None, None)
