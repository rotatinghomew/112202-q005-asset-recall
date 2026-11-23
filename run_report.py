#!/usr/bin/env python3
"""报告生成入口（隔离模式安全：不依赖调用目录，脚本目录自行入 sys.path）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from circulation.report import main

if __name__ == "__main__":
    main()
