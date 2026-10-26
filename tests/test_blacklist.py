"""IP 黑名单的准入校验：绝不能允许把自己锁在门外。"""
import pytest

from deck.web import blacklist


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.1",
    "169.254.1.1", "0.0.0.0", "224.0.0.1",
    "::1", "fe80::1", "fc00::1",
    "not-an-ip", "", "   ", "1.2.3.4.5",
])
def test_危险或非法地址一律拒绝(ip):
    assert blacklist._check_ip(ip) != ""


def test_公网地址放行():
    assert blacklist._check_ip("8.8.8.8") == ""
    assert blacklist._check_ip("1.1.1.1") == ""


def test_不能封操作者自己():
    assert blacklist._check_ip("8.8.8.8", actor="8.8.8.8") != ""
    assert blacklist._check_ip("8.8.8.8", actor="1.1.1.1") == ""
