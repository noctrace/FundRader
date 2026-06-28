"""
Fund-Radar 阶段一：数据验证与核心算法原型
==========================================
功能：接收4个收益率阈值参数，联网抓取开放式基金排行，筛选并打印结果。
用法：python milestone1_fund_screener.py [y1] [m6] [m3] [m1]
示例：python milestone1_fund_screener.py 100 60 40 25
"""

import sys
import io
import akshare as ak
import pandas as pd

# 修复 Windows 控制台编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


def parse_args(argv: list[str]) -> tuple[float, float, float, float]:
    """解析命令行参数，返回 (近1年, 近6月, 近3月, 近1月) 收益率阈值。"""
    defaults = (100.0, 60.0, 40.0, 25.0)
    if len(argv) >= 5:
        try:
            return tuple(float(x) for x in argv[1:5])
        except ValueError:
            print("[警告] 参数解析失败，使用默认值: y1=100%, m6=60%, m3=40%, m1=25%")
            return defaults
    print("[提示] 未指定参数，使用默认值: y1=100%, m6=60%, m3=40%, m1=25%")
    return defaults


def fetch_fund_ranking() -> pd.DataFrame:
    """联网获取开放式基金排行数据。"""
    print("\n[数据抓取] 正在联网调用 akshare fund_open_fund_rank_em ...")
    df = ak.fund_open_fund_rank_em(symbol="全部")
    print(f"[数据抓取] 成功获取 {len(df)} 条基金数据")
    return df


def screen_funds(
    df: pd.DataFrame,
    y1_min: float,
    m6_min: float,
    m3_min: float,
    m1_min: float,
) -> pd.DataFrame:
    """按照四个收益率维度进行交集筛选。"""
    # 字段映射（akshare 返回的中文列名）
    col_map = {
        "近1年": "y1",
        "近6月": "m6",
        "近3月": "m3",
        "近1月": "m1",
    }

    # 检查列是否存在
    available_cols = df.columns.tolist()
    print(f"[调试] 原始列名: {available_cols}")

    # 动态匹配列名（兼容可能的列名变体）
    rename_map = {}
    for cn_name, en_name in col_map.items():
        for col in available_cols:
            if cn_name in col:
                rename_map[col] = en_name
                break

    df = df.rename(columns=rename_map)

    # 确保必要列存在
    required = ["y1", "m6", "m3", "m1"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"[错误] 缺少必要列: {missing}")
        print(f"[调试] 当前列: {df.columns.tolist()}")
        sys.exit(1)

    # 转换为数值类型，无法转换的设为 NaN
    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 筛选前数据量
    total = len(df)

    # 四维度交集筛选
    mask = (
        (df["y1"] >= y1_min)
        & (df["m6"] >= m6_min)
        & (df["m3"] >= m3_min)
        & (df["m1"] >= m1_min)
    )
    filtered = df[mask].copy()

    print(f"\n[筛选结果] 原始 {total} 条 -> 满足条件 {len(filtered)} 条")
    print(f"  筛选阈值: 近1年≥{y1_min}% | 近6月≥{m6_min}% | 近3月≥{m3_min}% | 近1月≥{m1_min}%")

    return filtered


def print_results(df: pd.DataFrame, top_n: int = 5) -> None:
    """格式化打印筛选结果。"""
    if df.empty:
        print("\n" + "=" * 60)
        print("⚠️  当前行情下暂无满足该硬性指标的基金")
        print("   请适当放宽标准后重试")
        print("=" * 60)
        return

    # 选择展示列
    display_cols = ["基金代码", "基金简称", "y1", "m6", "m3", "m1"]
    available = [c for c in display_cols if c in df.columns]

    # 补充可能的名称列变体
    if "基金简称" not in df.columns:
        for col in df.columns:
            if "名称" in col or "简称" in col:
                available[1] = col
                break

    result = df[available].head(top_n)

    print(f"\n{'=' * 80}")
    print(f"  📊 筛选结果 TOP {min(top_n, len(df))}（共 {len(df)} 条满足条件）")
    print(f"{'=' * 80}")

    # 打印表头
    header = f"{'序号':>4} | {'基金代码':<10} | {'基金名称':<20} | {'近1年%':>8} | {'近6月%':>8} | {'近3月%':>8} | {'近1月%':>8}"
    print(header)
    print("-" * 80)

    # 打印数据行
    for idx, (_, row) in enumerate(result.iterrows(), 1):
        code = str(row.get(available[0], "N/A"))
        name = str(row.get(available[1], "N/A"))[:18]
        y1 = f"{row.get('y1', 0):.2f}"
        m6 = f"{row.get('m6', 0):.2f}"
        m3 = f"{row.get('m3', 0):.2f}"
        m1 = f"{row.get('m1', 0):.2f}"
        print(f"{idx:>4} | {code:<10} | {name:<20} | {y1:>8} | {m6:>8} | {m3:>8} | {m1:>8}")

    print("=" * 80)


def main():
    """主入口。"""
    print("=" * 60)
    print("  Fund-Radar 阶段一：数据验证与核心算法原型")
    print("=" * 60)

    # 1. 解析参数
    y1, m6, m3, m1 = parse_args(sys.argv)

    # 2. 抓取数据
    df = fetch_fund_ranking()

    # 3. 筛选基金
    filtered = screen_funds(df, y1, m6, m3, m1)

    # 4. 打印结果
    print_results(filtered, top_n=5)

    # 5. 返回完整筛选结果供后续阶段使用
    return filtered


if __name__ == "__main__":
    result = main()
