"""Enricher helper - standalone subprocess for PE/PB/52w/financial data.
Uses qt.gtimg.cn (Tencent) for PE/PB and datacenter.eastmoney.com for financials.
NO akshare dependency.
"""
import sys, json, urllib.request, urllib.parse, warnings, os, ssl
warnings.filterwarnings("ignore")

def _fetch_qq_quote(code: str) -> dict:
    """Fetch PE/PB/52w from qt.gtimg.cn (GBK encoding)."""
    url = f"https://qt.gtimg.cn/q={code}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gbk")
        start = text.find('"')
        end = text.rfind('"')
        if start >= 0 and end > start:
            fields = text[start+1:end].split("~")
            if len(fields) >= 49:
                return {
                    "name": fields[1],
                    "price": float(fields[3]) if fields[3] else 0,
                    "pe": float(fields[39]) if fields[39] and fields[39] != "-" else 0,
                    "pb": float(fields[46]) if fields[46] and fields[46] != "-" else 0,
                    "mcap": float(fields[45]) if fields[45] and fields[45] != "-" else 0,
                    "float_cap": float(fields[44]) if fields[44] and fields[44] != "-" else 0,
                    "high_52w": float(fields[47]) if fields[47] and fields[47] != "-" else 0,
                    "low_52w": float(fields[48]) if fields[48] and fields[48] != "-" else 0,
                    "turnover_rate": float(fields[38]) if fields[38] and fields[38] != "-" else 0,
                }
    except:
        pass
    return {}

def _fetch_industry(code_clean: str, market: str) -> str:
    """Fetch industry (BOARD_NAME) for a stock via East Money datacenter API."""
    try:
        url = ("https://datacenter.eastmoney.com/securities/api/data/v1/get?"
               "reportName=RPT_LICO_FN_CPD&columns=SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_NAME"
               "&sortTypes=-1&sortColumns=SECURITY_CODE&source=HSF10&client=PC&pageNumber=1&pageSize=1"
               "&filter=" + urllib.parse.quote(f'(SECUCODE="{code_clean}.{market}")'))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("result", {}).get("data", [])
        if items:
            return items[0].get("BOARD_NAME", "")
    except:
        pass
    return ""


def _fetch_financials(code_clean: str, market: str) -> dict:
    """Fetch fundamental financial data from East Money."""
    url = ("https://datacenter.eastmoney.com/securities/api/data/get?" +
           "type=RPT_F10_FINANCE_MAINFINADATA&sty=ALL&sr=-1&st=REPORT_DATE" +
           "&source=HSF10&client=PC&ps=3&p=1" +
           "&filter=" + urllib.parse.quote(f'(SECUCODE="{code_clean}.{market}")(REPORT_TYPE="年报")'))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        reports = data.get("result", {}).get("data", [])
        if not reports:
            return {}
        r = reports[0]
        result = {}
        for key in ["TOTALOPERATEREVE", "PARENTNETPROFIT", "EPSJB", "BPS",
                     "ROEJQ", "TOTALOPERATEREVETZ", "PARENTNETPROFITTZ"]:
            val = r.get(key)
            if val is not None:
                result[key] = val
        if reports and len(reports) > 1:
            r2 = reports[1]
            result["PREV_REVENUE"] = r2.get("TOTALOPERATEREVE")
        return result
    except:
        return {}

code = sys.argv[1] if len(sys.argv) > 1 else ""
if not code:
    print(json.dumps({"error": "no code"}))
    sys.exit(0)

code_clean = code.replace("sh", "").replace("sz", "").replace("bj", "")
market = "SH" if code_clean[0] in "6591" else "SZ"

qq = _fetch_qq_quote(code)
fin = _fetch_financials(code_clean, market)
industry = _fetch_industry(code_clean, market)

result = {**qq, **{"financials": fin, "industry": industry}}
print(json.dumps(result, ensure_ascii=False))
