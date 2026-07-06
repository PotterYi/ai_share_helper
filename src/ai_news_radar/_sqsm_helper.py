"""SQS helper - standalone subprocess for computing 十全十美.

Uses Tencent ifzq API (JSON, no JavaScript) directly via urllib.
NO akshare dependency = NO py_mini_racer crash.
"""
import sys, json, numpy as np, urllib.request, warnings
warnings.filterwarnings("ignore")


def _fetch_data(code: str, days: int = 500) -> list[dict]:
    """Fetch daily K-line from Tencent API (pure JSON, no JS needed)."""
    code_clean = code.replace("sh", "").replace("sz", "").replace("bj", "")
    first_digit = code_clean[0] if code_clean else "0"
    if first_digit in ("6", "5", "1"):
        prefix = "sh"
    elif first_digit in ("0", "2", "3"):
        prefix = "sz"
    else:
        prefix = "bj"
    
    url = f"http://ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code_clean},day,,,{days},qfq"
    
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    
    # Navigate JSON structure
    try:
        stock_data = data.get("data", {}).get(f"{prefix}{code_clean}", {})
        days_data = stock_data.get("qfqday", stock_data.get("day", []))
    except Exception:
        return []
    
    if not days_data:
        return []
    
    records = []
    for d in days_data:
        if len(d) < 6:
            continue
        try:
            records.append({
                "date": str(d[0]),
                "open": float(d[1]),
                "close": float(d[2]),
                "high": float(d[3]),
                "low": float(d[4]),
                "volume": float(d[5]),
            })
        except (ValueError, IndexError):
            continue
    
    if len(records) < 80:
        return []
    return records


code = sys.argv[1] if len(sys.argv) > 1 else ""
recs = _fetch_data(code, days=500)
if len(recs) < 80:
    print("NODATA")
    sys.exit(0)

c = np.array([r["close"] for r in recs], dtype=float)
h = np.array([r["high"] for r in recs], dtype=float)
l = np.array([r["low"] for r in recs], dtype=float)
v = np.array([r["volume"] for r in recs], dtype=float)

sys.path.insert(0, "G:/develop/project/ai_news_radar")
from ai_news_radar.sqsm_indicator import ShiQuanShiMei

sqsm = ShiQuanShiMei()
res = sqsm.calculate(c, h, l, v)
if "error" in res:
    print("ERROR")
    sys.exit(0)

today = res.get("total", 0)
res_y = sqsm.calculate(c[:-1], h[:-1], l[:-1], v[:-1])
yesterday = res_y.get("total", 0) if "error" not in res_y else 0
print(f"TODAY:{today} YESTERDAY:{yesterday}")
