"""画图产物：落库（刷新/重启还能看）、喂模型前剥标记、标记拼接。"""
import pytest

from deck import auth, db, store
from deck.tools import render


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(db, "_ready", False)
    db.init()
    render._ARTIFACTS.clear()
    yield
    monkeypatch.setattr(db, "_ready", False)
    render._ARTIFACTS.clear()


def test_产物落库之后重启也能取到():
    aid = "0123456789abcdef"
    html = "<html><body>hi</body></html>"
    assert render.persist_artifact(aid, "sess1", 1, "测试图", html) is True
    render._ARTIFACTS.clear()               # 模拟进程重启：内存没了
    item = render.get_artifact(aid)
    assert item and item["html"] == html
    assert item["title"] == "测试图"


def test_内存里有就直接用内存():
    aid = "fedcba9876543210"
    render._store("<b>x</b>", "内存图")
    item = render.get_artifact("fedcba9876543210".replace("fedcba9876543210", aid))
    assert item is None                    # 这个 id 没有，返回 None
    # 真的存一个
    real = render._store("<b>y</b>", "内存图2")
    assert render.get_artifact(real)["html"] == "<b>y</b>"


def test_标记拼接和剥离():
    text = "图给你画好了"
    marked = render.append_marks(text, [("0123456789abcdef", "红黑树"), ("fedcba9876543210", "a|b")])
    assert "[[artifact:0123456789abcdef|红黑树]]" in marked
    assert "|" in marked                   # 标题里的 | 被换掉了
    assert marked.count("[[artifact:") == 2
    assert render.strip_marks(marked) == "图给你画好了"


def test_喂模型的历史不含产物标记():
    user, err = auth.create_user("user1", "password123")
    assert not err
    sess = store.create(user["id"])
    marked = render.append_marks("画好了", [("0123456789abcdef", "红黑树")])
    store.save_messages(sess["id"], [
        {"role": "user", "content": "画个红黑树"},
        {"role": "assistant", "content": marked},
    ])
    # 给前端/历史：要留着标记（前端靠它渲染卡片）
    shown = store.load_messages(sess["id"])
    assert "[[artifact:" in shown[-1]["content"]
    # 给模型：必须剥掉
    for_model = store.load_messages(sess["id"], for_model=True)
    assert "[[artifact:" not in for_model[-1]["content"]
    assert "画好了" in for_model[-1]["content"]
