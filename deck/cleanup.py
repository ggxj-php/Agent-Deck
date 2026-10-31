"""生成文件自动清理：workspace 里生成的 PPT 等成品，超过 TTL 自动删。

跟上传附件分开：附件（uploads/）是 20 分钟 TTL，这里管的是「生成给他下载的成品」，
默认保留 12 小时（可在 config.py 里用 GENERATED_TTL_HOURS 改）。
"""
import os
import time

from .config import GENERATED_TTL_HOURS, PROJECT_ROOT

WORKSPACE = os.path.join(PROJECT_ROOT, "workspace")
PROJECTS_DIR = os.path.join(WORKSPACE, "projects")

# 哪些后缀算「生成物」，要参与 TTL 清理
GENERATED_EXTS = (".pptx", ".ppt", ".pdf", ".docx", ".xlsx",
                  # 浏览器下载的视频/图片/压缩包
                  ".mp4", ".m3u8", ".webm", ".flv", ".mov", ".zip",
                  ".jpg", ".jpeg", ".png", ".webp")


def _age(path, now):
    try:
        return now - os.path.getmtime(path)
    except OSError:
        return 0


def sweep(hours=None, now=None):
    """删掉超过 TTL 的生成文件/项目目录，返回 (删了几个文件, 删了几个目录, 释放字节)。"""
    ttl = (GENERATED_TTL_HOURS if hours is None else hours) * 3600
    now = time.time() if now is None else now
    files = dirs = 0
    freed = 0

    # 1) workspace/projects/ 下整个项目目录（技能生成的工程）按目录 mtime 算
    if os.path.isdir(PROJECTS_DIR):
        for entry in os.listdir(PROJECTS_DIR):
            folder = os.path.join(PROJECTS_DIR, entry)
            if not os.path.isdir(folder):
                continue
            if _age(folder, now) < ttl:
                continue
            size = 0
            for dirpath, _dirnames, filenames in os.walk(folder):
                for fname in filenames:
                    try:
                        size += os.path.getsize(os.path.join(dirpath, fname))
                    except OSError:
                        pass
            try:
                import shutil
                shutil.rmtree(folder, ignore_errors=True)
                dirs += 1
                freed += size
            except OSError:
                pass

    # 1.5) workspace/browser/ 下浏览器下载的东西（视频、截图、图片包）整个目录清理
    browser_dir = os.path.join(WORKSPACE, "browser")
    if os.path.isdir(browser_dir):
        for dirpath, dirnames, filenames in os.walk(browser_dir, topdown=False):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                if _age(full, now) < ttl:
                    continue
                try:
                    size = os.path.getsize(full)
                    os.remove(full)
                    files += 1
                    freed += size
                except OSError:
                    pass
            if dirpath != browser_dir and not os.listdir(dirpath):
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass

    # 2) workspace 根目录 / 子目录里散落的生成文件（.pptx 等）
    for dirpath, dirnames, filenames in os.walk(WORKSPACE):
        dirnames[:] = [d for d in dirnames if d not in ("uploads", "skills", ".git")]
        for fname in filenames:
            if not fname.lower().endswith(GENERATED_EXTS):
                continue
            full = os.path.join(dirpath, fname)
            if _age(full, now) < ttl:
                continue
            try:
                size = os.path.getsize(full)
                os.remove(full)
                files += 1
                freed += size
            except OSError:
                pass

    return files, dirs, freed
