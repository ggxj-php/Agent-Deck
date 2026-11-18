"""数据库每日备份：SQLite 在线备份 + 只保留最近 5 份。

手动：  python -m deck.db_backup create
列表：  python -m deck.db_backup list
清理：  python -m deck.db_backup prune
后台页面调 backup_now()/list_backups()，自动备份由 systemd timer 每天 04:00 触发。
"""
import argparse
import glob
import os
import re
import sqlite3
import time

from . import db

KEEP = 5
NAME_RE = re.compile(r"^taffy-\d{8}-\d{6}\.db$")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.environ.get("TAFFY_DB_BACKUP_DIR", "").strip() or os.path.join(
    PROJECT_ROOT, ".cache", "db-backups")


def _source_path():
    return db.DB_PATH


def _ensure_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    return BACKUP_DIR


def backup_now(reason="manual"):
    """做一份一致性备份，返回文件路径。reason 只用于控制台提示。"""
    src_path = _source_path()
    if not os.path.exists(src_path):
        raise FileNotFoundError(f"数据库不存在：{src_path}")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = f"taffy-{time.strftime('%Y%m%d-%H%M%S')}.db"
    dest_path = os.path.join(BACKUP_DIR, name)
    # 同一秒重复触发时加序号，避免覆盖
    if os.path.exists(dest_path):
        name = f"taffy-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid() % 1000}.db"
        dest_path = os.path.join(BACKUP_DIR, name)
    src = sqlite3.connect(src_path, timeout=30)
    dst = sqlite3.connect(dest_path)
    try:
        with dst:
            src.backup(dst)
        check = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"备份完整性校验失败：{check}")
        os.chmod(dest_path, 0o600)
    except Exception:
        dst.close()
        src.close()
        try:
            os.remove(dest_path)
        except OSError:
            pass
        raise
    finally:
        try:
            src.close()
        except Exception:
            pass
        try:
            dst.close()
        except Exception:
            pass
    prune()
    print(f"[db-backup] {reason} 完成：{dest_path} ({os.path.getsize(dest_path)/1048576:.1f} MB)", flush=True)
    return dest_path


def list_backups():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    items = []
    for path in glob.glob(os.path.join(BACKUP_DIR, "taffy-*.db")):
        name = os.path.basename(path)
        if not NAME_RE.match(name):
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        items.append({"name": name, "size": st.st_size, "mtime": st.st_mtime})
    items.sort(key=lambda x: (x["mtime"], x["name"]), reverse=True)
    return items


def prune(keep=KEEP):
    items = list_backups()
    removed = []
    for item in items[keep:]:
        path = os.path.join(BACKUP_DIR, item["name"])
        try:
            os.remove(path)
            removed.append(item["name"])
        except OSError:
            pass
    if removed:
        print(f"[db-backup] 清理旧备份：{', '.join(removed)}", flush=True)
    return removed


def delete_backup(name):
    name = os.path.basename(str(name or ""))
    if not NAME_RE.match(name):
        raise ValueError("备份文件名不合法")
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.isfile(path):
        raise FileNotFoundError(name)
    os.remove(path)
    return name


def main(argv=None):
    parser = argparse.ArgumentParser(description="Taffy 数据库备份")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_create = sub.add_parser("create")
    p_create.add_argument("--reason", default="manual")
    sub.add_parser("list")
    sub.add_parser("prune")
    args = parser.parse_args(argv)
    if args.cmd == "create":
        print(backup_now(args.reason))
    elif args.cmd == "list":
        for item in list_backups():
            print(f"{item['name']}\t{item['size']}\t{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(item['mtime']))}")
    elif args.cmd == "prune":
        prune()


if __name__ == "__main__":
    main()
