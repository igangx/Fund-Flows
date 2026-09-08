import requests
import pandas as pd
import time
import random
import os
from datetime import datetime

# ==================== 配置区 ====================
TARGET_BOARD = "半导体"
TOP_N = 10
EXPORT_DIR = "output"

# 预设半导体龙头股（AKShare获取成份股失败时的备选方案，可自行增删修改）
PRESET_STOCKS = [
    "688981", "002371", "603986", "688012", "603501",
    "688008", "603160", "002049", "300661", "688036",
    "603290", "300782", "688200", "603505", "002185",
    "300223", "688396", "603005", "300327", "002156",
    "688521", "300613", "603153", "300458", "002180",
    "688536", "300308", "603259", "300706", "002916",
    "688041", "603259", "688120", "688187", "300613",
    "300672", "688126", "688599", "300223", "002185",
]
# ================================================


def test_connection():
    """测试网络连接"""
    print("🔍 正在检测数据源连通性...")
    try:
        r = requests.get(
            "http://qt.gtimg.cn/q=sh000001",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200 and "~" in r.text:
            print("  ✅ 腾讯行情服务器连接正常！\n")
            return True
    except Exception as e:
        print(f"  ❌ 腾讯服务器连接失败: {e}")
    return False


def get_board_constituents():
    """获取板块成份股列表，优先AKShare，失败则用预设列表"""
    stock_codes = None

    # 方式一：尝试AKShare（底层走东方财富，可能被封锁）
    try:
        import akshare as ak
        print("  📡 尝试通过AKShare获取成份股列表...")
        for attempt in range(3):
            try:
                time.sleep(random.uniform(3, 6))
                df = ak.stock_board_industry_cons_em(symbol=TARGET_BOARD)
                stock_codes = df['代码'].tolist()
                print(f"  ✅ AKShare成功！获取到 {len(stock_codes)} 只成份股")
                return stock_codes
            except Exception as e:
                print(f"  ⚠️ 第 {attempt+1} 次尝试失败: {type(e).__name__}")
    except ImportError:
        print("  ⚠️ 未安装akshare")

    # 方式二：使用预设列表
    print(f"\n  💡 AKShare不可用，使用预设的{TARGET_BOARD}龙头股列表")
    print(f"  📌 共 {len(PRESET_STOCKS)} 只（可在代码顶部 PRESET_STOCKS 中自行增删修改）")
    return PRESET_STOCKS


def tencent_batch_quote(stock_codes):
    """
    腾讯行情API批量获取实时数据
    关键字段：外盘（主动买入量）、内盘（主动卖出量）→ 用于计算资金流向
    """
    tencent_codes = []
    for code in stock_codes:
        code = str(code).strip()
        prefix = "sh" if code.startswith(('6', '9')) else "sz"
        tencent_codes.append(f"{prefix}{code}")

    all_records = []
    batch_size = 50

    for i in range(0, len(tencent_codes), batch_size):
        batch = tencent_codes[i:i + batch_size]
        codes_str = ",".join(batch)
        url = f"http://qt.gtimg.cn/q={codes_str}"

        try:
            time.sleep(random.uniform(0.5, 1.5))
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            resp.encoding = "gbk"

            for line in resp.text.strip().split("\n"):
                line = line.strip().rstrip(";")
                if '="' not in line or '~' not in line:
                    continue
                try:
                    data_str = line.split('="', 1)[1].rstrip('"')
                    f = data_str.split("~")
                    if len(f) < 50 or not f[1]:
                        continue

                    record = {
                        "代码": f[2],
                        "名称": f[1],
                        "最新价": float(f[3]) if f[3] else 0,
                        "昨收": float(f[4]) if f[4] else 0,
                        "今开": float(f[5]) if f[5] else 0,
                        "成交量(手)": int(f[6]) if f[6] else 0,
                        "外盘(手)": int(f[7]) if f[7] else 0,       # 主动买入量
                        "内盘(手)": int(f[8]) if f[8] else 0,       # 主动卖出量
                        "最高": float(f[33]) if f[33] else 0,
                        "最低": float(f[34]) if f[34] else 0,
                        "涨跌幅(%)": float(f[32]) if f[32] else 0,
                        "成交额(万)": float(f[37]) if f[37] else 0,
                        "换手率(%)": float(f[38]) if f[38] else 0,
                        "市盈率": float(f[39]) if f[39] else 0,
                        "振幅(%)": float(f[43]) if f[43] else 0,
                        "流通市值(亿)": float(f[44]) if f[44] else 0,
                        "总市值(亿)": float(f[45]) if f[45] else 0,
                        "量比": float(f[49]) if f[49] else 0,
                    }
                    all_records.append(record)
                except (ValueError, IndexError):
                    continue
        except Exception as e:
            print(f"  ⚠️ 批次 {i // batch_size + 1} 请求失败: {e}")

    return pd.DataFrame(all_records) if all_records else None


def calc_fund_flow(df):
    """
    基于外盘/内盘计算资金流向指标
    外盘 = 主动买入（资金流入），内盘 = 主动卖出（资金流出）
    """
    df = df.copy()

    # 外盘与内盘的差值（手）→ 正值=资金净流入，负值=资金净流出
    df["净买入(手)"] = df["外盘(手)"] - df["内盘(手)"]

    # 净买入占比（占成交量的百分比）
    df["净买入占比(%)"] = df.apply(
        lambda r: round(r["净买入(手)"] / r["成交量(手)"] * 100, 2)
        if r["成交量(手)"] > 0 else 0, axis=1
    )

    # 资金流向强度评级
    def rating(row):
        ratio = row["外盘(手)"] / row["内盘(手)"] if row["内盘(手)"] > 0 else 999
        if ratio >= 2.0:
            return "强势流入"
        elif ratio >= 1.3:
            return "净流入"
        elif ratio >= 0.8:
            return "均衡"
        elif ratio >= 0.5:
            return "净流出"
        else:
            return "强势流出"

    df["资金流向"] = df.apply(rating, axis=1)

    return df


def main():
    print("=" * 58)
    print(f"  资金流向分析工具 — 腾讯行情版")
    print(f"  数据源：腾讯财经（qt.gtimg.cn）")
    print(f"  目标板块：{TARGET_BOARD}")
    print(f"  运行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 58)

    # 网络检测
    if not test_connection():
        print("❌ 无法连接腾讯行情服务器，请检查网络后重试。")
        return

    # 获取成份股列表
    print(f"📋 正在获取【{TARGET_BOARD}】板块成份股...")
    stock_codes = get_board_constituents()
    if not stock_codes:
        print("❌ 获取成份股列表失败。")
        return
    print(f"  共 {len(stock_codes)} 只股票待查询\n")

    # 获取腾讯实时行情
    print("💰 正在通过腾讯API获取实时行情...")
    df = tencent_batch_quote(stock_codes)
    if df is None or df.empty:
        print("❌ 获取行情数据失败。")
        return

    # 过滤停牌股（最新价为0）
    df = df[df["最新价"] > 0].copy()
    print(f"  ✅ 成功获取 {len(df)} 只有效股票数据\n")

    # 计算资金流向
    print("📊 正在计算资金流向指标...")
    df = calc_fund_flow(df)

    # ===== 显示结果 =====
    display_cols = ["代码", "名称", "最新价", "涨跌幅(%)", "成交额(万)",
                    "外盘(手)", "内盘(手)", "净买入(手)", "净买入占比(%)", "资金流向"]

    # 资金净流入 TOP N
    df_by_flow = df.sort_values(by="净买入(手)", ascending=False)
    print(f"\n{'='*58}")
    print(f"  🏆 【{TARGET_BOARD}】资金净流入 TOP {TOP_N}")
    print(f"{'='*58}")
    print(df_by_flow[display_cols].head(TOP_N).to_string(index=False))

    # 资金净流出 TOP N
    print(f"\n{'='*58}")
    print(f"  💔 【{TARGET_BOARD}】资金净流出 TOP {TOP_N}")
    print(f"{'='*58}")
    print(df_by_flow[display_cols].tail(TOP_N).to_string(index=False))

    # 涨幅榜 TOP N
    df_by_change = df.sort_values(by="涨跌幅(%)", ascending=False)
    print(f"\n{'='*58}")
    print(f"  📈 【{TARGET_BOARD}】涨幅榜 TOP {TOP_N}")
    print(f"{'='*58}")
    print(df_by_change[["代码", "名称", "最新价", "涨跌幅(%)", "成交额(万)",
                         "换手率(%)", "资金流向"]].head(TOP_N).to_string(index=False))

    # 跌幅榜 TOP N
    print(f"\n{'='*58}")
    print(f"  📉 【{TARGET_BOARD}】跌幅榜 TOP {TOP_N}")
    print(f"{'='*58}")
    print(df_by_change[["代码", "名称", "最新价", "涨跌幅(%)", "成交额(万)",
                         "换手率(%)", "资金流向"]].tail(TOP_N).to_string(index=False))

    # ===== 导出Excel =====
    os.makedirs(EXPORT_DIR, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{EXPORT_DIR}/{TARGET_BOARD}_资金流向分析_{today_str}.xlsx"

    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        df_by_flow.to_excel(writer, sheet_name="资金流向排名", index=False)
        df_by_change.to_excel(writer, sheet_name="涨跌幅排名", index=False)

    print(f"\n{'='*58}")
    print(f"  🎉 数据已导出至：{os.path.abspath(filename)}")
    print(f"  📌 Excel包含2个Sheet：资金流向排名 + 涨跌幅排名")
    print(f"{'='*58}")

    # 统计摘要
    inflow_count = len(df[df["净买入(手)"] > 0])
    outflow_count = len(df[df["净买入(手)"] < 0])
    total_volume = df["成交额(万)"].sum()
    print(f"\n  📊 板块统计：{inflow_count}只净流入 / {outflow_count}只净流出 / 总成交额{total_volume:.0f}万元")


if __name__ == "__main__":
    main()