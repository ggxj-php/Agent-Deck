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


def test_登录能区分密码错还是被封():
    user = make_user()
    # 密码错：reason 是 bad，登录页提示「用户名或密码不对」
    got, reason = auth.lookup(user["name"], "wrong-password")
    assert got is None and reason == "bad"
    # 用户名不存在也是 bad（不泄露账号是否存在）
    got, reason = auth.lookup("no-such-user", "x")
    assert got is None and reason == "bad"
    # 正常的：reason 空
    got, reason = auth.lookup(user["name"], "password123")
    assert got and reason == ""


def test_被封时返回解封时间():
    user = make_user()
    until = auth.set_ban(user["id"], 120)            # 封 2 小时
    fresh = auth.find_by_id(user["id"])
    got, reason = auth.lookup(user["name"], "password123")  # 密码对也要拦住
    assert got and reason == "banned"

    info = auth.ban_detail(fresh)
    assert info["permanent"] is False
    assert info["until"] == until
    assert "解封时间" in info["text"]
    assert "小时" in info["text"] or "分钟" in info["text"]


def test_永久封禁的说明():
    user = make_user()
    auth.set_ban(user["id"], 0)                      # 0 = 永久
    fresh = auth.find_by_id(user["id"])
    info = auth.ban_detail(fresh)
    assert info["permanent"] is True
    assert info["until"] == 0
    assert "永久封禁" in info["text"]


def test_剩余时间是中文的():
    assert auth._left_text(3 * 86400 + 2 * 3600) == "3 天 2 小时"
    assert auth._left_text(5 * 3600 + 20 * 60) == "5 小时 20 分"
    assert auth._left_text(8 * 60) == "8 分钟"
    assert auth._left_text(10) == "1 分钟"           # 不足一分钟也说 1 分钟


def test_用户名只认中文和英文字母数字():
    # 正常
    assert auth.check_name("塔菲塔菲") == ""
    assert auth.check_name("user_01-x") == ""
    assert auth.check_name("123456") == ""
    # 危险字符
    for bad in ("<script>", 'a"b', "a'b", "admin';--", "../etc", "a b", "abc\ndef",
                "ad\u200bmin", "塔菲😀"):
        assert auth.check_name(bad), f"{bad!r} 应该被拒"
    # 同形字：西里尔 / 希腊 / 全角，看着像 admin 但其实是别的字符
    for bad in ("аdmin", "αdmin", "ａｄｍｉｎ"):
        assert auth.check_name(bad), f"{bad!r} 应该被拒（同形字伪装）"


def test_大小写不同的同名不能注册():
    user, err = auth.create_user("Alice", "password123")
    assert not err, err
    dup, err = auth.create_user("alice", "password123")
    assert dup is None and "被占" in err
