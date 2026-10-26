"""会话持久化：增删改查、消息往返、按用户隔离、标题自动取第一句。"""
import pytest

from deck import auth, db, store


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(db, "_ready", False)
    db.init()
    yield
    monkeypatch.setattr(db, "_ready", False)


def make_user(name):
    user, err = auth.create_user(name, "password123")
    assert not err, err
    return user


def test_建会话和列表():
    user = make_user("user1")
    sess = store.create(user["id"])
    assert sess["id"]
    items = store.list_for_user(user["id"])
    assert len(items) == 1
    assert items[0]["id"] == sess["id"]
    assert items[0]["messages"] == 0


def test_消息往返():
    user = make_user("user1")
    sess = store.create(user["id"])
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀喵"},
    ]
    store.save_messages(sess["id"], messages)
    loaded = store.load_messages(sess["id"])
    assert loaded == messages


def test_标题自动取用户第一句话():
    user = make_user("user1")
    sess = store.create(user["id"])
    store.save_messages(sess["id"], [{"role": "user", "content": "帮我讲讲快速排序"}],
                        first_user_text="帮我讲讲快速排序")
    assert store.get(user["id"], sess["id"])["title"].startswith("帮我讲讲")


def test_别人的会话拿不到():
    a = make_user("user1")
    b = make_user("user2")
    sess = store.create(a["id"])
    assert store.get(a["id"], sess["id"]) is not None
    assert store.get(b["id"], sess["id"]) is None      # 不是自己的，当不存在
    assert store.delete(b["id"], sess["id"]) is False  # 也删不掉
    assert store.get(a["id"], sess["id"]) is not None


def test_删除会话会连消息一起删():
    user = make_user("user1")
    sess = store.create(user["id"])
    store.save_messages(sess["id"], [{"role": "user", "content": "hi"}])
    assert store.delete(user["id"], sess["id"]) is True
    assert store.load_messages(sess["id"]) == []
    assert store.count_for_user(user["id"]) == 0


def test_重命名():
    user = make_user("user1")
    sess = store.create(user["id"])
    store.rename(user["id"], sess["id"], "  新的 标题  ")
    assert store.get(user["id"], sess["id"])["title"] == "新的 标题"


def test_cut_off_会存下来():
    user = make_user("user1")
    sess = store.create(user["id"])
    store.save_messages(sess["id"], [], cut_off=True)
    assert store.get(user["id"], sess["id"])["cut_off"] == 1
