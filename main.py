import requests
import pandas as pd
import time
import random
import os
from datetime import datetime

# ==================== 配置区 ====================
TARGET_BOARD = "半导体"        # 目标板块名称
NUM_DAYS = 5                   # 统计最近几个交易日（修改此值即可）
TOP_N = 10                     # 显示前几名
EXPORT_DIR = "output"          # 导出目录

# 预设半导体龙头股（AKShare获取成份股失败时的备选，可自行增删）
PRESET_STOCKS = [
    "688981", "002371", "603986", "688012", "603501",
    "688008", "603160", "002049", "300661", "688036",
    "603290", "300782", "688200", "603505", "002185",
    "300223", "688396", "603005", "300327", "002156",
    "688521", "300613", "603153", "300458", "002180",
    "688536", "300308", "603259", "300706", "002916",
    "688041", "688120", "688187", "300672", "688126",
    "688599", "300236", "002156", "300706", "688052",
]
# ================================================


def test_connection():
    """测试腾讯行情服务器连通性"""
    print("🔍 正在检测数据源连通性...")
    try:
        r = requests.get("http://qt.gtimg.cn/q=sh000001", timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and "~" in r.text:
            print("  ✅ 腾讯行情服务器连接正常！\n")
            return True
    except Exception as e:
        print(f"  ❌ 连接失败: {e}")
    return False


def get_board_constituents():
    """获取板块成份股列表，优先AKShare，失败则用预设列表"""
    stock_codes = None
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

    print(f"\n  💡 AKShare不可用，使用预设的{TARGET_BOARD}龙头股列表")
    print(f"  📌 共 {len(PRESET_STOCKS)} 只（可在代码顶部 PRESET_STOCKS 中自行增删修改）")
    return PRESET_STOCKS


def to_tencent_code(code):
    """纯数字代码转腾讯格式（sh/sz前缀）"""
    code = str(code).strip()
    return f"sh{code}" if code.startswith(('6', '9')) else f"sz{code}"


def get_kline(stock_code, days=10):
    """
    获取腾讯日K线数据（最近N个交易日）
    返回字段：日期、开盘、收盘、最高、最低、成交量(手)
    """
    tc = to_tencent_code(stock_code)
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{tc},day,,,{days},qfq"}

    try:
        resp = requests.get(url, params=params, timeout=15,
                            headers={"User-Agent": "Mozilla/5.0"})
        data = resp.json()

        code_key = tc
        klines = (data.get("data", {}).get(code_key, {}).get("qfqday") or
                  data.get("data", {}).get(code_key, {}).get("day"))

        if not klines:
            return []

        result = []
        for k in klines:
            if len(k) >= 6:
                result.append({
                    "日期": k[0],
                    "开盘": float(k[1]),
                    "收盘": float(k[2]),
                    "最高": float(k[3]),
                    "最低": float(k[4]),
                    "成交量(手)": int(float(k[5])),
                })
        return result
    except Exception:
        return []


def get_realtime_quote(stock_code):
    """获取腾讯实时行情（含外盘/内盘数据）"""
    tc = to_tencent_code(stock_code)
    try:
        resp = requests.get(f"http://qt.gtimg.cn/q={tc}", timeout=10,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.encoding = "gbk"
        line = resp.text.strip()
        if '="' not in line or '~' not in line:
            return None
        data_str = line.split('="', 1)[1].rstrip('";')
        f = data_str.split("~")
        if len(f) < 50 or not f[1]:
            return None

        ext_vol = int(f[7]) if f[7] else 0  # 外盘
        int_vol = int(f[8]) if f[8] else 0  # 内盘
        ratio = ext_vol / int_vol if int_vol > 0 else 1.0

        return {
            "代码": f[2], "名称": f[1],
            "最新价": float(f[3]) if f[3] else 0,
            "涨跌幅(%)": float(f[32]) if f[32] else 0,
            "外盘(手)": ext_vol, "内盘(手)": int_vol,
            "外内比": round(ratio, 2),
            "成交额(万)": float(f[37]) if f[37] else 0,
            "换手率(%)": float(f[38]) if f[38] else 0,
            "总市值(亿)": float(f[45]) if f[45] else 0,
        }
    except Exception:
        return None


def calc_mfi(close, high, low, volume):
    """
    Chaikin Money Flow 指标
    MF = [(收盘-最低)/(最高-最低) × 2 - 1] × 成交量
    值域：[-成交量, +成交量]
    正值 = 资金净流入（收盘价偏向当日最高价，买方主导）
    负值 = 资金净流出（收盘价偏向当日最低价，卖方主导）
    """
    if volume == 0:
        return 0
    if high == low:
        # 一字涨跌停：用涨跌方向判断
        return volume if close > 0 else -volume
    multiplier = (close - low) / (high - low) * 2 - 1
    return multiplier * volume


def main():
    print("=" * 60)
    print(f"  资金流向分析工具 — 多日趋势版（腾讯行情）")
    print(f"  目标板块：{TARGET_BOARD}")
    print(f"  统计天数：最近 {NUM_DAYS} 个交易日")
    print(f"  运行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

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

    # 获取实时行情
    print("💰 正在获取实时行情...")
    realtime_data = []
    batch_codes = []
    for code in stock_codes:
        batch_codes.append(to_tencent_code(code))
        if len(batch_codes) >= 50:
            try:
                codes_str = ",".join(batch_codes)
                resp = requests.get(f"http://qt.gtimg.cn/q={codes_str}",
                                    timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                resp.encoding = "gbk"
                for line in resp.text.strip().split("\n"):
                    line = line.strip().rstrip(";")
                    if '="' not in line or '~' not in line:
                        continue
                    try:
                        data_str = line.split('="', 1)[1].rstrip('"')
                        f = data_str.split("~")
                        if len(f) >= 50 and f[1] and f[3]:
                            ext_vol = int(f[7]) if f[7] else 0
                            int_vol = int(f[8]) if f[8] else 0
                            ratio = ext_vol / int_vol if int_vol > 0 else 1.0
                            realtime_data.append({
                                "代码": f[2], "名称": f[1],
                                "最新价": float(f[3]) if f[3] else 0,
                                "涨跌幅(%)": float(f[32]) if f[32] else 0,
                                "外盘(手)": ext_vol, "内盘(手)": int_vol,
                                "外内比": round(ratio, 2),
                                "成交额(万)": float(f[37]) if f[37] else 0,
                                "换手率(%)": float(f[38]) if f[38] else 0,
                                "总市值(亿)": float(f[45]) if f[45] else 0,
                            })
                    except:
                        continue
            except:
                pass
            batch_codes = []
            time.sleep(random.uniform(0.5, 1))

    # 处理最后一批
    if batch_codes:
        try:
            codes_str = ",".join(batch_codes)
            resp = requests.get(f"http://qt.gtimg.cn/q={codes_str}",
                                timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            resp.encoding = "gbk"
            for line in resp.text.strip().split("\n"):
                line = line.strip().rstrip(";")
                if '="' not in line or '~' not in line:
                    continue
                try:
                    data_str = line.split('="', 1)[1].rstrip('"')
                    f = data_str.split("~")
                    if len(f) >= 50 and f[1] and f[3]:
                        ext_vol = int(f[7]) if f[7] else 0
                        int_vol = int(f[8]) if f[8] else 0
                        ratio = ext_vol / int_vol if int_vol > 0 else 1.0
                        realtime_data.append({
                            "代码": f[2], "名称": f[1],
                            "最新价": float(f[3]) if f[3] else 0,
                            "涨跌幅(%)": float(f[32]) if f[32] else 0,
                            "外盘(手)": ext_vol, "内盘(手)": int_vol,
                            "外内比": round(ratio, 2),
                            "成交额(万)": float(f[37]) if f[37] else 0,
                            "换手率(%)": float(f[38]) if f[38] else 0,
                            "总市值(亿)": float(f[45]) if f[45] else 0,
                        })
                except:
                    continue
        except:
            pass

    df_realtime = pd.DataFrame(realtime_data) if realtime_data else None
    if df_realtime is not None and not df_realtime.empty:
        print(f"  ✅ 成功获取 {len(df_realtime)} 只股票实时行情\n")
    else:
        print("  ⚠️ 实时行情获取失败，继续获取历史数据...\n")

    # 逐只获取日K线（多请求 NUM_DAYS+5 条以确保覆盖足够的交易日）
    print(f"📊 正在获取每只股票最近 {NUM_DAYS} 个交易日的日K线数据...")
    print("  （用于计算每日资金流向指标，请稍候...）\n")

    stock_klines = {}
    total = len(stock_codes)
    for i, code in enumerate(stock_codes):
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  进度：{i+1}/{total}")
        klines = get_kline(code, days=NUM_DAYS + 5)
        if klines:
            stock_klines[code] = klines
        time.sleep(random.uniform(0.3, 0.8))

    print(f"\n  ✅ 成功获取 {len(stock_klines)} 只股票的K线数据\n")

    if not stock_klines:
        print("❌ 未能获取任何K线数据，程序结束。")
        return

    # 按日期汇总全板块资金流向
    daily_summary = {}
    stock_total_mf = {}

    for code, klines in stock_klines.items():
        # 只取最近 NUM_DAYS 个交易日
        recent = klines[-NUM_DAYS:] if len(klines) >= NUM_DAYS else klines
        total_mf = 0
        stock_name = ""

        for day in recent:
            date = day["日期"]
            mf = calc_mfi(day["收盘"], day["最高"], day["最低"], day["成交量(手)"])
            total_mf += mf

            if date not in daily_summary:
                daily_summary[date] = {"资金流向": 0, "参与股票数": 0, "总成交量(手)": 0}
            daily_summary[date]["资金流向"] += mf
            daily_summary[date]["参与股票数"] += 1
            daily_summary[date]["总成交量(手)"] += day["成交量(手)"]

        stock_total_mf[code] = total_mf

    # ===== 显示每日资金流向汇总 =====
    sorted_dates = sorted(daily_summary.keys())

    print("=" * 60)
    print(f"  📊 【{TARGET_BOARD}】板块最近 {NUM_DAYS} 个交易日资金流向汇总")
    print("=" * 60)
    print(f"  {'日期':<12} {'资金流向(万手)':>14} {'参与股票':>8} {'总成交量(万手)':>14} {'方向':>6}")
    print("-" * 60)

    prev_mf = None
    for date in sorted_dates:
        info = daily_summary[date]
        mf = info["资金流向"]
        mf_display = mf / 10000  # 转为万手
        vol_display = info["总成交量(手)"] / 10000
        direction = "📈流入" if mf > 0 else "📉流出"

        # 日环比
        change_str = ""
        if prev_mf is not None and prev_mf != 0:
            change_pct = (mf - prev_mf) / abs(prev_mf) * 100
            change_str = f"  (环比{'+' if change_pct > 0 else ''}{change_pct:.1f}%)"
        prev_mf = mf

        print(f"  {date:<12} {mf_display:>14,.1f} {info['参与股票数']:>8} {vol_display:>14,.1f} {direction}{change_str}")

    # 统计汇总
    total_inflow = sum(v for v in [daily_summary[d]["资金流向"] for d in sorted_dates] if v > 0)
    total_outflow = sum(v for v in [daily_summary[d]["资金流向"] for d in sorted_dates] if v < 0)
    net_flow = total_inflow + total_outflow

    print("-" * 60)
    print(f"  📌 {NUM_DAYS}日累计：净流入 {total_inflow/10000:,.1f} 万手 / "
          f"净流出 {abs(total_outflow)/10000:,.1f} 万手 / "
          f"净{'流入' if net_flow > 0 else '流出'} {abs(net_flow)/10000:,.1f} 万手")
    print("=" * 60)

    # ===== 个股资金流向排名（基于N日累计） =====
    print(f"\n{'='*60}")
    print(f"  🏆 【{TARGET_BOARD}】个股 {NUM_DAYS} 日累计资金流向 TOP {TOP_N}")
    print(f"{'='*60}")

    # 合并实时行情数据
    stock_info = {}
    if df_realtime is not None:
        for _, row in df_realtime.iterrows():
            stock_info[row["代码"]] = row

    sorted_stocks = sorted(stock_total_mf.items(), key=lambda x: x[1], reverse=True)

    # 净流入 TOP N
    print(f"\n  --- 净流入 TOP {TOP_N} ---")
    print(f"  {'代码':<8} {'名称':<10} {'最新价':>8} {'涨跌%':>7} {'外/内比':>8} {'N日资金流(万手)':>14}")
    print("  " + "-" * 58)
    for code, mf in sorted_stocks[:TOP_N]:
        info = stock_info.get(code, {})
        name = info.get("名称", "N/A")
        price = info.get("最新价", 0)
        change = info.get("涨跌幅(%)", 0)
        ratio = info.get("外内比", 0)
        print(f"  {code:<8} {str(name):<10} {price:>8.2f} {change:>7.2f} {ratio:>8.2f} {mf/10000:>14,.1f}")

    # 净流出 TOP N
    print(f"\n  --- 净流出 TOP {TOP_N} ---")
    print(f"  {'代码':<8} {'名称':<10} {'最新价':>8} {'涨跌%':>7} {'外/内比':>8} {'N日资金流(万手)':>14}")
    print("  " + "-" * 58)
    for code, mf in sorted_stocks[-TOP_N:][::-1]:
        info = stock_info.get(code, {})
        name = info.get("名称", "N/A")
        price = info.get("最新价", 0)
        change = info.get("涨跌幅(%)", 0)
        ratio = info.get("外内比", 0)
        print(f"  {code:<8} {str(name):<10} {price:>8.2f} {change:>7.2f} {ratio:>8.2f} {mf/10000:>14,.1f}")

    # ===== 导出Excel =====
    os.makedirs(EXPORT_DIR, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{EXPORT_DIR}/{TARGET_BOARD}_{NUM_DAYS}日资金流向_{today_str}.xlsx"

    # Sheet1: 每日资金流向汇总
    daily_records = []
    for date in sorted_dates:
        info = daily_summary[date]
        daily_records.append({
            "日期": date,
            "资金流向(万手)": round(info["资金流向"] / 10000, 2),
            "参与股票数": info["参与股票数"],
            "总成交量(万手)": round(info["总成交量(手)"] / 10000, 2),
            "方向": "净流入" if info["资金流向"] > 0 else "净流出",
        })
    df_daily = pd.DataFrame(daily_records)

    # Sheet2: 个股N日累计资金流向
    stock_records = []
    for code, mf in sorted_stocks:
        info = stock_info.get(code, {})
        stock_records.append({
            "代码": code,
            "名称": info.get("名称", "N/A"),
            "最新价": info.get("最新价", 0),
            "涨跌幅(%)": info.get("涨跌幅(%)", 0),
            "外内比": info.get("外内比", 0),
            "成交额(万)": info.get("成交额(万)", 0),
            f"{NUM_DAYS}日资金流向(万手)": round(mf / 10000, 2),
            "总市值(亿)": info.get("总市值(亿)", 0),
        })
    df_stocks = pd.DataFrame(stock_records)

    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        df_daily.to_excel(writer, sheet_name=f"每日资金流向({NUM_DAYS}日)", index=False)
        df_stocks.to_excel(writer, sheet_name="个股资金流向排名", index=False)
        if df_realtime is not None and not df_realtime.empty:
            df_realtime.to_excel(writer, sheet_name="实时行情", index=False)

    print(f"\n{'='*60}")
    print(f"  🎉 数据已导出至：{os.path.abspath(filename)}")
    print(f"  📌 Excel包含：每日资金流向 + 个股资金流向排名 + 实时行情")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
