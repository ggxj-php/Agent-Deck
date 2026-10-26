"""上传图片：只认真图、剥 EXIF、限制数量、TTL 清理。"""
import base64
import io
import json
import time

import pytest
from PIL import Image

from taffy import uploads


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(uploads, "INDEX_PATH", str(tmp_path / "index.json"))
    return tmp_path


def image_url(fmt="PNG", size=(64, 64), color=(200, 40, 40), exif=None):
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    if exif is not None:
        img.save(buf, fmt, exif=exif)
    else:
        img.save(buf, fmt)
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return "data:" + mime + ";base64," + base64.b64encode(buf.getvalue()).decode()


def test_真图能存下并记索引(isolated):
    refs, notes = uploads.save([image_url()])
    assert len(refs) == 1 and not notes
    ref = refs[0]
    assert ref["path"].startswith("uploads/")
    assert (isolated / "uploads" / ref["path"].split("/", 1)[1]).is_file()
    index = json.loads((isolated / "index.json").read_text(encoding="utf-8"))
    assert ref["id"] in index
    assert uploads.data_url(ref["id"]).startswith("data:image/")


@pytest.mark.parametrize("bad, keyword", [
    ("data:image/png;base64," + base64.b64encode(b"not an image at all" * 10).decode(), "真图片"),
    ("data:image/png;base64,!!!!notbase64!!!!", "base64"),
    ("https://evil.example/x.png", "内联"),
])
def test_假图片被拒(bad, keyword):
    refs, notes = uploads.save([bad])
    assert refs == []
    assert notes and keyword in notes[0]


def test_空输入直接返回空():
    refs, notes = uploads.save([])
    assert refs == [] and notes == []
    refs, notes = uploads.save([""])
    assert refs == [] and notes == []


def test_超过上限只收前六张():
    url = image_url()
    refs, notes = uploads.save([url] * 10)
    assert len(refs) == uploads.MAX_COUNT


def test_重新编码会剥掉_EXIF(isolated):
    exif = Image.Exif()
    exif[0x010F] = "TaffyPrivateCam"   # Make
    exif[0x0112] = 6                   # Orientation
    refs, _ = uploads.save([image_url(fmt="JPEG", size=(120, 120), exif=exif)])
    assert refs
    stored = isolated / "uploads" / refs[0]["path"].split("/", 1)[1]
    with Image.open(stored) as img:
        tags = dict(img.getexif())
    assert 0x010F not in tags, "EXIF 没剥干净，设备信息会泄露"


def test_过期会被清掉(isolated):
    refs, _ = uploads.save([image_url()])
    assert refs
    index_path = isolated / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for item in index.values():
        item["ts"] = time.time() - uploads.TTL_SECONDS - 60
    index_path.write_text(json.dumps(index), encoding="utf-8")

    uploads.sweep()
    stored = isolated / "uploads" / refs[0]["path"].split("/", 1)[1]
    assert not stored.exists()
    assert uploads.data_url(refs[0]["id"]) == ""


def test_标记文本带路径和摘要():
    refs = [{"id": "abc123def456", "path": "uploads/abc123def456.jpg", "mime": "image/jpeg"}]
    text = uploads.marker_text(refs, {"abc123def456": "一张红色方块"})
    assert "abc123def456" in text
    assert "uploads/abc123def456.jpg" in text
    assert "一张红色方块" in text
