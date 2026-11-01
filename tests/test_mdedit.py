from verso.mdedit import continue_line, escape_label, in_literal_block, is_url, link, toggle_wrap


def test_continue_bullets_numbers_tasks_quotes():
    assert continue_line("- jabłka") == ("insert", "- ")
    assert continue_line("  * nested") == ("insert", "  * ")
    assert continue_line("9. dziewięć") == ("insert", "10. ")
    assert continue_line("3) trzy") == ("insert", "4) ")
    assert continue_line("- [x] done") == ("insert", "- [ ] ")
    assert continue_line("> cytat") == ("insert", "> ")
    assert continue_line("> > deeper") == ("insert", "> > ")
    assert continue_line("> - quoted item") == ("insert", "> - ")


def test_continue_ends_empty_items():
    assert continue_line("- ") == ("end", None)
    assert continue_line("2. ") == ("end", None)
    assert continue_line("- [ ] ") == ("end", None)
    assert continue_line("> ") == ("end", None)


def test_continue_ignores_ordinary_lines():
    assert continue_line("zwykły tekst") == (None, None)
    assert continue_line("-nie lista") == (None, None)
    assert continue_line("---") == (None, None)
    assert continue_line("* * *") == (None, None)
    assert continue_line("- - -") == (None, None)
    assert continue_line("2026. rok") == ("insert", "2027. ")  # CommonMark reads this as a list item too
    assert continue_line("") == (None, None)


def test_toggle_wrap():
    assert toggle_wrap("słowo", "**") == "**słowo**"
    assert toggle_wrap("**słowo**", "**") == "słowo"
    assert toggle_wrap("", "_") == "__"
    assert toggle_wrap("**bold**", "*") == "***bold***"
    assert toggle_wrap("***both***", "*") == "**both**"
    assert toggle_wrap("*it*", "*") == "it"
    assert toggle_wrap("`kod`", "`") == "kod"


def test_links():
    assert is_url("https://example.com/a?b=1")
    assert is_url("  www.gnome.org ")
    assert not is_url("to nie jest url")
    assert not is_url("https://a b")
    assert link("GNOME", " https://gnome.org ") == "[GNOME](https://gnome.org)"


def test_literal_blocks():
    assert in_literal_block(["text", "```diff", "- removed"])
    assert not in_literal_block(["```", "code", "```", "- item"])
    assert in_literal_block(["~~~~", "~~~", "still code"])  # a shorter fence does not close
    assert not in_literal_block(["````", "````", "after"])
    assert in_literal_block(["---", "tags:", "  - a"])
    assert not in_literal_block(["---", "title: x", "---", "- item"])
    assert not in_literal_block([])


def test_escape_label():
    assert escape_label("see [1] here") == "see \\[1\\] here"
    assert link("a [b]", "https://x.org") == "[a \\[b\\]](https://x.org)"


def test_format_table():
    from verso.mdedit import find_tables, format_table, split_row

    table = ["| Item | Per person|Notes |", "|---|---:|:-:|", "|Flights|180 €| return |", "|Żółć|5|中文|", "| a \\| b |"]
    assert format_table(table) == [
        "| Item    | Per person | Notes  |",
        "| ------- | ---------: | :----: |",
        "| Flights |      180 € | return |",
        "| Żółć    |          5 |  中文  |",
        "| a \\| b  |            |        |",
    ]
    assert format_table(format_table(table)) == format_table(table)  # stable
    assert format_table(["  a|b", "  -|-"]) == ["  | a   | b   |", "  | --- | --- |"]  # keeps the indent
    assert split_row("|a|b\\|c|") == ["a", "b\\|c"]


def test_find_tables():
    from verso.mdedit import find_tables

    lines = ["text", "|a|b|", "|-|-|", "|1|2|", "", "```", "|a|b|", "|-|-|", "```", "x | y", "--|--"]
    assert find_tables(lines) == [(1, 4), (9, 11)]
    assert find_tables(["a | b", "not a delimiter"]) == []
    assert find_tables(["a | b | c", "--|--"]) == []  # GFM: the delimiter row needs as many cells
    assert find_tables(["", "    x | y", "    --|--"]) == []  # indented code
    assert find_tables(["- item", "    x | y", "    --|--"]) == [(1, 3)]  # a table in a list item


def test_table_cells_and_cursor():
    from verso.mdedit import cell_at, column_of, format_table

    assert format_table(["|a|b|", "|-|-|", "|1|2|3|"])[2] == "| 1   | 2   | 3 |"  # extra cells stay
    line = "|ccc|1|"
    formatted = format_table(["|a|bb|", "|-|-:|", line])[2]
    assert cell_at(line, 5) == (1, 0) and formatted[column_of(formatted, 1, 0)] == "1"
    assert cell_at("| a   |  bb |", 10) == (1, 1) and column_of("| a   |  bb |", 1, 1) == 10
