"""
台股即時行情 MCP Server
讓 OpenClaw（龍蝦）能透過 Telegram 查詢台股價格

資料來源：
  - 上市股票 -> TWSE 公開 API（免費、無需 API Key）
  - 上櫃股票 -> Yahoo Finance API（免費、無需 API Key）
"""

import json
import time
import urllib.request
from datetime import datetime, timedelta
from mcp.server.fastmcp import FastMCP

# ========================================
# 建立 MCP Server（名字叫「台股行情」）
# ========================================
mcp = FastMCP("tw-stock")

# ========================================
# 上櫃股票代碼清單
# （上櫃股 TWSE 查不到，要走 Yahoo Finance）
# 你可以隨時在這裡加更多代碼
# ========================================
OTC_STOCKS = {
    "3580",  # 友威科
    "6889",  # 翰博高新
    "4137",  # 牧德
    "4967",  # 十銓
    "6128",  # 聯鈦
    "8464",  # 力旺
    "3380",  # 精誠
    "6176",  # 錸寶
    "6669",  # 立積
    "3362",  # 瑞智
}


def is_otc(stock_id: str) -> bool:
    """判斷是不是上櫃股"""
    return stock_id in OTC_STOCKS


# ========================================
# TWSE 上市股查詢
# ========================================
def fetch_twse(stock_id: str, days: int) -> list:
    """
    從台灣證券交易所拉取上市股日線數據
    每次最多回傳 30 天
    """
    end_date = datetime.now()
    results = []

    batch_size = 30
    for offset in range(0, days, batch_size):
        fetch_end = end_date - timedelta(days=offset)
        date_str = fetch_end.strftime("%Y%m%d")

        url = (
            "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
            f"?response=json&date={date_str}&stockNo={stock_id}"
        )

        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0"
        })

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue

        if data.get("stat") != "OK":
            continue

        for row in data["data"]:
            # 民國年 -> 西元年（例如 113/01/02 -> 2024-01-02）
            parts = row[0].split("/")
            year = int(parts[0]) + 1911
            month = parts[1].zfill(2)
            day = parts[2].zfill(2)
            date_clean = f"{year}-{month}-{day}"

            try:
                results.append({
                    "date": date_clean,
                    "open": float(row[3].replace(",", "")),
                    "high": float(row[4].replace(",", "")),
                    "low": float(row[5].replace(",", "")),
                    "close": float(row[6].replace(",", "")),
                    "volume": int(row[8].replace(",", "")),
                })
            except (ValueError, IndexError):
                continue

        # TWSE 有頻率限制，每次請求間隔 0.5 秒
        time.sleep(0.5)

    return results[-days:]


# ========================================
# Yahoo Finance 上櫃股查詢
# ========================================
def fetch_yahoo(stock_id: str, days: int) -> list:
    """
    從 Yahoo Finance 拉取上櫃股日線數據
    上櫃股代碼要加 .TWO 後綴
    """
    ticker = f"{stock_id}.TWO"
    end_ts = int(datetime.now().timestamp())
    start_ts = int((datetime.now() - timedelta(days=days + 15)).timestamp())

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{ticker}?period1={start_ts}&period2={end_ts}&interval=1d"
    )

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0"
    })

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise Exception(f"Yahoo Finance 查詢失敗: {e}")

    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    quotes = result["indicators"]["quote"][0]

    results = []
    for i, ts in enumerate(timestamps):
        if quotes["close"][i] is None:
            continue
        results.append({
            "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
            "open": round(quotes["open"][i], 2) if quotes["open"][i] else 0,
            "high": round(quotes["high"][i], 2) if quotes["high"][i] else 0,
            "low": round(quotes["low"][i], 2) if quotes["low"][i] else 0,
            "close": round(quotes["close"][i], 2),
            "volume": int(quotes["volume"][i]) if quotes["volume"][i] else 0,
        })

    return results[-days:]


# ========================================
# MCP 工具 1：查詢單檔股價
# ========================================
@mcp.tool()
def stock_price(stock_id: str, days: int = 5) -> str:
    """
    查詢台股即時或歷史股價。

    參數：
    - stock_id: 股票代碼，例如 "2330"（台積電）、"1522"（堤維西）
    - days: 查詢最近幾天，預設 5 天，最多 30 天

    支援上市和上櫃股票。
    """
    days = min(max(days, 1), 30)

    try:
        if is_otc(stock_id):
            data = fetch_yahoo(stock_id, days)
            source = "Yahoo Finance"
        else:
            data = fetch_twse(stock_id, days)
            source = "TWSE 台灣證券交易所"
    except Exception as e:
        return f"查詢失敗：{e}"

    if not data:
        return (
            f"查無股票 {stock_id} 的數據。\n"
            f"請確認代碼是否正確，或該股票近期是否有交易。"
        )

    latest = data[-1]
    prev = data[-2] if len(data) > 1 else None

    # 計算漲跌
    change_text = ""
    if prev and prev["close"] > 0:
        diff = latest["close"] - prev["close"]
        pct = (diff / prev["close"]) * 100
        sign = "+" if diff >= 0 else ""
        arrow = "[漲]" if diff > 0 else ("[跌]" if diff < 0 else "[平]")
        change_text = f"\n{arrow} 漲跌：{sign}{diff:.2f}（{sign}{pct:.2f}%）"

    lines = [
        f"股票 {stock_id} 最近 {len(data)} 天行情",
        f"資料來源：{source}",
        f"",
        f"最新日期：{latest['date']}",
        f"收盤價：{latest['close']:.2f}{change_text}",
        f"",
        f"開盤：{latest['open']:.2f}",
        f"最高：{latest['high']:.2f}",
        f"最低：{latest['low']:.2f}",
        f"成交量：{latest['volume']:,} 張",
        f"",
        f"近期走勢：",
    ]

    for d in data[-5:]:
        lines.append(f"  {d['date']}  收 {d['close']:.2f}  量 {d['volume']:,}")

    return "\n".join(lines)


# ========================================
# MCP 工具 2：一次查多檔（自選股速報）
# ========================================
@mcp.tool()
def stock_watchlist(stock_ids: str) -> str:
    """
    一次查詢多檔股票的最新收盤價，適合看自選股。

    參數：
    - stock_ids: 用逗號分隔的股票代碼，例如 "2330,1522,3580"
    """
    codes = [c.strip() for c in stock_ids.split(",") if c.strip()]
    results = []

    for code in codes:
        try:
            if is_otc(code):
                data = fetch_yahoo(code, 2)
            else:
                data = fetch_twse(code, 2)

            if data:
                latest = data[-1]
                prev = data[-2] if len(data) > 1 else None
                change = ""
                if prev and prev["close"] > 0:
                    diff = latest["close"] - prev["close"]
                    pct = (diff / prev["close"]) * 100
                    sign = "+" if diff >= 0 else ""
                    arrow = "[漲]" if diff > 0 else ("[跌]" if diff < 0 else "[平]")
                    change = f" {arrow}{sign}{diff:.2f}({sign}{pct:.1f}%)"
                results.append(
                    f"  {code}  {latest['close']:.2f}{change}"
                )
            else:
                results.append(f"  {code}  查無資料")
        except Exception as e:
            results.append(f"  {code}  錯誤: {e}")

        time.sleep(0.5)

    header = f"自選股速報（共 {len(codes)} 檔）\n"
    return header + "\n".join(results)


# ========================================
# 啟動 MCP Server
# ========================================
if __name__ == "__main__":
    print("台股行情 MCP Server 啟動中...")
    print("上市股資料來源：TWSE 台灣證券交易所")
    print("上櫃股資料來源：Yahoo Finance")
    mcp.run(transport="sse")
