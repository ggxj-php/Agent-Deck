"""python -m taffy.web 起服务。

监听 0.0.0.0，同一个 wifi 下手机直接访问 http://<电脑局域网IP>:8000 就能用。
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("taffy.web.app:app", host="0.0.0.0", port=8000)
