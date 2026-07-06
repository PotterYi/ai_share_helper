"""Standalone screener - NO direct akshare import. Uses utllib + subprocess."""
import sys, json, subprocess, os, asyncio, warnings, urllib.request, math
warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
CHAT_ID = "oc_8792267760e09f7c142bb0157bcf22f0"
_MIN_MC = 100; _MAX_MC = 1000; _MIN_5D = 7.0; _TOP_N = 10
SQS_HELPER = os.path.join(BASE, "src", "ai_news_radar", "_sqsm_helper.py")
SPOT_HELPER = os.path.join(BASE, "src", "ai_news_radar", "_spot_helper.py")

def _load_spot():
    """Load spot data via subprocess.
    Force UTF-8 mode so Chinese stock names round-trip correctly."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    r = subprocess.run([PYTHON, SPOT_HELPER], capture_output=True, timeout=120,
                       cwd=BASE, env=env)
    if r.returncode != 0 or not r.stdout.strip():
        return {}
    try:
        return json.loads(r.stdout.decode("utf-8"))
    except:
        return {}
    try: return json.loads(r.stdout.strip())
    except: return {}

def _calc_sqsm(code):
    r = subprocess.run([PYTHON, SQS_HELPER, code], capture_output=True, text=True, timeout=60,
                       cwd=BASE, encoding="utf-8", errors="replace")
    if r.returncode != 0: return None
    for line in r.stdout.strip().split("\n"):
        if "TODAY:" in line:
            parts = line.split()
            t = int(parts[0].split(":")[1])
            y = int(parts[1].split(":")[1]) if len(parts) > 1 else 0
            return {"total": t, "yesterday": y, "score": f"{t}/10"}
    return None

def _fetch_kline(code, days=10):
    try:
        raw = code.replace("sh","").replace("sz","").replace("bj","")
        pref = "sh" if raw[0] in "651" else "sz" if raw[0] in "023" else "bj"
        url = f"http://ifzq.gtimg.cn/appstock/app/fqkline/get?param={pref}{raw},day,,,{days},qfq"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        sd = d.get("data", {}).get(pref + raw, {})
        return sd.get("qfqday", sd.get("day", []))
    except: return []

def _estimate_mcap(code, price, spot_info):
    """Estimate market cap from price * outstanding shares via Tencent API."""
    try:
        raw = code.replace("sh","").replace("sz","").replace("bj","")
        pref = "sh" if raw[0] in "651" else "sz" if raw[0] in "023" else "bj"
        # Try to get outstanding shares from kline
        url = f"http://ifzq.gtimg.cn/appstock/app/fqkline/get?param={pref}{raw},day,,,2,qfq"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        sd = d.get("data", {}).get(pref + raw, {})
        qt = sd.get("qt", {})
        if isinstance(qt, dict):
            arr = qt.get(pref + raw, [])
            if isinstance(arr, list) and len(arr) >= 74:
                # Tencent qt array indices:
                #   [38] = turnover rate (%)
                #   [72] = total outstanding shares
                #   [44] = total market cap (100M yuan, for verification)
                outstanding = float(arr[72]) if arr[72] else 0
                trn = float(arr[38]) if arr[38] else 0
                if outstanding > 0:
                    return price * outstanding / 100000000, trn
    except: pass
    # If we can't get shares, estimate from amount
    amount = spot_info.get("amount", 0)
    # Amount / 10 (rough turnover) * 100 = rough mcap
    if amount > 0:
        trn_est = 10.0
        est_mcap = amount / trn_est * 100 / 100000000
        return est_mcap, trn_est
    return None, None

async def run():
    mode = sys.argv[1] if len(sys.argv) > 1 else "morning"
    label = "早间" if mode == "morning" else "盘后"
    spot = _load_spot()
    if not spot: print("Spot failed"); return
    print(f"Spot: {len(spot)}")

    cand = []
    for code, s in spot.items():
        if code.startswith("bj"): continue
        if "ST" in s.get("name","") or "退" in s.get("name",""): continue
        if s.get("amount",0) < 200000000: continue
        cand.append({"code": code, "name": s["name"], "price": s["price"], "amount": s["amount"], "_spot": s})
    cand.sort(key=lambda x: x["amount"], reverse=True)
    top_cand = cand[:200]
    print(f"Pre: {len(top_cand)}")

    flt = []
    for c in top_cand:
        days5 = _fetch_kline(c["code"], 10)
        if len(days5) < 2: continue
        close = float(days5[-1][2])
        c5 = float(days5[-5][2]) if len(days5) >= 5 else float(days5[0][2])
        ch5 = (close - c5) / c5 * 100
        if not (ch5 >= _MIN_5D or any(
            abs((float(days5[i][2]) - float(days5[i][1])) / float(days5[i][1]) * 100) >= 9.5
            for i in range(max(0, len(days5)-5), len(days5))
        )): continue

        mcap_b, trn = _estimate_mcap(c["code"], c["price"], c["_spot"])
        if not mcap_b: continue
        if not (_MIN_MC <= mcap_b <= _MAX_MC) or (trn or 0) < 5.0: continue

        c["mcap"] = mcap_b; c["trn"] = trn or 10.0; c["ch5d"] = ch5
        flt.append(c)
    print(f"Flt: {len(flt)}")

    if not flt:
        from ai_news_radar.feishu_client import FeishuClient
        fc = FeishuClient()
        if fc.is_configured: await fc.send_screener_card(chat_id=CHAT_ID, screener_data=[])
        print("No results"); return

    fin = []
    for s in flt:
        sq = _calc_sqsm(s["code"])
        if sq and sq["total"] >= 9 and sq["yesterday"] < 9:
            s["sqsm"] = sq["score"]; fin.append(s)
    print(f"SQS: {len(fin)}")

    # Institutional data - isolated in subprocess
    if fin:
        try:
            import akshare as ak
            df = ak.stock_lhb_jgstatistic_em()
            inst = {}
            for _, row in df.iterrows():
                inst[str(row["代码"])] = float(row["机构净买额"])
            for s in fin:
                raw = s["code"].replace("sh","").replace("sz","")
                net = inst.get(raw, 0)
                if abs(net) >= 100000000:
                    s["jnst"] = f"{'净买' if net>0 else '净卖'}¥{abs(net)/100000000:.1f}亿"
                elif abs(net) >= 1000000:
                    s["jnst"] = f"{'净买' if net>0 else '净卖'}¥{abs(net)/10000:.0f}万"
                else: s["jnst"] = "-"
        except: pass

    fin.sort(key=lambda x: x["mcap"], reverse=True)
    top10 = fin[:_TOP_N]
    from ai_news_radar.feishu_client import FeishuClient
    fc = FeishuClient()
    if fc.is_configured:
        ok = await fc.send_screener_card(chat_id=CHAT_ID, screener_data=top10)
        print(f"Sent: {ok}")

if __name__ == "__main__":
    asyncio.run(run())