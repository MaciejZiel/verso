from verso.document import Document


def test_utf8_and_bom():
    assert Document._decode("żółć".encode()) == ("żółć", "utf-8")
    assert Document._decode(b"\xef\xbb\xbfabc") == ("abc", "utf-8")


def test_cp1250_is_not_mistaken_for_utf16():
    text = "Zażółć gęślą jaźń!"
    assert Document._decode(text.encode("cp1250")) == (text, "Windows-1250")


def test_utf16_with_bom():
    assert Document._decode("abc".encode("utf-16")) == ("abc", "UTF-16")
