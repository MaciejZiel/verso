from verso.editorconfig import glob_match, indentation, matches, parse, properties


def test_globs():
    ok = glob_match

    assert ok("*.py", "a.py") and not ok("*.py", "dir/a.py")
    assert ok("**.py", "dir/a.py")
    assert ok("*.{js,ts}", "a.ts") and not ok("*.{js,ts}", "a.py")
    assert ok("file{1..3}.txt", "file2.txt") and not ok("file{1..3}.txt", "file4.txt")
    assert ok("[Mm]akefile", "makefile") and ok("[!a]bc", "xbc") and not ok("[!a]bc", "abc")
    assert ok("a?c", "abc") and ok("\\*", "*")
    assert ok("[^a]bc", "^bc") and ok("[^a]bc", "abc") and not ok("[^a]bc", "xbc")  # ^ is literal
    assert ok("f{1..100000000}.txt", "f99999999.txt") and not ok("f{1..100000000}.txt", "f0.txt")
    assert ok("{a,f{-5..5}}.c", "f-3.c") and ok("{a,f{-5..5}}.c", "a.c") and not ok("{a,f{-5..5}}.c", "f6.c")


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
    other = tmp_path / "api"
    other.mkdir()
    assert properties(str(other / "app.js")) == {"indent_style": "space", "indent_size": "4"}  # web/ stays in web/


def test_parse_is_lenient(tmp_path):
    text = "\ufeffRoot = true\nstray text\n[*] # not a section\n[*]\nindent_size = 4\n[*.py]\nindent_size = 2\n[*]\nIndent_Size = 8\n"
    assert parse(text.lstrip("\ufeff"))[0] == (None, {"root": "true"})
    (tmp_path / ".editorconfig").write_text(text, encoding="utf-8")
    assert properties(str(tmp_path / "a.py")) == {"indent_size": "8"}  # the later [*] wins over [*.py]


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
    assert indentation({"indent_size": "20"}) == (None, None)  # beyond what the status bar can show
    assert indentation({"indent_style": "tab", "indent_size": "4", "tab_width": "8"}) == (False, 8)
    assert indentation({}) == (None, None)
