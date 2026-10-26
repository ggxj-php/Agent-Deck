"""账号、登录态、邀请码。

设计取舍：
- 密码用 bcrypt（已在依赖里）存哈希；没装 bcrypt 就退回标准库的 scrypt，绝不存明文。
- 登录态是随机 token，**库里只存它的 sha256**——万一库被拖走，也拿不到能用的 token。
- 邀请制注册：每个用户注册时自动分到一个独一无二的邀请码，注册时必须填一个存在的邀请码。
  第一个用户用命令行建（`python -m deck.auth create <用户名> <密码>`）。
"""
import hashlib
import hmac
import os
import secrets
import sys
import time

from . import db

COOKIE_NAME = "taffy_user"
TOKEN_TTL = 30 * 24 * 3600        # 登录态 30 天
INVITE_LEN = 8
# 去掉了 0/O/1/I/B/8 这些容易看错的
INVITE_ALPHABET = "ACDEFGHJKLMNPQRSTUVWXY2345679"

NAME_MIN, NAME_MAX = 3, 20
PASSWORD_MIN = 8

# 默认管理员：第一次启动自动建。密码优先取 .env 里的 TAFFY_ADMIN_PASSWORD，
# 没配就随机生成一份写进 .cache/admin_initial_password.txt（权限 600）。
DEFAULT_ADMIN_NAME = (os.environ.get("TAFFY_ADMIN_NAME", "").strip() or "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("TAFFY_ADMIN_PASSWORD", "").strip()

# ---------------- 密码 ----------------


def _bcrypt():
    try:
        import bcrypt
    except ImportError:
        return None
    return bcrypt


def hash_password(password):
    bcrypt = _bcrypt()
    if bcrypt is not None:
        digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))
        return "bcrypt$" + digest.decode("ascii")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt.encode(),
                            n=2 ** 14, r=8, p=1, dklen=32).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password, stored):
    if not password or not stored:
        return False
    try:
        if stored.startswith("bcrypt$"):
            bcrypt = _bcrypt()
            if bcrypt is None:
                return False
            return bcrypt.checkpw(password.encode("utf-8"), stored[7:].encode("ascii"))
        if stored.startswith("scrypt$"):
            _, salt, digest = stored.split("$", 2)
            calc = hashlib.scrypt(password.encode("utf-8"), salt=salt.encode(),
                                  n=2 ** 14, r=8, p=1, dklen=32).hex()
            return hmac.compare_digest(calc, digest)
    except Exception:                  # noqa: BLE001
        return False
    return False


# ---------------- 校验 ----------------


def check_name(name):
    """用户名：3~20 位，中文 / 字母 / 数字 / _ - 。"""
    name = (name or "").strip()
    if not (NAME_MIN <= len(name) <= NAME_MAX):
        return f"用户名要 {NAME_MIN}~{NAME_MAX} 个字"
    for ch in name:
        if not (ch.isalnum() or ch in "_-"):
            return "用户名只能用中文 / 字母 / 数字 / _ - "
    return ""


def check_password(password):
    if not password or len(password) < PASSWORD_MIN:
        return f"密码至少 {PASSWORD_MIN} 位"
    if len(password) > 200:
        return "密码太长啦"
    return ""


# ---------------- 邀请码 ----------------


def new_invite_code():
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_LEN))


def find_by_invite(code):
    code = (code or "").strip().upper()
    if not code:
        return None
    with db.cursor() as conn:
        row = conn.execute(
            "SELECT id, name, invite_code FROM users WHERE invite_code = ?", (code,)
        ).fetchone()
    return dict(row) if row else None


# ---------------- 用户 ----------------


def create_user(name, password, invited_by="", is_admin=False, note=""):
    """直接建用户（命令行 / 后台 / 初始化用，不校验邀请码）。返回 (user, error)。"""
    name = (name or "").strip()
    err = check_name(name) or check_password(password)
    if err:
        return None, err
    now = time.time()
    with db.cursor() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE name = ?", (name,)).fetchone()
        if exists:
            return None, "这个名字被占了"
        code = new_invite_code()
        for _ in range(12):
            if not conn.execute("SELECT 1 FROM users WHERE invite_code = ?", (code,)).fetchone():
                break
            code = new_invite_code()
        cur = conn.execute(
            "INSERT INTO users (name, password_hash, invite_code, invited_by, created_at, "
            "                   is_admin, disabled, note, last_login) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0)",
            (name, hash_password(password), code, invited_by, now,
             1 if is_admin else 0, note or ""),
        )
        user_id = cur.lastrowid
    return {"id": user_id, "name": name, "invite_code": code,
            "invited_by": invited_by, "created_at": now,
            "is_admin": bool(is_admin), "disabled": False, "ban_until": 0.0,
            "note": note or ""}, ""


def register(name, password, invite):
    """邀请码注册。返回 (user, error)。"""
    name = (name or "").strip()
    code = (invite or "").strip().upper()
    if not code:
        return None, "要填邀请码（找已经在用的用户要一个）"
    err = check_name(name) or check_password(password)
    if err:
        return None, err
    inviter = find_by_invite(code)
    if not inviter:
        return None, "邀请码不对，检查一下大小写和有没有漏字符"
    return create_user(name, password, invited_by=inviter["name"])


def find_by_name(name):
    name = (name or "").strip()
    with db.cursor() as conn:
        row = conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def find_by_id(user_id):
    with db.cursor() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def lookup(name, password):
    """登录校验：返回 (user, reason)。

    reason 三种：
      ""        登录成功
      "banned"  账号被封（user 里带着 ban_until）
      "bad"     用户名或密码不对

    单独拆出来是为了让接口能区分「密码错」和「被封了」——以前两种情况
    都返回 None，用户看到的是「用户名或密码不对」，不知道自己是被封了。
    """
    user = find_by_name(name)
    if not user:
        return None, "bad"
    if user.get("disabled") and not is_banned(user):
        clear_ban(user["id"])          # 临时封禁到期了，顺手把标记清掉
        user["disabled"] = 0
        user["ban_until"] = 0
    if is_banned(user):
        return user, "banned"
    if not verify_password(password, user["password_hash"]):
        return None, "bad"
    with db.cursor() as conn:
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?",
                     (time.time(), user["id"]))
    user["last_login"] = time.time()
    return user, ""


def authenticate(name, password):
    """老的入口：只回 user 或 None（被封也算 None）。"""
    user, reason = lookup(name, password)
    return user if reason == "" else None


def _left_text(seconds):
    """把剩余秒数说成人话：2 天 3 小时 / 5 小时 20 分 / 8 分钟。"""
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} 天" + (f" {hours} 小时" if hours else "")
    if hours:
        return f"{hours} 小时" + (f" {minutes} 分" if minutes else "")
    return f"{max(1, minutes)} 分钟"


def ban_detail(user, now=None):
    """被封账号的说明：永久 / 到什么时候解封 / 还剩多久。

    返回 {"permanent": bool, "until": 时间戳（永久为 0）, "text": 给用户看的一句话}
    """
    until = float(user.get("ban_until") or 0)
    if until <= 0:
        return {"permanent": True, "until": 0,
                "text": "账号已被永久封禁，请联系管理员解封"}
    now = now or time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(until))
    return {"permanent": False, "until": until,
            "text": f"账号已被封禁，解封时间 {stamp}（还剩 {_left_text(until - now)}）"}


def public_user(user):
    """给前端看的用户信息（绝不带密码哈希）。"""
    if not user:
        return None
    return {"id": user["id"], "name": user["name"],
            "invite_code": user["invite_code"],
            "invited_by": user.get("invited_by", ""),
            "is_admin": bool(user.get("is_admin")),
            "disabled": bool(user.get("disabled")),
            "ban_until": float(user.get("ban_until") or 0),
            "banned": is_banned(user),
            "note": user.get("note", ""),
            "created_at": user.get("created_at", 0),
            "last_login": user.get("last_login", 0)}


# ---------------- 管理员 ----------------


def is_admin(user):
    return bool(user and user.get("is_admin"))


def ensure_default_admin():
    """第一次启动时建默认管理员。

    已存在任何管理员就什么都不做。密码优先取 TAFFY_ADMIN_PASSWORD，
    没配就随机生成，写进 .cache/admin_initial_password.txt 并在日志里打出来。
    返回 {"name", "password", "generated"} 或 None（不用建）。
    """
    with db.cursor() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1").fetchone()
        if row and row["n"]:
            return None

    password = DEFAULT_ADMIN_PASSWORD
    generated = False
    if not password:
        password = secrets.token_urlsafe(12)
        generated = True

    user, err = create_user(DEFAULT_ADMIN_NAME, password, invited_by="system", is_admin=True,
                            note="默认管理员（首次启动自动创建）")
    if err:
        # 名字被占了（老库升级上来的情况）：把那个用户提权
        existing = find_by_name(DEFAULT_ADMIN_NAME)
        if not existing:
            return None
        with db.cursor() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (existing["id"],))
        if DEFAULT_ADMIN_PASSWORD:
            set_password(existing["id"], DEFAULT_ADMIN_PASSWORD)
        return {"name": DEFAULT_ADMIN_NAME, "password": DEFAULT_ADMIN_PASSWORD,
                "generated": not bool(DEFAULT_ADMIN_PASSWORD)}

    if generated:
        try:
            from .config import PROJECT_ROOT
            path = os.path.join(PROJECT_ROOT, ".cache", "admin_initial_password.txt")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"用户名：{user['name']}\n密码：{password}\n"
                         f"（登录后请尽快在后台改掉，然后把本文件删掉）\n")
            os.chmod(path, 0o600)
        except OSError:
            pass
        print(f"[admin] 已创建默认管理员 {user['name']}，初始密码 {password} "
              f"（也写在 .cache/admin_initial_password.txt，登录后请尽快改掉）", flush=True)
    return {"name": user["name"], "password": password, "generated": generated}


def set_password(user_id, password):
    err = check_password(password)
    if err:
        return err
    with db.cursor() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (hash_password(password), user_id))
    return ""


def change_password(user_id, old_password, new_password):
    """用户自己改密码：要先验旧密码。返回错误串，成功返回空。"""
    user = find_by_id(user_id)
    if not user:
        return "用户不存在"
    if not verify_password(old_password, user["password_hash"]):
        return "原密码不对"
    return set_password(user_id, new_password)


# ---------------- 封禁 ----------------


def is_banned(user, now=None):
    """这个账号**现在**算不算被封。

    disabled=1 且 ban_until=0 → 永久封禁；
    临时封禁到期后自动算没封（不用管理员手动解）。
    """
    if not user or not user.get("disabled"):
        return False
    until = float(user.get("ban_until") or 0)
    if until <= 0:
        return True
    return until > (now or time.time())


def set_ban(user_id, minutes=0):
    """封号。minutes <= 0 表示永久封禁；否则封到这个时间点。"""
    now = time.time()
    until = 0.0 if int(minutes or 0) <= 0 else now + int(minutes) * 60
    with db.cursor() as conn:
        conn.execute("UPDATE users SET disabled = 1, ban_until = ? WHERE id = ?",
                     (until, user_id))
        conn.execute("DELETE FROM auth_tokens WHERE user_id = ?", (user_id,))
    return until


def clear_ban(user_id):
    with db.cursor() as conn:
        conn.execute("UPDATE users SET disabled = 0, ban_until = 0 WHERE id = ?", (user_id,))


# ---------------- 登录态 ----------------


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(user_id, ip="", ua=""):
    """发一个新的登录 token（原文给 cookie，库里只留哈希）。"""
    raw = secrets.token_urlsafe(32)
    now = time.time()
    with db.cursor() as conn:
        conn.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (now,))
        conn.execute(
            "INSERT INTO auth_tokens (token_hash, user_id, created_at, expires_at, ip, ua) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_token_hash(raw), user_id, now, now + TOKEN_TTL, (ip or "")[:64], (ua or "")[:300]),
        )
    return raw


def resolve_token(token):
    """token -> user dict；无效或过期返回 None。"""
    if not token:
        return None
    with db.cursor() as conn:
        row = conn.execute(
            "SELECT u.* FROM auth_tokens t JOIN users u ON u.id = t.user_id "
            "WHERE t.token_hash = ? AND t.expires_at > ?",
            (_token_hash(token), time.time()),
        ).fetchone()
    return dict(row) if row else None


def revoke_token(token):
    if not token:
        return
    with db.cursor() as conn:
        conn.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (_token_hash(token),))


def revoke_all(user_id):
    with db.cursor() as conn:
        conn.execute("DELETE FROM auth_tokens WHERE user_id = ?", (user_id,))


# ---------------- 命令行 ----------------

_USAGE = """用法：
  python -m deck.auth create <用户名> <密码>    # 建用户（第一个用户这么建）
  python -m deck.auth list                      # 看现有用户和邀请码
"""


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_USAGE)
        return 0
    cmd = argv[0]
    if cmd == "create":
        if len(argv) < 3:
            print(_USAGE)
            return 2
        user, err = create_user(argv[1], argv[2])
        if err:
            print(f"❌ {err}")
            return 1
        print(f"✅ 建好了：{user['name']}")
        print(f"   邀请码：{user['invite_code']}（把这个给要注册的人）")
        return 0
    if cmd == "list":
        with db.cursor() as conn:
            rows = conn.execute(
                "SELECT id, name, invite_code, invited_by, created_at, is_admin, disabled, ban_until "
                "FROM users ORDER BY id").fetchall()
        if not rows:
            print("还没有用户。用 `python -m deck.auth create <用户名> <密码>` 建第一个。")
            return 0
        print(f"{'ID':<4} {'用户名':<14} {'邀请码':<10} {'邀请人':<12} 注册时间")
        for row in rows:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["created_at"]))
            flag = "管理员" if row["is_admin"] else ""
            if row["disabled"]:
                until = row["ban_until"] or 0
                if until:
                    flag += "（封到 %s）" % time.strftime(
                        "%m-%d %H:%M", time.localtime(until))
                else:
                    flag += "（永久封禁）"
            print(f"{row['id']:<4} {row['name']:<14} {row['invite_code']:<10} "
                  f"{row['invited_by'] or '-':<12} {ts}  {flag}")
        return 0
    print(_USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
