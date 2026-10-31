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
    assert store.read_draft(draft_id) is None


def test_draft_id_cannot_escape_folder(store):
    assert store.read_draft("../../etc/passwd") is None
    with pytest.raises(ValueError):
        store.write_draft("../x", "nope")


def test_session_roundtrip_and_damaged_file(store):
    docs = [{"uri": "file:///a.md", "draft": None, "encoding": "UTF-8", "newline": "LF"}]
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
