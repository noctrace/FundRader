"""
Fund-Radar 阶段二：联动持仓数据深化
====================================
功能：在阶段一基础上，打通"基金->重仓股->行业板块"数据链路。
用法：python milestone2_portfolio_deep.py [y1] [m6] [m3] [m1]
示例：python milestone2_portfolio_deep.py 100 60 40 25
"""

import sys
import io
import time
import akshare as ak
import pandas as pd

# 修复 Windows 控制台编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ============================================================
# 阶段一：基金筛选逻辑（复用）
# ============================================================

def parse_args(argv: list[str]) -> tuple[float, float, float, float]:
    """解析命令行参数。"""
    defaults = (100.0, 60.0, 40.0, 25.0)
    if len(argv) >= 5:
        try:
            return tuple(float(x) for x in argv[1:5])
        except ValueError:
            print("[警告] 参数解析失败，使用默认值")
            return defaults
    print("[提示] 使用默认阈值: y1=100%, m6=60%, m3=40%, m1=25%")
    return defaults


def fetch_fund_ranking() -> pd.DataFrame:
    """联网获取开放式基金排行数据。"""
    print("\n[数据抓取] 正在联网获取开放式基金排行...")
    df = ak.fund_open_fund_rank_em(symbol="全部")
    print(f"[数据抓取] 成功获取 {len(df)} 条基金数据")
    return df


def screen_funds(df: pd.DataFrame, y1_min, m6_min, m3_min, m1_min) -> pd.DataFrame:
    """四维度交集筛选。"""
    col_map = {"近1年": "y1", "近6月": "m6", "近3月": "m3", "近1月": "m1"}
    rename_map = {}
    for cn_name, en_name in col_map.items():
        for col in df.columns:
            if cn_name in col:
                rename_map[col] = en_name
                break
    df = df.rename(columns=rename_map)

    for col in ["y1", "m6", "m3", "m1"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    mask = (df["y1"] >= y1_min) & (df["m6"] >= m6_min) & (df["m3"] >= m3_min) & (df["m1"] >= m1_min)
    filtered = df[mask].copy()
    print(f"[筛选] {len(df)} 条 -> {len(filtered)} 条满足条件")
    return filtered


# ============================================================
# 阶段二：持仓数据深化
# ============================================================

def get_fund_code(row: pd.Series) -> str:
    """从行数据中提取基金代码。"""
    for col in ["基金代码", "基金编号"]:
        if col in row.index:
            return str(row[col])
    return ""


def get_fund_name(row: pd.Series) -> str:
    """从行数据中提取基金名称。"""
    for col in ["基金简称", "基金名称"]:
        if col in row.index:
            return str(row[col])
    return ""


def fetch_fund_holdings(fund_code: str, year: str = "2024") -> pd.DataFrame:
    """获取单只基金的前十大重仓股。"""
    try:
        df = ak.fund_portfolio_hold_em(symbol=fund_code, date=year)
        # 只取前10条
        df = df.head(10).copy()
        return df
    except Exception as e:
        print(f"  [警告] 获取 {fund_code} 持仓失败: {e}")
        return pd.DataFrame()


def fetch_fund_industry(fund_code: str, year: str = "2024") -> pd.DataFrame:
    """获取单只基金的行业配置（只取最新一期）。"""
    try:
        df = ak.fund_portfolio_industry_allocation_em(symbol=fund_code, date=year)
        if df.empty:
            return df
        # 只取最新一期的数据（最后一列是报告期）
        last_col = df.columns[-1]
        if "时间" in str(last_col) or "期" in str(last_col):
            latest_date = df[last_col].max()
            df = df[df[last_col] == latest_date].copy()
        return df
    except Exception as e:
        print(f"  [警告] 获取 {fund_code} 行业配置失败: {e}")
        return pd.DataFrame()


def build_fund_detail(fund_code: str, fund_name: str, year: str = "2024") -> dict:
    """构建单只基金的完整持仓详情。"""
    print(f"\n{'='*60}")
    print(f"  基金: {fund_code} - {fund_name}")
    print(f"{'='*60}")

    # 获取重仓股
    holdings = fetch_fund_holdings(fund_code, year)
    # 获取行业配置
    industry = fetch_fund_industry(fund_code, year)

    result = {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "holdings": holdings,
        "industry": industry,
    }

    # 打印重仓股
    if not holdings.empty:
        print(f"\n  [前十大重仓股] (共 {len(holdings)} 只)")
        print(f"  {'序号':>4} | {'股票代码':<8} | {'股票名称':<12} | {'占净值%':>8} | {'持仓市值(万)':>12}")
        print(f"  {'-'*55}")
        for _, row in holdings.iterrows():
            seq = row.get("序号", "")
            code = str(row.get("股票代码", ""))
            name = str(row.get("股票名称", ""))[:10]
            ratio = f"{row.get('占净值比例', 0):.2f}"
            value = f"{row.get('持仓市值', 0):.2f}"
            print(f"  {seq:>4} | {code:<8} | {name:<12} | {ratio:>8} | {value:>12}")
    else:
        print("  [重仓股] 暂无数据")

    # 打印行业配置（使用列索引，避免中文列名编码问题）
    if not industry.empty:
        print(f"\n  [行业板块配置]")
        print(f"  {'序号':>4} | {'行业名称':<30} | {'占净值%':>8}")
        print(f"  {'-'*50}")
        for _, row in industry.iterrows():
            seq = row.iloc[0] if len(row) > 0 else ""
            name = str(row.iloc[1])[:28] if len(row) > 1 else ""
            ratio = f"{row.iloc[2]:.2f}" if len(row) > 2 else "0.00"
            print(f"  {seq:>4} | {name:<30} | {ratio:>8}")
    else:
        print("  [行业配置] 暂无数据")

    return result


def process_fund_list(filtered_df: pd.DataFrame, max_funds: int = 5, year: str = "2024") -> list[dict]:
    """批量处理筛选出的基金，获取持仓详情。"""
    results = []
    funds_to_process = filtered_df.head(max_funds)

    print(f"\n\n{'#'*60}")
    print(f"# 阶段二：开始获取前 {len(funds_to_process)} 只基金的持仓数据")
    print(f"{'#'*60}")

    for idx, (_, row) in enumerate(funds_to_process.iterrows(), 1):
        code = get_fund_code(row)
        name = get_fund_name(row)

        if not code:
            print(f"[跳过] 第 {idx} 行无法获取基金代码")
            continue

        detail = build_fund_detail(code, name, year)
        results.append(detail)

        # 请求间隔，避免频率限制
        if idx < len(funds_to_process):
            print(f"\n  [等待] 休息 1 秒后继续...")
            time.sleep(1)

    return results


def print_summary(results: list[dict]) -> None:
    """打印汇总信息。"""
    print(f"\n\n{'='*60}")
    print(f"  📊 阶段二数据汇总")
    print(f"{'='*60}")

    for r in results:
        holdings_count = len(r["holdings"]) if not r["holdings"].empty else 0
        industry_count = len(r["industry"]) if not r["industry"].empty else 0
        print(f"  {r['fund_code']} | {r['fund_name'][:20]:<20} | 重仓股: {holdings_count:>2} 只 | 行业: {industry_count:>2} 个")

    print(f"{'='*60}")
    print(f"  共处理 {len(results)} 只基金")
    print(f"{'='*60}")


def main():
    """主入口。"""
    print("=" * 60)
    print("  Fund-Radar 阶段二：联动持仓数据深化")
    print("=" * 60)

    # 1. 解析参数
    y1, m6, m3, m1 = parse_args(sys.argv)

    # 2. 抓取基金排行
    df = fetch_fund_ranking()

    # 3. 筛选基金
    filtered = screen_funds(df, y1, m6, m3, m1)

    if filtered.empty:
        print("\n⚠️  当前行情下暂无满足该硬性指标的基金")
        return []

    # 4. 获取持仓详情（默认处理前5只）
    results = process_fund_list(filtered, max_funds=5, year="2024")

    # 5. 打印汇总
    print_summary(results)

    return results


if __name__ == "__main__":
    results = main()
