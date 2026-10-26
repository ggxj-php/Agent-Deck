"""账号 / 邀请码 / 登录态：密码哈希、邀请码唯一性、token 失效、封号。"""
import time

import pytest

from deck import auth, db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(db, "_ready", False)
    db.init()
    yield
    monkeypatch.setattr(db, "_ready", False)


def make_user(name="someone", password="password123"):
    user, err = auth.create_user(name, password)
    assert not err, err
    return user


def test_密码哈希不是明文且能验证():
    stored = auth.hash_password("password123")
    assert "password123" not in stored
    assert auth.verify_password("password123", stored)
    assert not auth.verify_password("wrong-password", stored)
    assert not auth.verify_password("", stored)
    assert not auth.verify_password("password123", "")


def test_用户名和密码校验():
    assert auth.check_name("ab")                      # 太短
    assert auth.check_name("a" * 21)                  # 太长
    assert auth.check_name("有 空格")
    assert not auth.check_name("雏草姬_01")
    assert auth.check_password("1234567")             # 不够 8 位
    assert not auth.check_password("12345678")


def test_建用户会分到唯一邀请码():
    a = make_user("alpha")
    b = make_user("beta")
    assert a["invite_code"] and b["invite_code"]
    assert a["invite_code"] != b["invite_code"]
    assert len(a["invite_code"]) == auth.INVITE_LEN


def test_邀请码注册():
    inviter = make_user("inviter")
    user, err = auth.register("新来的雏草姬", "newpass123", inviter["invite_code"].lower())
    assert not err, err
    assert user["invited_by"] == "inviter"

    _, err = auth.register("路过的", "newpass123", "NOSUCHCD")
    assert "邀请码" in err


def test_登录态能发能验能撤销():
    user = make_user()
    token = auth.issue_token(user["id"])
    assert auth.resolve_token(token)["id"] == user["id"]
    auth.revoke_token(token)
    assert auth.resolve_token(token) is None
    assert auth.resolve_token("garbage") is None
    assert auth.resolve_token("") is None


def test_封号后登不进来_登录态也失效():
    user = make_user()
    token = auth.issue_token(user["id"])
    with db.cursor() as conn:
        conn.execute("UPDATE users SET disabled = 1 WHERE id = ?", (user["id"],))
    assert auth.authenticate("someone", "password123") is None
    # token 本身还能解析出来（中间件会再看 disabled 字段）——这里确认它带着这个标记
    resolved = auth.resolve_token(token)
    assert resolved and resolved["disabled"]


def test_改密码要先验旧密码():
    user = make_user()
    assert "原密码" in auth.change_password(user["id"], "bad", "newpass123")
    assert auth.change_password(user["id"], "password123", "newpass123") == ""
    assert auth.authenticate("someone", "newpass123")["id"] == user["id"]


def test_默认管理员只建一次():
    first = auth.ensure_default_admin()
    assert first and first["name"]
    assert auth.find_by_name(first["name"])["is_admin"] == 1
    assert auth.ensure_default_admin() is None       # 已经有一个了，不重复建


def test_普通用户不是管理员():
    user = make_user()
    assert auth.is_admin(user) is False
    admin, _ = auth.create_user("boss", "password123", is_admin=True)
    assert auth.is_admin(admin) is True

def test_临时封禁到期自动解封():
    user = make_user()
    until = auth.set_ban(user["id"], 1)              # 封 1 分钟
    assert until > time.time()
    fresh = auth.find_by_id(user["id"])
    assert auth.is_banned(fresh) is True
    assert auth.authenticate("someone", "password123") is None

    # 把到期时间改到过去，模拟时间流逝
    with db.cursor() as conn:
        conn.execute("UPDATE users SET ban_until = ? WHERE id = ?",
                     (time.time() - 1, user["id"]))
    assert auth.is_banned(auth.find_by_id(user["id"])) is False
    # 再登录一次：能登进去，并且顺手把封禁标记清了
    assert auth.authenticate("someone", "password123")["id"] == user["id"]
    assert auth.find_by_id(user["id"])["disabled"] == 0


def test_永久封禁和解封():
    user = make_user()
    auth.set_ban(user["id"], 0)                      # 0 = 永久
    row = auth.find_by_id(user["id"])
    assert row["ban_until"] == 0
    assert auth.is_banned(row) is True
    assert auth.authenticate("someone", "password123") is None

    auth.clear_ban(user["id"])
    assert auth.is_banned(auth.find_by_id(user["id"])) is False
    assert auth.authenticate("someone", "password123")["id"] == user["id"]


def test_封禁会立刻踢掉登录态():
    user = make_user()
    token = auth.issue_token(user["id"])
    assert auth.resolve_token(token) is not None
    auth.set_ban(user["id"], 30)
    assert auth.resolve_token(token) is None         # token 被删了
