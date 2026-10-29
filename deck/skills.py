"""Agent Skills（Claude Code 风格技能包）的发现与读取。

技能就是一个目录，里面至少有一个 `SKILL.md`，开头用 YAML frontmatter 写
name / description，正文是给模型看的工作流说明；同目录下还可以带脚本、模板。

约定：模型先用 list_skills 看到有哪些技能，需要时用 load_skill 把 SKILL.md
读进上下文，再按里面的步骤用 run_code / 各种工具干活。

技能目录只认这两个地方（都不进 git）：
  <项目>/skills/<name>/            —— 管理员手工装的
  <项目>/workspace/skills/<name>/  —— 从网上下载的、或用户会话里装的
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from .config import PROJECT_ROOT

SKILL_DIRS = (
    os.path.join(PROJECT_ROOT, "skills"),
    os.path.join(PROJECT_ROOT, "workspace", "skills"),
)
SKILL_FILE = "SKILL.md"
MAX_TEXT = 200_000          # 单个文件最多读这么多字符，别把上下文撑爆
MAX_FILES = 300             # 列目录最多列这么多条
_NAME_OK = re.compile(r"^[A-Za-z0-9._\-\u4e00-\u9fff]+$")


def _safe_name(name):
    name = str(name or "").strip()
    if not name or not _NAME_OK.match(name) or name in (".", ".."):
        return ""
    return name


def _parse_frontmatter(text):
    """取 YAML frontmatter 的 name / description（只解析这两个字段，够用）。"""
    out = {}
    if not text.startswith("---"):
        return out
    end = text.find("\n---", 3)
    if end < 0:
        return out
    block = text[3:end]
    key = None
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if m:
            key = m.group(1).lower()
            value = m.group(2).strip()
            if value in (">", "|", ">-", "|-"):
                value = ""
            out[key] = value
        elif key and line.startswith((" ", "\t")):
            out[key] = (out.get(key, "") + " " + line.strip()).strip()
    return out


def _read_text(path, limit=MAX_TEXT):
    with open(path, encoding="utf-8", errors="replace") as fp:
        return fp.read(limit)


def discover():
    """扫所有技能目录，返回 [{name, description, path, source}]。"""
    found = {}
    for base in SKILL_DIRS:
        if not os.path.isdir(base):
            continue
        for entry in sorted(os.listdir(base)):
            folder = os.path.join(base, entry)
            skill_md = os.path.join(folder, SKILL_FILE)
            if not os.path.isfile(skill_md):
                continue
            try:
                info = _parse_frontmatter(_read_text(skill_md, 20_000))
            except OSError:
                continue
            name = _safe_name(info.get("name") or entry)
            if not name:
                continue
            desc = " ".join(str(info.get("description") or "").split())
            found[name] = {
                "name": name,
                "description": desc[:600],
                "path": folder,
                "source": "workspace" if "workspace" in base else "project",
            }
    return [found[k] for k in sorted(found)]


def find(name):
    """按名字找技能目录（绝对路径）；找不到返回空串。"""
    name = _safe_name(name)
    if not name:
        return ""
    for item in discover():
        if item["name"] == name:
            return item["path"]
    return ""


def load(name):
    """读 SKILL.md 正文（含 frontmatter）。返回 (name, text, path)。"""
    folder = find(name)
    if not folder:
        raise ValueError(f"没有这个技能：{name}。先用 list_skills 看看有哪些。")
    path = os.path.join(folder, SKILL_FILE)
    return name, _read_text(path), path


def files(name, subdir=""):
    """列出技能目录下的文件（相对路径）。subdir 只能往深处走，不能跳出技能目录。"""
    folder = find(name)
    if not folder:
        raise ValueError(f"没有这个技能：{name}")
    root = os.path.realpath(folder)
    target = os.path.realpath(os.path.join(root, subdir or ""))
    if not (target == root or target.startswith(root + os.sep)):
        raise ValueError("路径越界了，只能看技能目录里的文件")
    if not os.path.isdir(target):
        raise ValueError(f"技能里没有这个目录：{subdir}")
    out = []
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, fname), root)
            out.append(rel.replace(os.sep, "/"))
            if len(out) >= MAX_FILES:
                return out
    return out


def read_file(name, path):
    """读技能目录里的某个文件（SKILL.md、workflow 文档、脚本都行）。"""
    folder = find(name)
    if not folder:
        raise ValueError(f"没有这个技能：{name}")
    root = os.path.realpath(folder)
    full = os.path.realpath(os.path.join(root, str(path or "")))
    if not (full == root or full.startswith(root + os.sep)):
        raise ValueError("路径越界了，只能读技能目录里的文件")
    if not os.path.isfile(full):
        raise ValueError(f"技能里没有这个文件：{path}。用 list_skill_files 看看有哪些。")
    if os.path.getsize(full) > 5 * 1024 * 1024:
        raise ValueError("这个文件超过 5MB，别整个读，挑需要的部分")
    return _read_text(full)

# ---------------- 启用/禁用 与 安装 ----------------

STATE_FILE = "skills.json"          # 放在技能目录里，记哪些被后台关掉了


def _state_path():
    return os.path.join(SKILL_DIRS[-1], STATE_FILE)


def _load_state():
    path = _state_path()
    if not os.path.isfile(path):
        return {"disabled": [], "installed": {}}
    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            raise ValueError("bad state")
    except (OSError, ValueError):
        return {"disabled": [], "installed": {}}
    data.setdefault("disabled", [])
    data.setdefault("installed", {})
    return data


def _save_state(data):
    os.makedirs(SKILL_DIRS[-1], exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, _state_path())


def disabled_names():
    return set(_load_state().get("disabled") or [])


def enabled_skills():
    """模型实际能看到的技能（排掉后台关掉的）。"""
    off = disabled_names()
    return [item for item in discover() if item["name"] not in off]


def set_enabled(name, enabled):
    name = _safe_name(name)
    if not find(name):
        raise ValueError(f"没有这个技能：{name}")
    state = _load_state()
    off = set(state.get("disabled") or [])
    if enabled:
        off.discard(name)
    else:
        off.add(name)
    state["disabled"] = sorted(off)
    _save_state(state)
    return name not in off


def _dir_size(path):
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for fname in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fname))
            except OSError:
                pass
    return total


def describe():
    """给后台用：所有技能 + 是否启用 + 大小 + 安装时间。"""
    off = disabled_names()
    installed = _load_state().get("installed") or {}
    out = []
    for item in discover():
        folder = item["path"]
        out.append({
            "name": item["name"],
            "description": item["description"],
            "source": item["source"],
            "enabled": item["name"] not in off,
            "size_mb": round(_dir_size(folder) / 1024 / 1024, 1),
            "installed_at": installed.get(item["name"], {}).get("at", 0),
            "repo": installed.get(item["name"], {}).get("repo", ""),
        })
    return sorted(out, key=lambda x: x["name"])


_REPO_OK = re.compile(r"^https://(github\.com|gitee\.com)/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$")


def normalize_repo(url, subdir=""):
    """校验 GitHub/Gitee 仓库地址和子目录（防止命令注入 / 路径穿越）。"""
    url = str(url or "").strip().rstrip("/")
    if not _REPO_OK.match(url + "/" if not url.endswith("/") else url):
        raise ValueError("只支持 https://github.com/owner/repo 或 https://gitee.com/owner/repo 这种地址")
    if url.endswith(".git"):
        url = url[:-4]
    raw_sub = str(subdir or "").strip()
    if raw_sub.startswith(("/", "\\")) or "\\" in raw_sub or ".." in raw_sub:
        raise ValueError("子目录名不合法")
    sub = raw_sub.strip("/")
    if sub:
        parts = sub.split("/")
        if any(p in ("", ".", "..") or not re.match(r"^[A-Za-z0-9_.\-]+$", p) for p in parts):
            raise ValueError("子目录名不合法")
    return url, sub


def install_from_git(url, subdir=""):
    """从 GitHub/Gitee 拉一个技能仓库（可只取子目录），装到 workspace/skills/。

    返回安装好的技能名列表。整个过程在临时目录里做，失败不会弄脏现有技能。
    """
    url, sub = normalize_repo(url, subdir)
    os.makedirs(SKILL_DIRS[-1], exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="skilldl-") as tmp:
        clone_dir = os.path.join(tmp, "repo")
        cmd = ["git", "clone", "--depth", "1", "--quiet"]
        if sub:
            cmd += ["--filter=blob:none", "--sparse"]
        cmd += [url, clone_dir]
        proc = subprocess.run(cmd, capture_output=True, timeout=300)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "ignore")[-300:]
            raise ValueError(f"仓库拉不下来：{err or 'git 出错'}")
        root = clone_dir
        if sub:
            proc = subprocess.run(["git", "-C", clone_dir, "sparse-checkout", "set", sub],
                                  capture_output=True, timeout=300)
            if proc.returncode != 0:
                raise ValueError("子目录拉不下来：" + proc.stderr.decode("utf-8", "ignore")[-200:])
            root = os.path.join(clone_dir, sub)
        if os.path.isfile(os.path.join(root, SKILL_FILE)):
            candidates = [root]
        else:
            candidates = []
            for entry in sorted(os.listdir(root)):
                folder = os.path.join(root, entry)
                if os.path.isfile(os.path.join(folder, SKILL_FILE)):
                    candidates.append(folder)
        if not candidates:
            raise ValueError("这个仓库里没找到 SKILL.md（技能目录里必须有它）")
        state = _load_state()
        installed = state.setdefault("installed", {})
        names = []
        for folder in candidates:
            text = _read_text(os.path.join(folder, SKILL_FILE), 20_000)
            info = _parse_frontmatter(text)
            name = _safe_name(info.get("name") or os.path.basename(folder))
            if not name:
                continue
            target = os.path.join(SKILL_DIRS[-1], name)
            if os.path.isdir(target):
                shutil.rmtree(target)
            shutil.copytree(folder, target)
            installed[name] = {"repo": url, "subdir": sub, "at": int(time.time())}
            names.append(name)
        if not names:
            raise ValueError("找到的技能没有合法的 name")
        _save_state(state)
        return names


def uninstall(name):
    name = _safe_name(name)
    folder = find(name)
    if not folder:
        raise ValueError(f"没有这个技能：{name}")
    root = os.path.realpath(SKILL_DIRS[-1])
    real = os.path.realpath(folder)
    if not real.startswith(root + os.sep):
        raise ValueError("这个技能不是装在 workspace/skills 里的，不能在这里删")
    shutil.rmtree(real)
    state = _load_state()
    state.get("installed", {}).pop(name, None)
    off = set(state.get("disabled") or [])
    off.discard(name)
    state["disabled"] = sorted(off)
    _save_state(state)
    return name
