"""
Strategy Signal Tracker — records and tracks 十全十美 / 主力捉妖 signals.

Flow:
  1. When a signal is pushed, record_signal() is called -> stores in strategy_signals
  2. Daily update_task() refreshes prices for all active signals
  3. Signals older than 60 days auto-expire
"""
import logging
from datetime import datetime
from typing import Optional

import urllib.request
import json

from .database import Database

logger = logging.getLogger(__name__)

TRACKING_DAYS = 60  # Max tracking window


def _fetch_realtime_price(code: str) -> Optional[dict]:
    """Fetch latest price via Tencent API (same helper pattern)."""
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
            return {
                "price": float(arr[3]) if arr[3] else 0,
                "high": float(arr[33]) if len(arr) > 33 and arr[33] else 0,
                "low": float(arr[34]) if len(arr) > 34 and arr[34] else 0,
                "open": float(arr[5]) if arr[5] else 0,
                "pre_close": float(arr[4]) if arr[4] else 0,
            }
    except Exception:
        pass
    return None


def record_signal(strategy_type: str, stock_code: str, stock_name: str,
                  price: float, score: str = "") -> int:
    """Record a strategy signal. Returns signal_id."""
    db = Database()
    try:
        sid = db.record_strategy_signal(strategy_type, stock_code, stock_name, price, score)
        logger.info("Recorded %s signal: %s(%s) price=%.2f score=%s id=%d",
                     strategy_type, stock_name, stock_code, price, score, sid)
        return sid
    finally:
        db.close()


def update_daily_tracking() -> dict:
    """Update prices for all active signals. Returns stats dict."""
    db = Database()
    try:
        # First expire old signals
        expired = db.expire_old_signals()
        if expired:
            logger.info("Expired %d old signals", expired)

        signals = db.get_active_signals()
        updated = 0
        errors = 0

        for sig in signals:
            quote = _fetch_realtime_price(sig["stock_code"])
            if not quote:
                errors += 1
                continue

            signal_price = sig["price"]
            current = quote["price"]
            change_pct = (current - signal_price) / signal_price * 100 if signal_price > 0 else 0

            # Calculate peak (above entry) and drawdown from existing tracking
            prev_tracking = db.get_signal_tracking(sig["id"])
            peak_so_far = max(0.0, change_pct)   # highest gain above entry (0 = entry baseline)
            low_so_far = min(0.0, change_pct)    # lowest below entry
            for t in prev_tracking:
                peak_so_far = max(peak_so_far, max(0.0, t["peak_pct"]))
                low_so_far = min(low_so_far, t["change_pct"])
            peak_so_far = max(peak_so_far, change_pct)
            low_so_far = min(low_so_far, change_pct)

            # Drawdown: distance from highest peak to current (always >= 0)
            drawdown = max(0.0, peak_so_far - change_pct)

            db.update_signal_tracking(
                signal_id=sig["id"],
                price=current,
                high=quote.get("high", 0),
                low=quote.get("low", 0),
                change_pct=round(change_pct, 2),
                peak_pct=round(peak_so_far, 2),
                drawdown_pct=round(drawdown, 2),
            )
            updated += 1

        logger.info("Daily tracking: %d updated, %d errors, %d active",
                     updated, errors, len(signals))
        return {"active": len(signals), "updated": updated, "errors": errors, "expired": expired}
    finally:
        db.close()


def get_weekly_report() -> dict:
    """Generate a weekly performance report for all strategies."""
    db = Database()
    try:
        sqsm_signals = db.get_signal_report(strategy_type="sqsm", since_days=7)

        # Stats
        def _calc_stats(signals: list) -> dict:
            total = len(signals)
            if total == 0:
                return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                        "avg_return": 0, "total_return": 0, "best": None, "worst": None}

            wins = sum(1 for s in signals if s.get("change_pct", 0) > 0)
            losses = sum(1 for s in signals if s.get("change_pct", 0) <= 0)
            returns = [s.get("change_pct", 0) for s in signals if s.get("change_pct") is not None]

            best = max(signals, key=lambda s: s.get("change_pct", 0))
            worst = min(signals, key=lambda s: s.get("change_pct", 0))

            return {
                "total": total,
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "avg_return": round(sum(returns) / len(returns), 2) if returns else 0,
                "total_return": round(sum(returns), 2) if returns else 0,
                "best": {"name": best.get("stock_name", ""), "code": best.get("stock_code", ""),
                          "return": best.get("change_pct", 0), "score": best.get("score", "")},
                "worst": {"name": worst.get("stock_name", ""), "code": worst.get("stock_code", ""),
                           "return": worst.get("change_pct", 0), "score": worst.get("score", "")},
            }

        sqsm_stats = _calc_stats(sqsm_signals)

        # Historical cumulative stats (all time)
        all_time = db.get_signal_report(strategy_type="sqsm", since_days=365)
        history = _calc_stats(all_time)

        now = datetime.now()
        week_start = now.strftime("%Y-%m-%d")
        week_end = now.strftime("%Y-%m-%d")

        return {
            "week_start": week_start,
            "week_end": week_end,
            "sqsm": sqsm_stats,
            "history": history,
            "all_signals": sqsm_signals,
        }
    finally:
        db.close()
