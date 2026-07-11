"""ZLZY tracking pool — dedicated tracking for 主力捉妖 signals.
每天16:00更新，跟踪25个交易日，展示对比数据。"""
import logging
from datetime import datetime, timedelta
from typing import Optional
import urllib.request, json

from .database import Database

logger = logging.getLogger(__name__)

TRADING_DAYS_LIMIT = 25


def _count_weekdays(from_date: str, to_date: str) -> int:
    """Count weekdays (Mon-Fri) between two dates."""
    from_dt = datetime.strptime(from_date[:10], "%Y-%m-%d")
    to_dt = datetime.strptime(to_date[:10], "%Y-%m-%d")
    if to_dt < from_dt:
        return 0
    days = 0
    current = from_dt
    while current <= to_dt:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


def _fetch_latest_price(code: str) -> Optional[float]:
    """Fetch latest price via Tencent API."""
    raw = code.replace("sh", "").replace("sz", "").replace("bj", "")
    pref = "sh" if raw[0] in "651" else "sz" if raw[0] in "023" else "bj"
    url = f"http://ifzq.gtimg.cn/appstock/app/fqkline/get?param={pref}{raw},day,,,2,qfq"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        sd = d.get("data", {}).get(pref + raw, {})
        qt = sd.get("qt", {})
        arr = qt.get(pref + raw, [])
        if isinstance(arr, list) and len(arr) >= 38:
            return float(arr[3]) if arr[3] else None
    except:
        pass
    return None


def get_zlzy_tracking_pool() -> dict:
    """Get all active ZLZY signals within 25 trading days with latest prices.

    Returns:
        dict with signals list and summary stats.
    """
    db = Database()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        signals = db.get_active_signals_by_strategy("zlzy")
        
        pool = []
        total_change = 0
        wins = 0
        losses = 0
        
        for sig in signals:
            # Check trading days
            trading_days = _count_weekdays(sig["signal_date"], today)
            if trading_days > TRADING_DAYS_LIMIT:
                continue
                
            entry_price = sig["price"]

            # Recalculate from all tracking records for accuracy
            all_tracking = db.get_signal_tracking(sig["id"])

            if all_tracking:
                latest = all_tracking[-1]
                current_price = latest["price"]
                change_pct = latest["change_pct"]
                peak_pct = max(t["peak_pct"] for t in all_tracking)
                drawdown_pct = max(t["drawdown_pct"] for t in all_tracking)

                # If latest tracking is not today, fetch fresh price
                if latest["track_date"] != today:
                    fresh = _fetch_latest_price(sig["stock_code"])
                    if fresh and abs(fresh - current_price) / max(current_price, 0.001) > 0.001:
                        current_price = fresh
                        change_pct = round((fresh - entry_price) / entry_price * 100, 2) if entry_price > 0 else 0
                        peak_pct = max(peak_pct, change_pct)
            else:
                current_price = entry_price
                change_pct = 0.0
                peak_pct = 0.0
                drawdown_pct = 0.0
            
            if change_pct > 0:
                wins += 1
            else:
                losses += 1
            total_change += change_pct
            
            pool.append({
                "name": sig["stock_name"],
                "code": sig["stock_code"],
                "entry_price": entry_price,
                "current_price": current_price,
                "change_pct": round(change_pct, 2),
                "peak_pct": round(peak_pct, 2),
                "drawdown_pct": round(drawdown_pct, 2),
                "signal_date": sig["signal_date"],
                "trading_days": trading_days,
                "score": sig.get("score", ""),
            })
        
        pool.sort(key=lambda x: x["signal_date"], reverse=True)
        total = len(pool)
        
        return {
            "signals": pool,
            "total": total,
            "wins": wins,
            "losses": losses,
            "avg_return": round(total_change / total, 2) if total > 0 else 0,
            "update_time": today,
        }
    finally:
        db.close()
