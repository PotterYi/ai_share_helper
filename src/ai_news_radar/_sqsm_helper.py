import sys, json, numpy as np
import sys as _sys
_sys.path.insert(0, chr(71)+chr(58)+chr(92)+chr(100)+chr(101)+chr(118)+chr(101)+chr(108)+chr(111)+chr(112)+chr(92)+chr(112)+chr(114)+chr(111)+chr(106)+chr(101)+chr(99)+chr(116)+chr(92)+chr(97)+chr(105)+chr(95)+chr(110)+chr(101)+chr(119)+chr(115)+chr(95)+chr(114)+chr(97)+chr(100)+chr(97)+chr(114))
from ai_news_radar.stock_analyzer import _fetch_stock_daily
from ai_news_radar.sqsm_indicator import ShiQuanShiMei

code = sys.argv[1] if len(sys.argv) > 1 else ""
recs = _fetch_stock_daily(code, days=500)
if len(recs) < 80:
    print("NODATA")
    sys.exit(0)
c = np.array([r["close"] for r in recs], dtype=float)
h = np.array([r["high"] for r in recs], dtype=float)
l = np.array([r["low"] for r in recs], dtype=float)
v = np.array([r["volume"] for r in recs], dtype=float)
sqsm = ShiQuanShiMei()
res = sqsm.calculate(c, h, l, v)
if "error" in res:
    print("ERROR")
    sys.exit(0)
today = res.get("total", 0)
res_y = sqsm.calculate(c[:-1], h[:-1], l[:-1], v[:-1])
yesterday = res_y.get("total", 0) if "error" not in res_y else 0
print(f"TODAY:{today} YESTERDAY:{yesterday}")