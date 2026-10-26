"""pytest 入口：把仓库根目录塞进 sys.path，测试里才能 import deck。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
