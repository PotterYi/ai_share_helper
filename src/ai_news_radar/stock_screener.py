"""
十全十美股票推荐 - 每日A股扫描筛选系统
每天 09:45 (早间·盘中实时估算) / 19:30 (晚间·收盘数据) 执行筛选并推送群通知
"""
import asyncio, logging, numpy as np, time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_LHB_CACHE = {"data": None, "ts": 0}


def _load_institutional_data() -> dict:
    """Load 30-day institutional net buy data from LHB.

    Returns:
        dict: {code: net_buy_amount_in_yuan}
    """
    global _LHB_CACHE
    now = time.time()
    if _LHB_CACHE["data"] is not None and now - _LHB_CACHE["ts"] < 300:
        return _LHB_CACHE["data"]
    try:
        import akshare as ak, warnings
        warnings.filterwarnings("ignore")
        df = ak.stock_lhb_jgstatistic_em()
        result = {}
        for _, row in df.iterrows():
            code = str(row["代码"])
            result[code] = float(row["机构净买额"])
        _LHB_CACHE = {"data": result, "ts": time.time()}
        logger.info("机构数据加载完成: %d 只", len(result))
        return result
    except Exception as e:
        logger.warning("机构数据加载失败: %s", e)
        return {}

_MIN_MARKET_CAP = 100
_MAX_MARKET_CAP = 1000
_MIN_TURNOVER_RATE = 5.0
_MIN_5D_CHANGE = 7.0
_TOP_N = 10


def _get_stock_5d(code: str) -> Optional[dict]:
    """Stage 1: get 5-day price data via stable Tencent API."""
    import akshare as ak
    try:
        today = datetime.now()
        start = (today - timedelta(days=10)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        df = ak.stock_zh_a_hist_tx(symbol=code, start_date=start, end_date=end, adjust="qfq")
        if df is None or len(df) < 2:
            return None
        close = float(df.iloc[-1]["close"])
        close5 = float(df.iloc[-5]["close"]) if len(df) >= 5 else float(df.iloc[0]["close"])
        ch5d = (close - close5) / close5 * 100
        has_zt = False
        peak_day = ""
        peak_change = 0.0
        for i in range(max(0, len(df) - 5), len(df)):
            r = df.iloc[i]
            dc = (float(r["close"]) - float(r["open"])) / float(r["open"]) * 100
            if dc >= 9.5:
                has_zt = True
            if dc > peak_change:
                peak_change = dc
                peak_day = str(r["date"])
        return {"code": code, "price": close, "ch5d": ch5d,
                "has_zt": has_zt, "peak_day": peak_day, "peak_change": peak_change}
    except:
        return None


def _get_stock_mcap_trn(code: str, price: float) -> Optional[dict]:
    """Stage 2: get market cap and turnover via Sina API (called on fewer stocks)."""
    import akshare as ak
    try:
        today = datetime.now()
        start = (today - timedelta(days=10)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        df = ak.stock_zh_a_daily(symbol=code, start_date=start, end_date=end)
        if df is None or len(df) < 2:
            return None
        last = df.iloc[-1]
        osh = float(last["outstanding_share"]) if "outstanding_share" in df.columns else 0
        trn = float(last["turnover"]) * 100 if "turnover" in df.columns else 0
        mcap = price * osh / 100000000 if osh > 0 else 0
        return {"code": code, "mcap": mcap, "trn": trn}
    except:
        return None


def _get_realtime_quote(code: str) -> Optional[dict]:
    """Get real-time quote for a stock (today's open/high/low/current)."""
    import akshare as ak, warnings
    warnings.filterwarnings("ignore")
    try:
        df = ak.stock_zh_a_spot()
        row = df[df['代码'] == code]
        if not row.empty:
            r = row.iloc[0]
            return {
                "price": float(r["最新价"]),
                "high": float(r["最高"]),
                "low": float(r["最低"]),
                "open": float(r["今开"]),
                "pre_close": float(r["昨收"]),
                "change_pct": float(r["涨跌幅"]),
            }
    except:
        pass
    return None


def _calc_sqsm_realtime(code: str) -> Optional[dict]:
    """Compute sqsm using real-time price to estimate TODAY's resonance.

    Appends today's real-time price to historical data so the comparison
    is TODAY(realtime) vs YESTERDAY(close), giving true 'today first resonance'.
    Falls back to subprocess method if real-time data unavailable.
    """
    from .stock_analyzer import _fetch_stock_daily
    from .sqsm_indicator import ShiQuanShiMei

    records = _fetch_stock_daily(code, days=500)
    if len(records) < 60:
        return None

    quote = _get_realtime_quote(code)
    if not quote:
        return None  # fall back to subprocess method

    # Build today's mock K-line from real-time data
    today = datetime.now().strftime("%Y-%m-%d")
    mock_today = {
        "date": today,
        "open": quote.get("open", records[-1]["close"]),
        "close": quote.get("price", records[-1]["close"]),
        "high": max(quote.get("high", records[-1]["high"]), records[-1]["high"]),
        "low": min(quote.get("low", records[-1]["low"]), records[-1]["low"]),
        "volume": records[-1]["volume"],  # use yesterday's volume as estimate
    }
    records_ext = records + [mock_today]

    c = np.array([r["close"] for r in records_ext], dtype=float)
    h = np.array([r["high"] for r in records_ext], dtype=float)
    l = np.array([r["low"] for r in records_ext], dtype=float)
    v = np.array([r["volume"] for r in records_ext], dtype=float)

    sqsm = ShiQuanShiMei()
    res_today = sqsm.calculate(c, h, l, v)
    if "error" in res_today:
        return None
    t = res_today.get("total", 0)

    # Yesterday (without mock data)
    c_y = np.array([r["close"] for r in records], dtype=float)
    h_y = np.array([r["high"] for r in records], dtype=float)
    l_y = np.array([r["low"] for r in records], dtype=float)
    v_y = np.array([r["volume"] for r in records], dtype=float)
    res_yest = sqsm.calculate(c_y, h_y, l_y, v_y)
    y = res_yest.get("total", 0) if "error" not in res_yest else 0

    return {"code": code, "score": f"{t}/10", "total": t, "yesterday_total": y}


def _calc_sqsm(code: str) -> Optional[dict]:
    """Compute sqsm in isolated subprocess (avoids py_mini_racer crash).

    For morning mode during trading hours (09:30+), use real-time estimation
    instead so 'today' truly means today, not the last closing day.
    """
    from datetime import time as dtime

    now = datetime.now()
    in_morning_trading = (9, 30) <= (now.hour, now.minute) <= (11, 30)

    # Try real-time estimation if in morning trading hours
    if in_morning_trading:
        rt = _calc_sqsm_realtime(code)
        if rt is not None:
            return rt

    # Fall back to subprocess (uses latest closed K-line)
    import subprocess, os, sys
    try:
        helper = os.path.join(os.path.dirname(__file__), "_sqsm_helper.py")
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        python_exe = os.path.join(os.path.dirname(sys.executable), "python.exe")
        env = os.environ.copy()
        env["PYTHONPATH"] = project_root
        env["AKSHARE_PROGRESS"] = "false"
        r = subprocess.run(
            [python_exe, helper, code],
            capture_output=True, text=True, timeout=60,
            cwd=project_root, env=env,
            encoding='utf-8', errors='replace',
        )
        if r.returncode != 0:
            return None
        for line in r.stdout.strip().split(chr(10)):
            if 'TODAY:' in line:
                parts = line.split()
                t = int(parts[0].split(':')[1])
                y = int(parts[1].split(':')[1]) if len(parts) > 1 else 0
                return {'code': code, 'score': f'{t}/10', 'total': t, 'yesterday_total': y}
        return None
    except:
        return None


def format_report(stocks: list, mode: str = "morning") -> str:
    if not stocks:
        return "今日无符合条件的十全十美推荐股票"
    now = datetime.now()
    mode_label = "早间筛选" if mode == "morning" else "盘后筛选"
    inst_label = "昨日机构净买" if mode == "morning" else "今日机构净买"
    lines = [f"**📊 十全十美股票推荐 ({mode_label})**", f"  {now.strftime('%Y-%m-%d %H:%M')}", ""]
    lines.append(f"  {'名称':<10s} {'代码':<8s} {'流通市值':<8s} {'换手率':<6s} {'5日涨幅(峰值)':<22s} {inst_label:<12s} {'十全十美'}")
    lines.append(f"  {'─'*10} {'─'*8} {'─'*8} {'─'*6} {'─'*22} {'─'*12} {'─'*10}")
    for i, s in enumerate(stocks, 1):
        sq = s.get("sqsm", "-/10")
        inst = s.get("jnst", "")
        ch5 = s.get("ch5d_display", f"{s['ch5d']:+.1f}%")
        lines.append(f"  {i:2d}. {s['name'][:8]:<10s} {'' if s['code'][:2].isalpha() else ''}{s['code'][-6:]:<8s} {s['mcap']:.0f}亿 {s['trn']:.1f}% {ch5:<22s} {inst:<12s} {sq}")
    lines.append(""); lines.append("  ---"); lines.append(f"  由 AI News Radar 自动筛选 · {now.strftime('%H:%M')}")
    return chr(10).join(lines)

async def run_daily_screener(mode: str = "morning"):
    import akshare as ak, warnings
    warnings.filterwarnings("ignore")
    loop = asyncio.get_event_loop()

    spot = await loop.run_in_executor(None, lambda: ak.stock_zh_a_spot())
    logger.info("全市场 %d 只", len(spot))

    cand = []
    for _, row in spot.iterrows():
        code = str(row.iloc[0])
        if code.startswith("bj"): continue
        name = str(row.iloc[1])
        if "ST" in name or "退" in name: continue
        amount = float(row.iloc[12])
        cand.append({"code": code, "name": name, "price": float(row.iloc[2]), "amount": amount})

    cand = [c for c in cand if c["amount"] >= 200000000]
    logger.info("成交额预筛后 %d 只", len(cand))
    cand.sort(key=lambda x: x["amount"], reverse=True)
    top_cand = cand[:200]

    sem = asyncio.Semaphore(8)
    async def _get_price(code):
        async with sem:
            return await loop.run_in_executor(None, _get_stock_5d, code)
    res = await asyncio.gather(*[_get_price(x["code"]) for x in top_cand])
    enr = [{**x, **r} for x, r in zip(top_cand, res) if r]
    logger.info("价格数据 %d 只", len(enr))

    flt_p = [s for s in enr if (s["ch5d"] >= _MIN_5D_CHANGE or s["has_zt"])]
    logger.info("价格筛选 %d 只", len(flt_p))
    if not flt_p: return []

    sem_m = asyncio.Semaphore(2)
    async def _get_mcap(s):
        async with sem_m:
            r = await loop.run_in_executor(None, _get_stock_mcap_trn, s["code"], s["price"])
            if r and _MIN_MARKET_CAP <= r["mcap"] <= _MAX_MARKET_CAP and r["trn"] >= _MIN_TURNOVER_RATE:
                return {**s, **r}
            return None
    mc = await asyncio.gather(*[_get_mcap(s) for s in flt_p])
    flt = [s for s in mc if s]
    logger.info("条件筛选 %d 只", len(flt))
    if not flt: return []

    sem_s = asyncio.Semaphore(2)
    async def _calc_one(code):
        async with sem_s:
            return await loop.run_in_executor(None, _calc_sqsm, code)
    sq = await asyncio.gather(*[_calc_one(s["code"]) for s in flt])
    fin = []
    for s, q in zip(flt, sq):
        if q and q.get("total", 0) >= 9 and q.get("yesterday_total", 10) < 9:
            fin.append({**s, "sqsm": q["score"]})
    logger.info("十全十美筛选 %d 只", len(fin))

    # 加载机构净买入数据
    if fin:
        inst_data = await loop.run_in_executor(None, _load_institutional_data)
        for s in fin:
            raw_code = s["code"].replace("sh", "").replace("sz", "")
            net = inst_data.get(raw_code, 0)
            if abs(net) >= 100000000:
                s["jnst"] = f"{'🟢' if net>0 else '🔴'}¥{abs(net)/100000000:.1f}亿"
            elif abs(net) >= 1000000:
                s["jnst"] = f"{'🟢' if net>0 else '🔴'}¥{abs(net)/10000:.0f}万"
            else:
                s["jnst"] = "-"

    for s in fin:
        pk = s.get("peak_day", "")[-5:]
        pc = s.get("peak_change", 0)
        if pk and pc:
            s["ch5d_display"] = f"{s['ch5d']:+.1f}%(峰值{pk}+{pc:.1f}%)" if pc > 0 else f"{s['ch5d']:+.1f}%(峰值{pk}{pc:.1f}%)"
        else:
            s["ch5d_display"] = f"{s['ch5d']:+.1f}%"

    fin.sort(key=lambda x: x["mcap"], reverse=True)
    return fin[:_TOP_N]
