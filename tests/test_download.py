"""临时下载链接：路径安全 / 过期 / 用户隔离。"""
import json
import os
import time

from deck.tools import download


def test_正常生成链接(tmp_path):
    download.set_owner(1)
    path = os.path.join(download.WORKSPACE, "notes")
    os.makedirs(path, exist_ok=True)
    file = os.path.join(path, "a.txt")
    with open(file, "w") as fp:
        fp.write("hello")
    out = json.loads(download.make_download_link("notes/a.txt"))
    assert out["ok"] and out["url"].startswith("/d/")
    token = out["url"].split("/")[-1]
    assert download.lookup(token, 1) is not None


def test_不允许越界路径():
    download.set_owner(1)
    for bad in ("../secret.txt", "/etc/passwd", "..\\..\\x"):
        out = json.loads(download.make_download_link(bad))
        assert "error" in out, bad


def test_别人打不开():
    download.set_owner(1)
    os.makedirs(os.path.join(download.WORKSPACE, "notes"), exist_ok=True)
    with open(os.path.join(download.WORKSPACE, "notes", "b.txt"), "w") as fp:
        fp.write("x")
    token = json.loads(download.make_download_link("notes/b.txt"))["url"].split("/")[-1]
    assert download.lookup(token, 1) is not None
    assert download.lookup(token, 2) is None       # 其他用户


def test_过期失效():
    download.set_owner(1)
    os.makedirs(os.path.join(download.WORKSPACE, "notes"), exist_ok=True)
    with open(os.path.join(download.WORKSPACE, "notes", "c.txt"), "w") as fp:
        fp.write("x")
    token = json.loads(download.make_download_link("notes/c.txt"))["url"].split("/")[-1]
    download._LINKS[token]["expires"] = time.time() - 1
    assert download.lookup(token, 1) is None
