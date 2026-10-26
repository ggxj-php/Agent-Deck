# Taffy Agent 镜像
#   docker build -t taffy-agent .
#   docker run --rm -p 8000:8000 --env-file .env \
#       -v "$PWD/workspace:/app/workspace" -v "$PWD/knowledge:/app/knowledge" \
#       taffy-agent
#
# 容器里以非 root（uid 10001）运行，所以宿主机上要挂进去的目录得让它能写：
#   sudo chown -R 10001:10001 workspace knowledge .cache sessions
# （用 docker named volume 的话不用管，Docker 会按镜像里的属主建目录）
#
# 想让它能跑 run_code 的 C/C++ / Java / Node：
#   docker build --build-arg WITH_TOOLCHAINS=1 -t taffy-agent:full .
# （镜像会大 300MB 左右，只跑 Python 的话不用开）

FROM python:3.12-slim

ARG WITH_TOOLCHAINS=0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 可选的编译 / 运行环境：只有 run_code 跑 C/C++、Java、Node 时才需要
RUN if [ "$WITH_TOOLCHAINS" = "1" ]; then \
        apt-get update && \
        apt-get install -y --no-install-recommends g++ openjdk-17-jdk-headless nodejs && \
        rm -rf /var/lib/apt/lists/*; \
    fi

# 先装依赖，代码改动时不会把这一层缓存冲掉
COPY requirements.txt requirements.lock.txt ./
RUN python -m pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# 非 root 运行：容器里只跑应用，万一哪天被逃逸出去，拿到的也只是普通用户权限。
# 建个固定 uid（10001）方便宿主机上的目录授权；给 /app 的写权限是为了 .cache / workspace。
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 taffy \
    && mkdir -p workspace knowledge .cache sessions \
    && chown -R taffy:taffy /app

USER taffy

# 运行时数据目录（建议挂 volume，容器删了数据还在）
VOLUME ["/app/workspace", "/app/knowledge", "/app/.cache", "/app/sessions"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status else 1)"

CMD ["python", "-m", "taffy.web"]
