"""主力作妖推荐 - 每日早间扫描筛选系统
每天 08:00 执行筛选并推送群通知
"""
import sys, json, subprocess, os, asyncio, warnings, urllib.request
warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
CHAT_ID = "oc_8792267760e09f7c142bb0157bcf22f0"
_MIN_5D = 7.0; _TOP_N = 10
ZLZY_HELPER = os.path.join(BASE, "src", "ai_news_radar", "_zlzy_helper.py")
SPOT_HELPER = os.path.join(BASE, "src", "ai_news_radar", "_spot_helper.py")

def _load_spot():
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
    except:
        return []

def _estimate_mcap(code, price, spot_info):
    try:
        raw = code.replace("sh","").replace("sz","").replace("bj","")
        pref = "sh" if raw[0] in "651" else "sz" if raw[0] in "023" else "bj"
        url = f"http://ifzq.gtimg.cn/appstock/app/fqkline/get?param={pref}{raw},day,,,2,qfq"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        sd = d.get("data", {}).get(pref + raw, {})
        qt = sd.get("qt", {})
        if isinstance(qt, dict):
            arr = qt.get(pref + raw, [])
            if isinstance(arr, list) and len(arr) >= 74:
                outstanding = float(arr[72]) if arr[72] else 0
                trn = float(arr[38]) if arr[38] else 0
                if outstanding > 0:
                    return price * outstanding / 100000000, trn
    except:
        pass
    amount = spot_info.get("amount", 0)
    if amount > 0:
        trn_est = 10.0
        est_mcap = amount / trn_est * 100 / 100000000
        return est_mcap, trn_est
    return None, None

def _calc_zlzy(code):
    r = subprocess.run([PYTHON, ZLZY_HELPER, code], capture_output=True, text=True, timeout=120,
                       cwd=BASE, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None
    for line in r.stdout.strip().split(chr(10)):
        if "SIGNAL:" in line:
            parts = line.split()
            sig = int(parts[0].split(":")[1])
            prev = int(parts[1].split(":")[1])
            yest = int(parts[2].split(":")[1])
            return {"signal": bool(sig), "prev": bool(prev), "yesterday": bool(yest), "is_new": sig == 1 and yest == 0}
    return None

async def run():
    label = "主力作妖早间"
    spot = _load_spot()
    if not spot:
        print("Spot failed")
        return
    print(f"Spot: {len(spot)}")

    cand = []
    for code, s in spot.items():
        if code.startswith("bj"): continue
        if "ST" in s.get("name","") or chr(36864) in s.get("name",""): continue
        if s.get("amount",0) < 200000000: continue
        cand.append({"code": code, "name": s["name"], "price": s["price"], "amount": s["amount"], "_spot": s})
    cand.sort(key=lambda x: x["amount"], reverse=True)
    top_cand = cand[:1000]  # 扩大候选池，覆盖成交额较小的信号股
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
        c["mcap"] = mcap_b or 0; c["trn"] = trn or 0; c["ch5d"] = ch5
        flt.append(c)
    print(f"Flt: {len(flt)}")

    if not flt:
        from ai_news_radar.feishu_client import FeishuClient
        fc = FeishuClient()
        if fc.is_configured:
            await fc.send_zlzy_card(chat_id=CHAT_ID, zlzy_data=[])
        print("No results")
        return

    fin = []
    for s in flt:
        zlzy = _calc_zlzy(s["code"])
        if zlzy and zlzy["signal"] and zlzy["yesterday"] == False:
            s["zlzy"] = "触发" if zlzy["signal"] else "未触发"
            fin.append(s)
    print(f"ZLZY: {len(fin)}")

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
                    s["jnst"] = f"{"净买" if net>0 else "净卖"}Y{abs(net)/100000000:.1f}亿"
                elif abs(net) >= 1000000:
                    s["jnst"] = f"{"净买" if net>0 else "净卖"}Y{abs(net)/10000:.0f}万"
                else:
                    s["jnst"] = "-"
        except:
            pass

    fin.sort(key=lambda x: x["mcap"], reverse=True)
    top10 = fin[:_TOP_N]
    from ai_news_radar.feishu_client import FeishuClient
    fc = FeishuClient()
    if fc.is_configured:
        ok = await fc.send_zlzy_card(chat_id=CHAT_ID, zlzy_data=top10)
        print(f"Sent: {ok}")

if __name__ == "__main__":
    asyncio.run(run())
