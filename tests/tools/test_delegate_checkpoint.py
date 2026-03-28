import sys
import tempfile
import os
sys.path.insert(0, '/root/hermes-agent')
from tools.delegate_checkpoint import CheckpointStore


def test_checkpoint_save_and_load(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = CheckpointStore(db_path)
    store.save(
        task_id="task_0",
        iteration=5,
        messages=[{"role": "user", "content": "hello"}],
        metadata={"goal": "do something"},
    )
    cp = store.load("task_0")
    assert cp is not None
    assert cp["iteration"] == 5
    assert cp["messages"][0]["content"] == "hello"
    assert cp["metadata"]["goal"] == "do something"


def test_checkpoint_load_missing_returns_none(tmp_path):
    store = CheckpointStore(str(tmp_path / "test.db"))
    assert store.load("nonexistent") is None


def test_checkpoint_save_overwrites_previous(tmp_path):
    store = CheckpointStore(str(tmp_path / "test.db"))
    store.save("task_0", 3, [], {})
    store.save("task_0", 7, [{"role": "user", "content": "updated"}], {})
    cp = store.load("task_0")
    assert cp["iteration"] == 7


def test_checkpoint_delete(tmp_path):
    store = CheckpointStore(str(tmp_path / "test.db"))
    store.save("task_0", 1, [], {})
    store.delete("task_0")
    assert store.load("task_0") is None


def test_checkpoint_list(tmp_path):
    store = CheckpointStore(str(tmp_path / "test.db"))
    store.save("task_0", 1, [], {"goal": "a"})
    store.save("task_1", 2, [], {"goal": "b"})
    tasks = store.list_checkpoints()
    ids = [t["task_id"] for t in tasks]
    assert "task_0" in ids
    assert "task_1" in ids
