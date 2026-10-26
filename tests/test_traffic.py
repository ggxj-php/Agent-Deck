"""访问统计：站点归类、机房识别、口径别名。"""
from taffy.web import traffic


def test_站点别名():
    assert traffic.norm_site("www") == traffic.SITE_WWW
    assert traffic.norm_site("api") == traffic.SITE_API
    assert traffic.norm_site("all") == "all"
    assert traffic.norm_site("") == traffic.SITE_WWW
    assert traffic.norm_site(None) == traffic.SITE_WWW
    # 传全名也要认（之前在这里踩过坑，会回落成 www）
    assert traffic.norm_site(traffic.SITE_WWW) == traffic.SITE_WWW
    assert traffic.norm_site(traffic.SITE_API) == traffic.SITE_API


def test_按_Host_归类站点():
    # 域名不写死在代码里，测试也用 traffic 里那对常量
    assert traffic.site_of({"host": traffic.SITE_WWW, "url": "/", "ref": ""}) == traffic.SITE_WWW
    assert traffic.site_of({"host": traffic.SITE_API, "url": "/", "ref": ""}) == traffic.SITE_API
    # 裸 IP / 随机 Host 头都落到默认 server（one-api）
    assert traffic.site_of({"host": "198.51.100.7", "url": "/", "ref": ""}) == traffic.SITE_API
    assert traffic.site_of({"host": "random.example", "url": "/", "ref": ""}) == traffic.SITE_API


def test_归类认得出自家域名和子域():
    assert traffic._classify(traffic.SITE_WWW, "/") == traffic.SITE_WWW
    assert traffic._classify(traffic.SITE_API, "/") == traffic.SITE_API
    # 同一基础域名的子域算 WWW 站
    assert traffic._classify("blog." + traffic.SITE_BASE, "/") == traffic.SITE_WWW
    # 认不出来的原样返回，不硬套成本站
    assert traffic._classify("evil.example", "/") == "evil.example"


def test_referer_里是自家域名也能认():
    ref = "https://" + traffic.SITE_WWW + "/chat"
    assert traffic._classify("", "/", ref) == traffic.SITE_WWW


def test_机房识别():
    assert traffic._is_idc({"as": "AS45090 Shenzhen Tencent"}) is True
    assert traffic._is_idc({"as": "AS137718 Beijing Volcano Engine"}) is True
    assert traffic._is_idc({"isp": "Huawei Cloud Service data center"}) is True
    assert traffic._is_idc({"isp": "Onyphe SAS"}) is True          # 测绘机构
    assert traffic._is_idc({"hosting": True}) is True
    assert traffic._is_idc({"proxy": True}) is True
    # 真实运营商不能误杀
    assert traffic._is_idc({"isp": "China Mobile Communications Corporation",
                            "as": "AS134810 China Mobile"}) is False
    assert traffic._is_idc({"isp": "Chinanet", "as": "AS4134 CHINANET"}) is False
    assert traffic._is_idc(None) is False


def test_敏感路径判定不误伤前端资源():
    assert traffic.is_sensitive_path("/.env") is True
    assert traffic.is_sensitive_path("/.git/config") is True
    assert traffic.is_sensitive_path("/wp-login.php") is True
    # /static/ 下的是正常前端资源，不能算敏感（之前这里出过误报）
    assert traffic.is_sensitive_path("/static/vendor/marked.min.js") is False
    assert traffic.is_sensitive_path("/static/style.css") is False
    assert traffic.is_sensitive_path("/") is False
