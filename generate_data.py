"""
Fund-Radar 数据生成脚本
======================
由 GitHub Actions 每天调用，生成静态 JSON 数据文件。
也可本地运行：python generate_data.py
"""

import json
import os
import sys
import io
from datetime import datetime
from pathlib import Path

# 修复 Windows 控制台编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from fund_data import get_page_data

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

# 固定阈值
Y1, M6, M3, M1 = 100, 60, 40, 25


def main():
    print(f"{'='*60}")
    print(f"  Fund-Radar 静态数据生成")
    print(f"  阈值: y1≥{Y1}%  m6≥{M6}%  m3≥{M3}%  m1≥{M1}%")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # 抓取数据
    data = get_page_data(Y1, M6, M3, M1)

    # 确保 data/ 目录存在
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 写入高收益基金数据
    funds_path = DATA_DIR / "funds.json"
    with open(funds_path, "w", encoding="utf-8") as f:
        json.dump(data["fund_data"], f, ensure_ascii=False, indent=2)
    print(f"[写入] {funds_path} ({data['fund_count']} 条)")

    # 写入亏损基金数据
    loss_path = DATA_DIR / "loss_funds.json"
    with open(loss_path, "w", encoding="utf-8") as f:
        json.dump(data["loss_fund_data"], f, ensure_ascii=False, indent=2)
    print(f"[写入] {loss_path} ({data['loss_fund_count']} 条)")

    # 写入元数据
    meta_path = DATA_DIR / "meta.json"
    meta = {
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "thresholds": {"y1": Y1, "m6": M6, "m3": M3, "m1": M1},
        "fund_count": data["fund_count"],
        "loss_fund_count": data["loss_fund_count"],
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[写入] {meta_path}")

    print(f"\n{'='*60}")
    print(f"  生成完成！")
    print(f"  高收益基金: {data['fund_count']} 只")
    print(f"  亏损基金: {data['loss_fund_count']} 只")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
