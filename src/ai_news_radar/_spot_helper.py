"""Load spot data via akshare, output JSON. Used as subprocess to isolate crash."""
import sys, json, warnings
warnings.filterwarnings('ignore')
try:
    import akshare as ak
    df = ak.stock_zh_a_spot()
    result = {}
    for _, row in df.iterrows():
        try:
            code = str(row.iloc[0])
            result[code] = {
                'name': str(row.iloc[1]),
                'price': float(row.iloc[2]) if len(row) > 2 else 0,
                'high': float(row.iloc[6]) if len(row) > 6 else 0,
                'low': float(row.iloc[7]) if len(row) > 7 else 0,
                'change_pct': float(row.iloc[4]) if len(row) > 4 else 0,
                'amount': float(row.iloc[12]) if len(row) > 12 else 0,
            }
        except:
            pass
    print(json.dumps(result, ensure_ascii=False))
except Exception as e:
    print(json.dumps({'error': str(e)}))
