import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    from verso import session

    monkeypatch.setattr(session, "data_dir", lambda: tmp_path / "verso")
    return session


def test_draft_roundtrip(store):
    draft_id = store.new_draft_id()
    store.write_draft(draft_id, "Zażółć\njaźń")
    assert store.read_draft(draft_id) == "Zażółć\njaźń"
    store.delete_draft(draft_id)
    store.wait_for_writes()
    assert store.read_draft(draft_id) is None


def test_draft_id_cannot_escape_folder(store):
    assert store.read_draft("../../etc/passwd") is None
    with pytest.raises(ValueError):
        store.write_draft("../x", "nope")


def test_session_roundtrip_and_damaged_file(store):
    docs = [{"uri": "file:///a.md", "draft": None, "encoding": "UTF-8", "newline": "LF", "etag": "1:2"}]
    store.save_session(docs, 0)
    assert store.load_session() == {"documents": docs, "selected": 0}
    (store.data_dir() / "session.json").write_text("{not json")
    assert store.load_session() == {"documents": [], "selected": 0}


def test_prune_keeps_referenced(store):
    keep, drop = store.new_draft_id(), store.new_draft_id()
    store.write_draft(keep, "a")
    store.write_draft(drop, "b")
    store.prune_drafts({keep})
    assert store.read_draft(keep) == "a" and store.read_draft(drop) is None


def test_no_temp_files_left(store):
    store.save_session([], 0)
    leftovers = [p.name for p in store.data_dir().iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_async_write_then_delete_keeps_order(store):
    draft_id = store.new_draft_id()
    store.write_draft_async(draft_id, "x" * 100_000)
    store.delete_draft(draft_id)
    store.wait_for_writes()
    assert store.read_draft(draft_id) is None


@pytest.mark.parametrize("content", ["[]", "null", '{"documents": "x"}', '{"documents": [{"uri": 5}, 3, null]}', '{"documents": [], "selected": true}'])
def test_damaged_session_shapes(store, content):
    store.data_dir().mkdir(parents=True, exist_ok=True)
    (store.data_dir() / "session.json").write_text(content)
    assert store.load_session() == {"documents": [], "selected": 0}


def test_entry_fields_are_sanitized(store):
    store.data_dir().mkdir(parents=True, exist_ok=True)
    (store.data_dir() / "session.json").write_text('{"documents": [{"uri": "file:///a", "encoding": 7, "extra": 1}], "selected": 0}')
    assert store.load_session()["documents"] == [{"uri": "file:///a", "draft": None, "encoding": None, "newline": None, "etag": None}]
