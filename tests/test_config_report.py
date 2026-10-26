"""配置总览：能生成、每项字段齐全、密钥不裸奔。"""
from taffy import config_report


def test_报告能生成():
    data = config_report.report()
    assert data["count"] > 10
    assert len(data["groups"]) >= 4
    for group in data["groups"]:
        assert group["name"] and group["items"]
        for item in group["items"]:
            assert item["label"] and item["source"]
            assert "value" in item and "default" in item
            assert isinstance(item["secret"], bool)


def test_密钥会被打码():
    for group in config_report.report()["groups"]:
        for item in group["items"]:
            if item["secret"] and item["value"]:
                assert "…" in item["value"] or item["value"].startswith("sk-***")
