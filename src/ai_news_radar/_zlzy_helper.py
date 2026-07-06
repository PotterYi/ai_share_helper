"""ZLZY helper - standalone subprocess for computing 主力作妖.
Uses Tencent ifzq API (JSON, no JavaScript) directly via urllib.
NO akshare dependency = NO py_mini_racer crash.
"""
import sys, json, numpy as np, urllib.request, warnings, os
warnings.filterwarnings("ignore")

def _fetch_data(code: str, days: int = 500) -> list[dict]:
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
                "date": str(d[0]), "open": float(d[1]), "close": float(d[2]),
                "high": float(d[3]), "low": float(d[4]), "volume": float(d[5]),
            })
        except (ValueError, IndexError):
            continue
    if len(records) < 200:
        return []
    return records

code = sys.argv[1] if len(sys.argv) > 1 else ""
recs = _fetch_data(code, days=500)
if len(recs) < 200:
    print("NODATA")
    sys.exit(0)

c = np.array([r["close"] for r in recs], dtype=float)
h = np.array([r["high"] for r in recs], dtype=float)
l = np.array([r["low"] for r in recs], dtype=float)
o = np.array([r["open"] for r in recs], dtype=float)
v = np.array([r["volume"] for r in recs], dtype=float)

# Get capital shares from qt data
code_clean = code.replace("sh", "").replace("sz", "").replace("bj", "")
first_digit = code_clean[0] if code_clean else "0"
if first_digit in ("6", "5", "1"):
    prefix = "sh"
elif first_digit in ("0", "2", "3"):
    prefix = "sz"
else:
    prefix = "bj"

url = f"http://ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code_clean},day,,,2,qfq"
capital_shares = 0
try:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        d2 = json.loads(resp.read().decode("utf-8"))
    sd = d2.get("data", {}).get(f"{prefix}{code_clean}", {})
    qt = sd.get("qt", {})
    arr = qt.get(f"{prefix}{code_clean}", [])
    if isinstance(arr, list) and len(arr) >= 74:
        capital_shares = float(arr[72]) if arr[72] else 0
except Exception:
    pass

if capital_shares <= 0:
    print("NODATA")
    sys.exit(0)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from ai_news_radar.zlzy_indicator import ZhuLiZuoYao

zlzy = ZhuLiZuoYao()
res = zlzy.calculate(c, h, l, o, v, capital_shares)
if "error" in res:
    print("ERROR")
    sys.exit(0)

# Also compute for yesterday (exclude last bar)
res_y = zlzy.calculate(c[:-1], h[:-1], l[:-1], o[:-1], v[:-1], capital_shares)
yesterday = res_y.get("signal", False) if "error" not in res_y else False

print(f"SIGNAL:{int(res['signal'])} PREV:{int(res['prev_signal'])} YESTERDAY:{int(yesterday)}")
