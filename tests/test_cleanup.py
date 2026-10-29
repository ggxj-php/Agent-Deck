"""生成物 TTL 清理：过期的删、新的留、uploads 不动。"""
import os
import time

from deck import cleanup


def _touch(path, mtime, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fp:
        fp.write(content)
    os.utime(path, (mtime, mtime))


def test_生成文件按TTL清理(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    projects = ws / "projects"
    projects.mkdir(parents=True)
    now = time.time()
    old_ts = now - 13 * 3600
    new_ts = now - 1 * 3600

    _touch(str(ws / "old.pptx"), old_ts)
    _touch(str(ws / "new.pptx"), new_ts)
    _touch(str(ws / "keep.py"), old_ts)              # 非生成物，不删
    _touch(str(ws / "uploads" / "img.jpg"), old_ts)  # 上传目录，不归这里管

    old_proj = projects / "old_proj"
    _touch(str(old_proj / "svg" / "a.svg"), old_ts)
    os.utime(str(old_proj), (old_ts, old_ts))      # 目录 mtime 也改成旧的
    new_proj = projects / "new_proj"
    _touch(str(new_proj / "svg" / "b.svg"), new_ts)

    monkeypatch.setattr(cleanup, "WORKSPACE", str(ws))
    monkeypatch.setattr(cleanup, "PROJECTS_DIR", str(projects))

    files, dirs, freed = cleanup.sweep(hours=12, now=now)
    assert files == 1 and freed > 0
    assert not (ws / "old.pptx").exists()
    assert (ws / "new.pptx").exists()
    assert (ws / "keep.py").exists()
    assert (ws / "uploads" / "img.jpg").exists()
    assert dirs == 1 and not old_proj.exists()
    assert new_proj.exists()


def test_没到时间什么都不删(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    now = time.time()
    _touch(str(ws / "a.pptx"), now - 3600)
    monkeypatch.setattr(cleanup, "WORKSPACE", str(ws))
    monkeypatch.setattr(cleanup, "PROJECTS_DIR", str(ws / "projects"))
    assert cleanup.sweep(hours=12, now=now) == (0, 0, 0)
