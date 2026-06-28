"""
Fund-Radar 阶段四：全链路生产级服务
====================================
功能：URL参数传参 + 实时数据抓取 + 服务端渲染
用法：python app_server.py
访问：http://localhost:8080
      http://localhost:8080?y1=100&m6=60&m3=40&m1=25
"""

import http.server
import socketserver
import urllib.parse
import json
import time
import sys
import io
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import akshare as ak
import pandas as pd

# 修复 Windows 控制台编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

PORT = 8080

# ============================================================
# 数据抓取模块（复用阶段一、二逻辑）
# ============================================================

def fetch_fund_ranking() -> pd.DataFrame:
    """联网获取开放式基金排行数据。"""
    print("[数据抓取] 正在获取开放式基金排行...")
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
    return df[mask].copy()


def get_fund_code(row: pd.Series) -> str:
    for col in ["基金代码", "基金编号"]:
        if col in row.index:
            return str(row[col])
    return ""


def get_fund_name(row: pd.Series) -> str:
    for col in ["基金简称", "基金名称"]:
        if col in row.index:
            return str(row[col])
    return ""


def fetch_fund_holdings(fund_code: str, year: str = "2026") -> tuple:
    """获取单只基金的前十大重仓股，返回 (holdings_list, report_date)。"""
    try:
        df = ak.fund_portfolio_hold_em(symbol=fund_code, date=year)
        if df.empty:
            return [], ""
        # 获取报告期（最后一列）
        report_date = str(df.iloc[0, -1]) if len(df.columns) > 0 else ""
        df = df.head(10)
        holdings = []
        for _, row in df.iterrows():
            holdings.append({
                "code": str(row.get("股票代码", "")),
                "name": str(row.get("股票名称", "")),
                "ratio": float(row.get("占净值比例", 0))
            })
        return holdings, report_date
    except Exception as e:
        print(f"  [警告] 获取 {fund_code} 重仓股失败: {e}")
        return [], ""


def fetch_fund_industry(fund_code: str, year: str = "2026") -> list:
    """获取单只基金的行业配置（只取最新一期）。"""
    try:
        df = ak.fund_portfolio_industry_allocation_em(symbol=fund_code, date=year)
        if df.empty:
            return []
        # 只取最新一期
        last_col = df.columns[-1]
        if "时间" in str(last_col) or "期" in str(last_col):
            latest_date = df[last_col].max()
            df = df[df[last_col] == latest_date].copy()

        industry = []
        for _, row in df.iterrows():
            ratio = float(row.iloc[2]) if len(row) > 2 else 0
            if ratio > 0:
                industry.append({
                    "name": str(row.iloc[1]) if len(row) > 1 else "",
                    "ratio": ratio
                })
        return industry
    except Exception as e:
        print(f"  [警告] 获取 {fund_code} 行业配置失败: {e}")
        return []


def fetch_single_fund_data(row: pd.Series) -> dict:
    """获取单只基金的完整数据（用于并行处理）。"""
    code = get_fund_code(row)
    name = get_fund_name(row)

    if not code:
        return None

    holdings, report_date = fetch_fund_holdings(code)
    industry = fetch_fund_industry(code)

    return {
        "code": code,
        "name": name,
        "y1": float(row.get("y1", 0)),
        "m6": float(row.get("m6", 0)),
        "m3": float(row.get("m3", 0)),
        "m1": float(row.get("m1", 0)),
        "holdings": holdings,
        "industry": industry,
        "report_date": report_date
    }


def build_fund_list(filtered_df: pd.DataFrame, max_funds: int = 20) -> list:
    """构建基金列表数据（并行获取持仓）。"""
    funds_to_process = filtered_df.head(max_funds)
    total = len(funds_to_process)

    print(f"[数据处理] 开始并行获取前 {total} 只基金的持仓数据...")

    funds = []
    # 使用线程池并行获取，最多6个并发
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(fetch_single_fund_data, row): idx
            for idx, (_, row) in enumerate(funds_to_process.iterrows(), 1)
        }

        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                if result:
                    funds.append(result)
                    print(f"  [{len(funds)}/{total}] 完成 {result['code']}")
            except Exception as e:
                print(f"  [错误] 第 {idx} 只基金处理失败: {e}")

    # 按原始顺序排序
    code_order = {get_fund_code(row): i for i, (_, row) in enumerate(funds_to_process.iterrows())}
    funds.sort(key=lambda x: code_order.get(x["code"], 999))

    return funds


# ============================================================
# HTML模板
# ============================================================

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Fund-Radar 基金筛选看板</title>
    <link rel="icon" href="/icon/icon.jpg?v=2" type="image/jpeg">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            theme: {{
                extend: {{
                    fontFamily: {{
                        sans: ['Outfit', 'system-ui', 'sans-serif'],
                    }},
                }},
            }},
        }}
    </script>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <style>
        ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
        ::-webkit-scrollbar-track {{ background: #f1f5f9; }}
        ::-webkit-scrollbar-thumb {{ background: #94a3b8; border-radius: 3px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: #64748b; }}
        .fund-row {{ transition: background-color 0.2s ease, transform 0.15s ease, box-shadow 0.15s ease; cursor: pointer; }}
        .fund-row:hover {{ background-color: #eff6ff; transform: translateX(3px); box-shadow: inset 3px 0 0 #93c5fd; }}
        .fund-row.active {{ background-color: #dbeafe; box-shadow: inset 3px 0 0 #3b82f6; }}
        .chart-panel {{ transition: box-shadow 0.3s ease, transform 0.2s ease; }}
        .chart-panel:hover {{ box-shadow: 0 4px 24px rgba(0,0,0,0.08); }}
        .loading-spinner {{
            border: 3px solid #e2e8f0; border-top: 3px solid #3b82f6;
            border-radius: 50%; width: 24px; height: 24px;
            animation: spin 0.8s linear infinite;
        }}
        @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
        .fade-in {{ animation: fadeIn 0.4s ease-out; }}
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(8px); }} to {{ opacity: 1; transform: translateY(0); }} }}
        .num {{ font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; letter-spacing: -0.01em; }}
        .btn-press:active {{ transform: scale(0.97); }}
    </style>
</head>
<body class="bg-gray-50 min-h-screen">
    <header class="bg-white shadow-sm border-b border-gray-200">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <div class="flex items-center justify-between">
                <div class="flex items-center space-x-3">
                    <div class="w-10 h-10 bg-blue-600 rounded-lg flex items-center justify-center">
                        <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"></path>
                        </svg>
                    </div>
                    <div>
                        <h1 class="text-xl font-bold text-gray-900">Fund-Radar</h1>
                        <p class="text-sm text-gray-500">动态基金筛选与重仓股可视化看板</p>
                    </div>
                </div>
                <div class="text-sm text-gray-500">
                    数据更新时间：{update_time}
                </div>
            </div>
        </div>
    </header>

    <section class="bg-white border-b border-gray-200 shadow-sm">
        <div class="max-w-7xl mx-auto px-4 py-4">
            <form id="searchForm" class="flex flex-wrap items-end gap-4">
                <div class="flex-1 min-w-[150px]">
                    <label class="block text-sm font-medium text-gray-700 mb-1">近1年收益率 ≥</label>
                    <div class="relative">
                        <input type="number" id="inputY1" name="y1" value="{y1}" min="0" max="500"
                               class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors">
                        <span class="absolute right-3 top-2 text-gray-400">%</span>
                    </div>
                </div>
                <div class="flex-1 min-w-[150px]">
                    <label class="block text-sm font-medium text-gray-700 mb-1">近6月收益率 ≥</label>
                    <div class="relative">
                        <input type="number" id="inputM6" name="m6" value="{m6}" min="0" max="300"
                               class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors">
                        <span class="absolute right-3 top-2 text-gray-400">%</span>
                    </div>
                </div>
                <div class="flex-1 min-w-[150px]">
                    <label class="block text-sm font-medium text-gray-700 mb-1">近3月收益率 ≥</label>
                    <div class="relative">
                        <input type="number" id="inputM3" name="m3" value="{m3}" min="0" max="200"
                               class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors">
                        <span class="absolute right-3 top-2 text-gray-400">%</span>
                    </div>
                </div>
                <div class="flex-1 min-w-[150px]">
                    <label class="block text-sm font-medium text-gray-700 mb-1">近1月收益率 ≥</label>
                    <div class="relative">
                        <input type="number" id="inputM1" name="m1" value="{m1}" min="0" max="100"
                               class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-colors">
                        <span class="absolute right-3 top-2 text-gray-400">%</span>
                    </div>
                </div>
                <button type="submit"
                        class="btn-press px-6 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-all duration-200 flex items-center space-x-2 font-medium tracking-wide">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                    </svg>
                    <span>开始查询</span>
                </button>
            </form>
        </div>
    </section>

    <main class="max-w-7xl mx-auto px-4 py-6">
        <!-- 上方：高收益基金 -->
        <div class="mb-8">
            <h2 class="text-xl font-bold text-gray-800 mb-4 flex items-center tracking-tight">
                <span class="w-2 h-6 bg-green-500 rounded mr-2"></span>
                高收益基金（满足筛选条件）
            </h2>
            <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
                <div class="lg:col-span-7 bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                    <div class="px-4 py-3 bg-gray-50 border-b border-gray-200 flex justify-between items-center">
                        <h3 class="text-lg font-semibold text-gray-800">筛选结果</h3>
                        <span id="fundCount" class="px-3 py-1 bg-green-100 text-green-700 rounded-full text-sm font-medium">{fund_count} 只</span>
                    </div>
                    <div class="overflow-auto" style="max-height: 728px">
                        <table class="w-full">
                            <thead class="bg-gray-50 sticky top-0">
                                <tr>
                                    <th class="px-3 py-2 text-left text-[0.65rem] font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700 whitespace-nowrap" onclick="sortTable('code')">代码 <span class="sort-icon">↕</span></th>
                                    <th class="px-3 py-2 text-left text-[0.65rem] font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700" onclick="sortTable('name')">基金名称 <span class="sort-icon">↕</span></th>
                                    <th class="px-3 py-2 text-right text-[0.65rem] font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700 whitespace-nowrap" onclick="sortTable('y1')">近1年 <span class="sort-icon">↕</span></th>
                                    <th class="px-3 py-2 text-right text-[0.65rem] font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700 whitespace-nowrap" onclick="sortTable('m6')">近6月 <span class="sort-icon">↕</span></th>
                                    <th class="px-3 py-2 text-right text-[0.65rem] font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700 whitespace-nowrap" onclick="sortTable('m3')">近3月 <span class="sort-icon">↕</span></th>
                                    <th class="px-3 py-2 text-right text-[0.65rem] font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700 whitespace-nowrap" onclick="sortTable('m1')">近1月 <span class="sort-icon">↕</span></th>
                                </tr>
                            </thead>
                            <tbody id="fundTableBody" class="divide-y divide-gray-100">
                            </tbody>
                        </table>
                    </div>
                    <div id="emptyState" class="hidden p-8 text-center">
                        <svg class="w-16 h-16 mx-auto text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        <p class="text-gray-500 text-lg">当前行情下暂无满足该硬性指标的基金</p>
                        <p class="text-gray-400 text-sm mt-2">请适当放宽标准后重试</p>
                    </div>
                </div>

                <div class="lg:col-span-5 space-y-6">
                    <div class="bg-white rounded-xl shadow-sm border border-gray-200 chart-panel">
                        <div class="px-4 py-3 bg-gray-50 border-b border-gray-200">
                            <h3 class="text-lg font-semibold text-gray-800">前十大重仓股持仓权重 <span id="reportDate" class="text-sm font-normal text-gray-500"></span></h3>
                            <p id="holdingsFundName" class="text-sm text-gray-500 mt-1">点击左侧基金查看</p>
                        </div>
                        <div id="holdingsChart" class="h-[300px]"></div>
                    </div>
                    <div class="bg-white rounded-xl shadow-sm border border-gray-200 chart-panel">
                        <div class="px-4 py-3 bg-gray-50 border-b border-gray-200">
                            <h3 class="text-lg font-semibold text-gray-800">行业板块分布</h3>
                            <p id="industryFundName" class="text-sm text-gray-500 mt-1">点击左侧基金查看</p>
                        </div>
                        <div id="industryChart" class="h-[300px]"></div>
                    </div>
                </div>
            </div>
        </div>

        <!-- 下方：亏损基金 -->
        <div class="border-t-2 border-gray-200 pt-8">
            <h2 class="text-xl font-bold text-gray-800 mb-4 flex items-center tracking-tight">
                <span class="w-2 h-6 bg-red-500 rounded mr-2"></span>
                亏损基金（近1月收益率 ≤ -15%）
            </h2>
            <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
                <div class="lg:col-span-7 bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                    <div class="px-4 py-3 bg-gray-50 border-b border-gray-200 flex justify-between items-center">
                        <h3 class="text-lg font-semibold text-gray-800">筛选结果</h3>
                        <span id="lossFundCount" class="px-3 py-1 bg-red-100 text-red-700 rounded-full text-sm font-medium">{loss_fund_count} 只</span>
                    </div>
                    <div class="overflow-auto" style="max-height: 728px">
                        <table class="w-full">
                            <thead class="bg-gray-50 sticky top-0">
                                <tr>
                                    <th class="px-3 py-2 text-left text-[0.65rem] font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700 whitespace-nowrap" onclick="sortLossTable('code')">代码 <span class="sort-icon">↕</span></th>
                                    <th class="px-3 py-2 text-left text-[0.65rem] font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700" onclick="sortLossTable('name')">基金名称 <span class="sort-icon">↕</span></th>
                                    <th class="px-3 py-2 text-right text-[0.65rem] font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700 whitespace-nowrap" onclick="sortLossTable('y1')">近1年 <span class="sort-icon">↕</span></th>
                                    <th class="px-3 py-2 text-right text-[0.65rem] font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700 whitespace-nowrap" onclick="sortLossTable('m6')">近6月 <span class="sort-icon">↕</span></th>
                                    <th class="px-3 py-2 text-right text-[0.65rem] font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700 whitespace-nowrap" onclick="sortLossTable('m3')">近3月 <span class="sort-icon">↕</span></th>
                                    <th class="px-3 py-2 text-right text-[0.65rem] font-semibold text-gray-500 uppercase tracking-wider cursor-pointer hover:text-gray-700 whitespace-nowrap" onclick="sortLossTable('m1')">近1月 <span class="sort-icon">↕</span></th>
                                </tr>
                            </thead>
                            <tbody id="lossFundTableBody" class="divide-y divide-gray-100">
                            </tbody>
                        </table>
                    </div>
                    <div id="lossEmptyState" class="hidden p-8 text-center">
                        <svg class="w-16 h-16 mx-auto text-gray-300 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        <p class="text-gray-500 text-lg">当前无近1月亏损超15%的基金</p>
                    </div>
                </div>

                <div class="lg:col-span-5 space-y-6">
                    <div class="bg-white rounded-xl shadow-sm border border-gray-200 chart-panel">
                        <div class="px-4 py-3 bg-gray-50 border-b border-gray-200">
                            <h3 class="text-lg font-semibold text-gray-800">前十大重仓股持仓权重 <span id="lossReportDate" class="text-sm font-normal text-gray-500"></span></h3>
                            <p id="lossHoldingsFundName" class="text-sm text-gray-500 mt-1">点击左侧基金查看</p>
                        </div>
                        <div id="lossHoldingsChart" class="h-[300px]"></div>
                    </div>
                    <div class="bg-white rounded-xl shadow-sm border border-gray-200 chart-panel">
                        <div class="px-4 py-3 bg-gray-50 border-b border-gray-200">
                            <h3 class="text-lg font-semibold text-gray-800">行业板块分布</h3>
                            <p id="lossIndustryFundName" class="text-sm text-gray-500 mt-1">点击左侧基金查看</p>
                        </div>
                        <div id="lossIndustryChart" class="h-[300px]"></div>
                    </div>
                </div>
            </div>
        </div>
    </main>

    <script>
    const FUND_DATA = {fund_data};
    const LOSS_FUND_DATA = {loss_fund_data};

    let currentFunds = [...FUND_DATA];
    let currentLossFunds = [...LOSS_FUND_DATA];
    let selectedFund = null;
    let selectedLossFund = null;
    let sortField = null;
    let sortAsc = true;
    let lossSortField = null;
    let lossSortAsc = true;
    let holdingsChart = null;
    let industryChart = null;
    let lossHoldingsChart = null;
    let lossIndustryChart = null;
    let displayedCount = 0;
    let lossDisplayedCount = 0;
    const PAGE_SIZE = 30;
    let isLoading = false;
    let isLossLoading = false;

    document.addEventListener('DOMContentLoaded', () => {{
        // 初始化高收益基金图表
        holdingsChart = echarts.init(document.getElementById('holdingsChart'));
        industryChart = echarts.init(document.getElementById('industryChart'));

        // 初始化亏损基金图表
        lossHoldingsChart = echarts.init(document.getElementById('lossHoldingsChart'));
        lossIndustryChart = echarts.init(document.getElementById('lossIndustryChart'));

        window.addEventListener('resize', () => {{
            holdingsChart?.resize();
            industryChart?.resize();
            lossHoldingsChart?.resize();
            lossIndustryChart?.resize();
        }});

        // 初始化高收益基金显示
        document.getElementById('fundCount').textContent = `${{currentFunds.length}} 只`;
        if (currentFunds.length === 0) {{
            document.getElementById('emptyState').classList.remove('hidden');
        }} else {{
            loadMoreFunds();
            setupScrollLoad('fundTableBody', loadMoreFunds);
            setTimeout(() => selectFund(currentFunds[0].code), 300);
        }}

        // 初始化亏损基金显示
        document.getElementById('lossFundCount').textContent = `${{currentLossFunds.length}} 只`;
        if (currentLossFunds.length === 0) {{
            document.getElementById('lossEmptyState').classList.remove('hidden');
        }} else {{
            loadMoreLossFunds();
            setupScrollLoad('lossFundTableBody', loadMoreLossFunds);
            setTimeout(() => selectLossFund(currentLossFunds[0].code), 300);
        }}
    }});

    function setupScrollLoad(tbodyId, loadFunc) {{
        const tbody = document.getElementById(tbodyId);
        if (!tbody) return;
        const container = tbody.closest('.overflow-auto');
        if (!container) return;
        container.addEventListener('scroll', () => {{
            const {{ scrollTop, scrollHeight, clientHeight }} = container;
            if (scrollTop + clientHeight >= scrollHeight - 50) {{
                loadFunc();
            }}
        }});
    }}

    function loadMoreFunds() {{
        if (displayedCount >= currentFunds.length) return;
        isLoading = true;

        const tbody = document.getElementById('fundTableBody');
        const end = Math.min(displayedCount + PAGE_SIZE, currentFunds.length);

        for (let i = displayedCount; i < end; i++) {{
            const fund = currentFunds[i];
            const tr = document.createElement('tr');
            tr.className = 'fund-row fade-in';
            tr.style.animationDelay = `${{(i - displayedCount) * 30}}ms`;
            tr.onclick = () => selectFund(fund.code);
            tr.innerHTML = `
                <td class="px-3 py-2 text-sm font-medium text-gray-900">${{fund.code}}</td>
                <td class="px-3 py-2 text-sm text-gray-700 truncate" style="max-width:180px" title="${{fund.name}}">${{fund.name}}</td>
                <td class="px-3 py-2 text-sm text-right font-medium num ${{fund.y1 >= 100 ? 'text-green-600' : 'text-gray-700'}} whitespace-nowrap">${{fund.y1.toFixed(2)}}%</td>
                <td class="px-3 py-2 text-sm text-right font-medium num ${{fund.m6 >= 60 ? 'text-green-600' : 'text-gray-700'}} whitespace-nowrap">${{fund.m6.toFixed(2)}}%</td>
                <td class="px-3 py-2 text-sm text-right font-medium num ${{fund.m3 >= 40 ? 'text-green-600' : 'text-gray-700'}} whitespace-nowrap">${{fund.m3.toFixed(2)}}%</td>
                <td class="px-3 py-2 text-sm text-right font-medium num ${{fund.m1 >= 25 ? 'text-green-600' : 'text-gray-700'}} whitespace-nowrap">${{fund.m1.toFixed(2)}}%</td>
            `;
            tbody.appendChild(tr);
        }}

        displayedCount = end;
        isLoading = false;
    }}

    function loadMoreLossFunds() {{
        if (lossDisplayedCount >= currentLossFunds.length) return;
        isLossLoading = true;

        const tbody = document.getElementById('lossFundTableBody');
        const end = Math.min(lossDisplayedCount + PAGE_SIZE, currentLossFunds.length);

        for (let i = lossDisplayedCount; i < end; i++) {{
            const fund = currentLossFunds[i];
            const tr = document.createElement('tr');
            tr.className = 'fund-row fade-in';
            tr.style.animationDelay = `${{(i - lossDisplayedCount) * 30}}ms`;
            tr.onclick = () => selectLossFund(fund.code);
            tr.innerHTML = `
                <td class="px-3 py-2 text-sm font-medium text-gray-900">${{fund.code}}</td>
                <td class="px-3 py-2 text-sm text-gray-700 truncate" style="max-width:180px" title="${{fund.name}}">${{fund.name}}</td>
                <td class="px-3 py-2 text-sm text-right font-medium num whitespace-nowrap ${{fund.y1 >= 0 ? 'text-green-600' : 'text-red-600'}}">${{fund.y1.toFixed(2)}}%</td>
                <td class="px-3 py-2 text-sm text-right font-medium num whitespace-nowrap ${{fund.m6 >= 0 ? 'text-green-600' : 'text-red-600'}}">${{fund.m6.toFixed(2)}}%</td>
                <td class="px-3 py-2 text-sm text-right font-medium num whitespace-nowrap ${{fund.m3 >= 0 ? 'text-green-600' : 'text-red-600'}}">${{fund.m3.toFixed(2)}}%</td>
                <td class="px-3 py-2 text-sm text-right font-medium num whitespace-nowrap ${{fund.m1 >= 0 ? 'text-green-600' : 'text-red-600'}}">${{fund.m1.toFixed(2)}}%</td>
            `;
            tbody.appendChild(tr);
        }}

        lossDisplayedCount = end;
        isLossLoading = false;
    }}

    // URL参数传参刷新机制
    document.getElementById('searchForm').addEventListener('submit', (e) => {{
        e.preventDefault();
        const y1 = document.getElementById('inputY1').value || '100';
        const m6 = document.getElementById('inputM6').value || '60';
        const m3 = document.getElementById('inputM3').value || '40';
        const m1 = document.getElementById('inputM1').value || '25';
        window.location.href = `/?y1=${{y1}}&m6=${{m6}}&m3=${{m3}}&m1=${{m1}}`;
    }});

    function resetAndRender() {{
        const tbody = document.getElementById('fundTableBody');
        const emptyState = document.getElementById('emptyState');
        const fundCount = document.getElementById('fundCount');

        fundCount.textContent = `${{currentFunds.length}} 只`;
        tbody.innerHTML = '';
        displayedCount = 0;

        if (currentFunds.length === 0) {{
            emptyState.classList.remove('hidden');
            return;
        }}

        emptyState.classList.add('hidden');
        loadMoreFunds();
    }}

    function selectFund(code) {{
        selectedFund = currentFunds.find(f => f.code === code);
        if (!selectedFund) return;

        document.querySelectorAll('#fundTableBody .fund-row').forEach(row => row.classList.remove('active'));
        const rows = document.querySelectorAll('#fundTableBody .fund-row');
        rows.forEach(row => {{
            if (row.querySelector('td')?.textContent === code) {{
                row.classList.add('active');
            }}
        }});

        renderHoldingsChart(selectedFund);
        renderIndustryChart(selectedFund);
    }}

    function selectLossFund(code) {{
        selectedLossFund = currentLossFunds.find(f => f.code === code);
        if (!selectedLossFund) return;

        document.querySelectorAll('#lossFundTableBody .fund-row').forEach(row => row.classList.remove('active'));
        const rows = document.querySelectorAll('#lossFundTableBody .fund-row');
        rows.forEach(row => {{
            if (row.querySelector('td')?.textContent === code) {{
                row.classList.add('active');
            }}
        }});

        renderLossHoldingsChart(selectedLossFund);
        renderLossIndustryChart(selectedLossFund);
    }}

    function renderHoldingsChart(fund) {{
        document.getElementById('holdingsFundName').textContent = `${{fund.code}} - ${{fund.name}}`;
        document.getElementById('reportDate').textContent = fund.report_date ? `(${{fund.report_date}})` : '';

        const data = fund.holdings.map(h => ({{ name: h.name, value: h.ratio }}));
        const top10Total = data.reduce((sum, d) => sum + d.value, 0);
        if (top10Total < 100) {{
            data.push({{ name: '其他持仓', value: parseFloat((100 - top10Total).toFixed(2)) }});
        }}

        holdingsChart.setOption({{
            tooltip: {{ trigger: 'item', formatter: '{{b}}: {{c}}% ({{d}}%)' }},
            legend: {{ orient: 'vertical', right: '5%', top: 'center', textStyle: {{ fontSize: 13 }} }},
            series: [{{
                name: '持仓权重', type: 'pie', radius: ['40%', '70%'], center: ['40%', '50%'],
                avoidLabelOverlap: true,
                itemStyle: {{ borderRadius: 6, borderColor: '#fff', borderWidth: 2 }},
                label: {{ show: true, formatter: '{{b}}\\n{{c}}%', fontSize: 14 }},
                emphasis: {{ label: {{ show: true, fontSize: 18, fontWeight: 'bold' }}, itemStyle: {{ shadowBlur: 10 }} }},
                animationType: 'scale', animationEasing: 'elasticOut',
                animationDelay: (idx) => idx * 100,
                data: data
            }}]
        }}, true);
    }}

    function renderIndustryChart(fund) {{
        document.getElementById('industryFundName').textContent = `${{fund.code}} - ${{fund.name}}`;

        const data = fund.industry.filter(i => i.ratio > 0).map(i => ({{
            name: i.name.length > 12 ? i.name.substring(0, 12) + '...' : i.name,
            fullName: i.name, value: i.ratio
        }}));

        industryChart.setOption({{
            tooltip: {{ trigger: 'item', formatter: (params) => {{ const item = data.find(d => d.name === params.name); return `${{item?.fullName || params.name}}: ${{params.value}}% (${{params.percent}}%)`; }} }},
            legend: {{ orient: 'vertical', right: '5%', top: 'center', textStyle: {{ fontSize: 13 }} }},
            series: [{{
                name: '行业分布', type: 'pie', radius: ['40%', '70%'], center: ['40%', '50%'],
                avoidLabelOverlap: true,
                itemStyle: {{ borderRadius: 6, borderColor: '#fff', borderWidth: 2 }},
                label: {{ show: true, formatter: '{{b}}\\n{{c}}%', fontSize: 14 }},
                emphasis: {{ label: {{ show: true, fontSize: 18, fontWeight: 'bold' }} }},
                animationType: 'scale', animationEasing: 'elasticOut',
                animationDelay: (idx) => idx * 150,
                data: data
            }}]
        }}, true);
    }}

    function renderLossHoldingsChart(fund) {{
        document.getElementById('lossHoldingsFundName').textContent = `${{fund.code}} - ${{fund.name}}`;
        document.getElementById('lossReportDate').textContent = fund.report_date ? `(${{fund.report_date}})` : '';

        const data = fund.holdings.map(h => ({{ name: h.name, value: h.ratio }}));
        const top10Total = data.reduce((sum, d) => sum + d.value, 0);
        if (top10Total < 100) {{
            data.push({{ name: '其他持仓', value: parseFloat((100 - top10Total).toFixed(2)) }});
        }}

        lossHoldingsChart.setOption({{
            tooltip: {{ trigger: 'item', formatter: '{{b}}: {{c}}% ({{d}}%)' }},
            legend: {{ orient: 'vertical', right: '5%', top: 'center', textStyle: {{ fontSize: 13 }} }},
            series: [{{
                name: '持仓权重', type: 'pie', radius: ['40%', '70%'], center: ['40%', '50%'],
                avoidLabelOverlap: true,
                itemStyle: {{ borderRadius: 6, borderColor: '#fff', borderWidth: 2 }},
                label: {{ show: true, formatter: '{{b}}\\n{{c}}%', fontSize: 14 }},
                emphasis: {{ label: {{ show: true, fontSize: 18, fontWeight: 'bold' }}, itemStyle: {{ shadowBlur: 10 }} }},
                animationType: 'scale', animationEasing: 'elasticOut',
                animationDelay: (idx) => idx * 100,
                data: data
            }}]
        }}, true);
    }}

    function renderLossIndustryChart(fund) {{
        document.getElementById('lossIndustryFundName').textContent = `${{fund.code}} - ${{fund.name}}`;

        const data = fund.industry.filter(i => i.ratio > 0).map(i => ({{
            name: i.name.length > 12 ? i.name.substring(0, 12) + '...' : i.name,
            fullName: i.name, value: i.ratio
        }}));

        lossIndustryChart.setOption({{
            tooltip: {{ trigger: 'item', formatter: (params) => {{ const item = data.find(d => d.name === params.name); return `${{item?.fullName || params.name}}: ${{params.value}}% (${{params.percent}}%)`; }} }},
            legend: {{ orient: 'vertical', right: '5%', top: 'center', textStyle: {{ fontSize: 13 }} }},
            series: [{{
                name: '行业分布', type: 'pie', radius: ['40%', '70%'], center: ['40%', '50%'],
                avoidLabelOverlap: true,
                itemStyle: {{ borderRadius: 6, borderColor: '#fff', borderWidth: 2 }},
                label: {{ show: true, formatter: '{{b}}\\n{{c}}%', fontSize: 14 }},
                emphasis: {{ label: {{ show: true, fontSize: 18, fontWeight: 'bold' }} }},
                animationType: 'scale', animationEasing: 'elasticOut',
                animationDelay: (idx) => idx * 150,
                data: data
            }}]
        }}, true);
    }}

    function sortTable(field) {{
        if (sortField === field) {{ sortAsc = !sortAsc; }} else {{ sortField = field; sortAsc = true; }}

        currentFunds.sort((a, b) => {{
            let valA, valB;
            switch (field) {{
                case 'code': valA = a.code; valB = b.code; break;
                case 'name': valA = a.name; valB = b.name; break;
                case 'y1': valA = a.y1; valB = b.y1; break;
                case 'm6': valA = a.m6; valB = b.m6; break;
                case 'm3': valA = a.m3; valB = b.m3; break;
                case 'm1': valA = a.m1; valB = b.m1; break;
                default: return 0;
            }}
            if (typeof valA === 'string') {{ return sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA); }}
            return sortAsc ? valA - valB : valB - valA;
        }});

        resetAndRender();
    }}

    function sortLossTable(field) {{
        if (lossSortField === field) {{ lossSortAsc = !lossSortAsc; }} else {{ lossSortField = field; lossSortAsc = true; }}

        currentLossFunds.sort((a, b) => {{
            let valA, valB;
            switch (field) {{
                case 'code': valA = a.code; valB = b.code; break;
                case 'name': valA = a.name; valB = b.name; break;
                case 'y1': valA = a.y1; valB = b.y1; break;
                case 'm6': valA = a.m6; valB = b.m6; break;
                case 'm3': valA = a.m3; valB = b.m3; break;
                case 'm1': valA = a.m1; valB = b.m1; break;
                default: return 0;
            }}
            if (typeof valA === 'string') {{ return lossSortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA); }}
            return lossSortAsc ? valA - valB : valB - valA;
        }});

        const tbody = document.getElementById('lossFundTableBody');
        tbody.innerHTML = '';
        lossDisplayedCount = 0;
        loadMoreLossFunds();
    }}
    </script>
</body>
</html>"""


# ============================================================
# HTTP请求处理
# ============================================================

class FundRadarHandler(http.server.BaseHTTPRequestHandler):
    """处理HTTP请求，解析URL参数并返回渲染后的页面。"""

    def do_GET(self):
        # 解析URL
        parsed = urllib.parse.urlparse(self.path)

        # 处理静态文件（icon）
        if parsed.path.startswith('/icon/'):
            file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), parsed.path.lstrip('/'))
            if os.path.exists(file_path):
                self.send_response(200)
                if file_path.endswith('.jpg') or file_path.endswith('.jpeg'):
                    self.send_header("Content-Type", "image/jpeg")
                elif file_path.endswith('.png'):
                    self.send_header("Content-Type", "image/png")
                elif file_path.endswith('.ico'):
                    self.send_header("Content-Type", "image/x-icon")
                self.end_headers()
                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_response(404)
                self.end_headers()
                return

        # 解析参数
        params = urllib.parse.parse_qs(parsed.query)

        # 获取参数（默认值100%, 60%, 40%, 25%）
        y1 = float(params.get("y1", ["100"])[0])
        m6 = float(params.get("m6", ["60"])[0])
        m3 = float(params.get("m3", ["40"])[0])
        m1 = float(params.get("m1", ["25"])[0])

        print(f"\n{'='*60}")
        print(f"[请求] {self.path}")
        print(f"[参数] y1={y1}%, m6={m6}%, m3={m3}%, m1={m1}%")
        print(f"{'='*60}")

        try:
            # 抓取数据
            start_time = time.time()
            df = fetch_fund_ranking()

            # 筛选高收益基金，按近1月收益降序排列取前30
            filtered = screen_funds(df, y1, m6, m3, m1)
            if filtered.empty:
                fund_data = []
                fund_count = 0
            else:
                filtered = filtered.sort_values(by="m1", ascending=False).head(30)
                fund_data = build_fund_list(filtered, max_funds=30)
                fund_count = len(fund_data)

            # 筛选近一月亏损超15%的基金
            df_loss = df.copy()
            col_map = {"近1年": "y1", "近6月": "m6", "近3月": "m3", "近1月": "m1"}
            rename_map = {}
            for cn_name, en_name in col_map.items():
                for col in df_loss.columns:
                    if cn_name in col:
                        rename_map[col] = en_name
                        break
            df_loss = df_loss.rename(columns=rename_map)
            df_loss["m1"] = pd.to_numeric(df_loss["m1"], errors="coerce")
            loss_mask = df_loss["m1"] <= -15
            loss_filtered = df_loss[loss_mask].copy()

            if loss_filtered.empty:
                loss_fund_data = []
                loss_fund_count = 0
            else:
                # 按近1月亏损收益率升序排列（亏损越多越靠前）
                loss_filtered = loss_filtered.sort_values(by="m1", ascending=True).head(20)
                loss_fund_data = build_fund_list(loss_filtered, max_funds=20)
                loss_fund_count = len(loss_filtered)

            elapsed = time.time() - start_time
            print(f"[完成] 数据处理耗时 {elapsed:.2f} 秒")
            print(f"  高收益基金: {fund_count} 只")
            print(f"  亏损基金: {loss_fund_count} 只")

            # 渲染HTML
            html = HTML_TEMPLATE.format(
                update_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                y1=int(y1),
                m6=int(m6),
                m3=int(m3),
                m1=int(m1),
                fund_count=fund_count,
                fund_data=json.dumps(fund_data, ensure_ascii=False),
                loss_fund_count=loss_fund_count,
                loss_fund_data=json.dumps(loss_fund_data, ensure_ascii=False)
            )

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))

        except Exception as e:
            print(f"[错误] {e}")
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            error_html = f"<h1>服务器错误</h1><pre>{e}</pre>"
            self.wfile.write(error_html.encode('utf-8'))

    def log_message(self, format, *args):
        pass  # 静默日志


# ============================================================
# 启动服务器
# ============================================================

if __name__ == "__main__":
    print(f"{'='*60}")
    print(f"  Fund-Radar 全链路生产级服务")
    print(f"  访问地址: http://localhost:{PORT}")
    print(f"  带参示例: http://localhost:{PORT}?y1=100&m6=60&m3=40&m1=25")
    print(f"  按 Ctrl+C 停止服务器")
    print(f"{'='*60}")

    with socketserver.TCPServer(("", PORT), FundRadarHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n服务器已停止")
