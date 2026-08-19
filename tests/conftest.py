"""pytest 共享配置：让测试能直接 import backend 包。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
