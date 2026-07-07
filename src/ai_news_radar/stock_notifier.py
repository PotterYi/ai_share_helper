"""
Stock notification dispatcher — sends personalized stock reports
to each user via their webhook URL (Feishu / custom).
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

import httpx

from .database import Database
from .stock_analyzer import _get_realtime_price, analyze_stock
from .feishu_client import FeishuClient

logger = logging.getLogger(__name__)


def _format_analysis(analysis: dict) -> list[str]:
    """Extract key analysis lines from AI analysis result (morning mode)."""
    lines = []
    if not analysis or "error" in analysis:
        return lines

    trend_map = {
        "up": "上涨趋势", "down": "下跌趋势",
        "sideways": "震荡趋势", "volatile": "剧烈波动",
    }
    strength_map = {"strong": "强", "moderate": "中等", "weak": "弱"}
    sug_map = {"buy": "建议买入", "sell": "建议卖出", "hold": "建议持有", "wait": "建议观望"}

    trend = analysis.get("trend", "")
    strength = analysis.get("trend_strength", "")
    suggestion = analysis.get("suggestion", "")

    trend_display = trend_map.get(trend, trend)
    strength_display = strength_map.get(strength, "")

    lines.append(f"     AI分析: {trend_display} ({strength_display})")

    if suggestion:
        sug_display = sug_map.get(suggestion, suggestion)
        lines.append(f"     操作建议: {sug_display}")
        if analysis.get("suggestion_reason"):
            lines.append(f"     {analysis['suggestion_reason']}")

    if analysis.get("entry_point") or analysis.get("stop_loss"):
        parts = []
        if analysis.get("entry_point"):
            parts.append(f"入场: {analysis['entry_point']}")
        if analysis.get("stop_loss"):
            parts.append(f"止损: {analysis['stop_loss']}")
        if analysis.get("target_price"):
            parts.append(f"目标: {analysis['target_price']}")
        if parts:
            lines.append(f"     {' | '.join(parts)}")

    risk_map = {"high": "高风险", "medium": "中等风险", "low": "低风险"}
    risk = analysis.get("risk_level", "")
    if risk:
        lines.append(f"     {risk_map.get(risk, risk)}")

    # Urgent action
    action = analysis.get("current_action", {})
    if action.get("should_act"):
        urgency_map = {"urgent": "紧急", "today": "今日", "this_week": "本周", "no_need": ""}
        urgency_str = urgency_map.get(action.get("urgency", ""), "")
        act_str = action.get("action", "")
        if urgency_str:
            lines.append(f"     >> {urgency_str} {act_str}")
        if action.get("reason"):
            lines.append(f"     {action['reason']}")

    return lines


def _format_evening_analysis(analysis: dict) -> list[str]:
    """Extract key analysis lines from AI analysis result (evening mode)."""
    lines = []
    if not analysis or "error" in analysis:
        return lines

    sug_map = {"buy": "建议买入", "sell": "建议卖出", "hold": "建议持有", "wait": "建议观望"}

    # Today performance
    perf = analysis.get("today_performance", "")
    if perf:
        lines.append(f"     今日表现: {perf}")
    else:
        hl = analysis.get("today_high")
        lw = analysis.get("today_low")
        cl = analysis.get("today_close")
        if hl or lw or cl:
            parts = []
            if hl: parts.append(f"最高 {hl}")
            if lw: parts.append(f"最低 {lw}")
            if cl: parts.append(f"收盘 {cl}")
            lines.append(f"     今日行情: {' | '.join(parts)}")

    # Key levels
    kl = analysis.get("key_levels", "")
    if kl:
        lines.append(f"     关键价位: {kl}")

    # Tomorrow outlook
    outlook = analysis.get("tomorrow_outlook", "")
    if outlook:
        lines.append(f"     明日展望: {outlook}")

    # Tomorrow strategy
    strategy = analysis.get("tomorrow_strategy", "")
    if strategy:
        lines.append(f"     明日策略: {strategy}")

    # Suggestion
    suggestion = analysis.get("suggestion", "")
    if suggestion:
        sug_display = sug_map.get(suggestion, suggestion)
        lines.append(f"     操作建议: {sug_display}")
        if analysis.get("suggestion_reason"):
            lines.append(f"     {analysis['suggestion_reason']}")

    if analysis.get("entry_point") or analysis.get("stop_loss"):
        parts = []
        if analysis.get("entry_point"):
            parts.append(f"入场: {analysis['entry_point']}")
        if analysis.get("stop_loss"):
            parts.append(f"止损: {analysis['stop_loss']}")
        if analysis.get("target_price"):
            parts.append(f"目标: {analysis['target_price']}")
        if parts:
            lines.append(f"     {' | '.join(parts)}")

    risk_map = {"high": "高风险", "medium": "中等风险", "low": "低风险"}
    risk = analysis.get("risk_level", "")
    if risk:
        lines.append(f"     {risk_map.get(risk, risk)}")

    # Tomorrow action
    action = analysis.get("tomorrow_action", {})
    if action.get("should_act"):
        urgency_map = {"urgent": "紧急", "tomorrow": "明日", "this_week": "本周", "no_need": ""}
        urgency_str = urgency_map.get(action.get("urgency", ""), "")
        act_str = action.get("action", "")
        if urgency_str:
            lines.append(f"     >> {urgency_str} {act_str}")
        if action.get("reason"):
            lines.append(f"     {action['reason']}")

    return lines


def _grade_signal(sqsm_score: str, sqsm_days: list) -> dict:
    """Signal grading system: maps SQSM score + trend to recommendation level.

    Returns:
        dict with grade, label, level_stars, advice.
    """
    # Parse current score
    try:
        parts = sqsm_score.split("/")
        score = int(parts[0])
        max_score = int(parts[1]) if len(parts) > 1 else 10
    except:
        return {"grade": "N/A", "label": "暂无数据", "level_stars": "—", "advice": ""}

    ratio = score / max_score if max_score > 0 else 0

    # Check if trend is improving or declining
    improving = False
    declining = False
    consecutive_high = 0
    if len(sqsm_days) >= 2:
        try:
            cur = sqsm_days[0].get("score", 0)
            prev = sqsm_days[1].get("score", 0)
            improving = cur > prev
            declining = cur < prev
            # Count consecutive days >= 9
            for d in sqsm_days:
                if d.get("score", 0) >= 9:
                    consecutive_high += 1
                else:
                    break
        except:
            pass

    if score >= 9:
        if consecutive_high >= 3:
            return {"grade": "SS", "label": "★★★ 强烈关注",
                    "level_stars": "★★★",
                    "advice": "连续多日共振，趋势强劲，技术与基本面共振"}
        elif improving:
            return {"grade": "S", "label": "★★ 关注",
                    "level_stars": "★★",
                    "advice": "今日首次共振或评分提升，技术面转好"}
        else:
            return {"grade": "S", "label": "★★ 关注",
                    "level_stars": "★★",
                    "advice": "技术面共振中，持续跟踪"}
    elif score >= 7:
        if improving:
            return {"grade": "A", "label": "★ 观察",
                    "level_stars": "★",
                    "advice": "接近共振区间，评分在提升，可关注等待确认"}
        else:
            return {"grade": "A", "label": "★ 观察",
                    "level_stars": "★",
                    "advice": "接近共振但尚未达标，需持续观察"}
    elif score >= 5:
        return {"grade": "B", "label": "观望",
                "level_stars": "—",
                "advice": "评分中等，暂时观望等待信号明朗"}
    else:
        return {"grade": "C", "label": "不推荐",
                "level_stars": "—",
                "advice": "评分较低，不适合当前介入"}


def _get_sqsm_suggestion(sqsm_records_3days: list, inst_buy: float = 0, inst_sell: float = 0) -> str:
    """Determine 十全十美 trading suggestion based on sqsm trend + institutional data.

    Logic:
      - 机构大量买入(>5000万) + 前天不共振→今天共振 → "买入"
      - 机构大量卖出(>5000万) + 前天共振→今天不共振 → "卖出"
      - 传统: 连续3天≥9共振 → "买入"
      - 传统: 连续3天≥9后跌破 → "卖出"
      - 其他 → 无建议

    Args:
        sqsm_records_3days: list of dicts [{"score": N, "date": "..."}, ...]
        inst_buy: institutional buy amount in yuan
        inst_sell: institutional sell amount in yuan

    Returns:
        "" (no suggestion), "买入" or "卖出"
    """
    if len(sqsm_records_3days) < 3:
        return ""

    d1 = sqsm_records_3days[0].get("score", 0)  # today
    d2 = sqsm_records_3days[1].get("score", 0)  # yesterday
    d3 = sqsm_records_3days[2].get("score", 0)  # day before

    heavy_buy = inst_buy > 50_000_000  # >5000万
    heavy_sell = inst_sell > 50_000_000

    # 机构买入 + 不共振→共振
    if heavy_buy and d1 >= 9 and d2 < 9:
        return "买入"

    # 机构卖出 + 共振→不共振
    if heavy_sell and d1 < 9 and d2 >= 9:
        return "卖出"

    # 传统逻辑
    if d1 >= 9 and d2 >= 9 and d3 >= 9:
        return "买入"
    if d1 < 9 and d2 >= 9 and d3 >= 9:
        if len(sqsm_records_3days) >= 4 and sqsm_records_3days[3].get("score", 0) >= 9:
            return "卖出"

    return ""


def _load_inst_data() -> dict:
    """Load institutional buy/sell data from LHB."""
    import akshare as ak, warnings
    warnings.filterwarnings("ignore")
    try:
        df = ak.stock_lhb_jgstatistic_em()
        result = {}
        for _, row in df.iterrows():
            rc = str(row["代码"])
            result[rc] = {"buy": float(row["机构买入额"]), "sell": float(row["机构卖出额"]), "net": float(row["机构净买额"])}
        return result
    except: return {}


async def _compute_sqsm_for_stock(code: str, records: list, inst_data: list = None) -> dict:
    """Compute 十全十美 for current and recent days from price records.

    Args:
        code: Stock code.
        records: List of OHLC dicts, newest last.
        inst_data: dict of institutional data {raw_code: {buy, sell, net}}

    Returns:
        dict with sqsm_score, sqsm_suggestion, and per-day breakdown.
    """
    from .sqsm_indicator import ShiQuanShiMei
    import numpy as np
    import json

    result = {"sqsm_score": "-/-", "sqsm_suggestion": "", "sqsm_days": []}

    if len(records) < 80:
        return result

    c = np.array([r["close"] for r in records], dtype=float)
    h = np.array([r["high"] for r in records], dtype=float)
    l = np.array([r["low"] for r in records], dtype=float)
    v = np.array([r["volume"] for r in records], dtype=float)

    sqsm = ShiQuanShiMei()
    total = len(c)

    # Compute sqsm for last 4 days by truncating data
    day_scores = []
    for offset in range(4):
        if total - offset < 80:
            break
        c_slice = c[:total - offset]
        h_slice = h[:total - offset]
        l_slice = l[:total - offset]
        v_slice = v[:total - offset]
        res = sqsm.calculate(c_slice, h_slice, l_slice, v_slice)
        if "error" not in res:
            score = res.get("total", 0)
            day = {"date": records[total - 1 - offset]["date"], "score": score}
            day_scores.append(day)

    result["sqsm_days"] = day_scores

    if day_scores:
        score = day_scores[0].get("score", 0)
        result["sqsm_score"] = f"{score}/10"
        # Extract institutional data for this stock
        inst_buy = 0
        inst_sell = 0
        if inst_data and code in inst_data:
            d = inst_data.get(code.replace("sh","").replace("sz",""), {})
            inst_buy = d.get("buy", 0)
            inst_sell = d.get("sell", 0)
        result["sqsm_suggestion"] = _get_sqsm_suggestion(day_scores, inst_buy, inst_sell)

    return result


async def build_user_stock_report_async(user: dict, stocks: list[dict], mode: str = "morning") -> Optional[dict]:
    """Build a personalized stock report with structured data for card rendering.

    Args:
        user: User dict from database.
        stocks: List of stock dicts (watched=1).
        mode: "morning" or "evening".

    Returns:
        dict with "stock_data" (list) and "total_pnl" (float), or None.
    """
    if not stocks:
        return None

    is_evening = mode == "evening"
    stock_results = []
    total_pnl = 0.0

    for s in stocks:
        code = s["stock_code"]
        buy_price = s["buy_price"]
        qty = s.get("quantity", 0) or 0

        quote = _get_realtime_price(code)
        if quote:
            current = quote.get("price", 0)
            name = quote.get("name", code)
        else:
            current = 0
            name = code

        change_pct = 0
        pnl = 0
        if current and buy_price and buy_price > 0:
            change_pct = (current - buy_price) / buy_price * 100
            pnl = (current - buy_price) * (qty or 1)
            total_pnl += pnl

        stock_item = {
            "name": name,
            "code": code,
            "buy_price": buy_price or 0,
            "quantity": qty,
            "current_price": current,
            "change_pct": change_pct,
            "pnl": pnl,
            "sqsm_score": "-/-",
            "sqsm_suggestion": "",
            "ai_trend": "",
            "ai_suggestion": "",
            "ai_summary": "",
            # 机构数据默认值（始终渲染卡片，哪怕是"暂无数据"）
            "inst_loaded": False,
            "inst_buy": 0,
            "inst_sell": 0,
            # 共振趋势默认值
            "sqsm_trend": "",
        }

        # Load institutional data once — 始终标记已加载，让卡片决定显示什么
        try:
            inst_data_db = _load_inst_data()
        except:
            inst_data_db = {}
        stock_item["inst_loaded"] = True  # 永远置True: 告诉卡片"已检查过机构数据"
        raw_code = code.replace("sh", "").replace("sz", "")
        if raw_code in inst_data_db:
            stock_item["inst_buy"] = inst_data_db[raw_code]["buy"]
            stock_item["inst_sell"] = inst_data_db[raw_code]["sell"]

        # 十全十美
        try:
            from .stock_analyzer import _fetch_stock_daily
            records = _fetch_stock_daily(code, days=500)
            if len(records) >= 80:
                sqsm_info = await _compute_sqsm_for_stock(code, records, inst_data_db)

                # Update sqsm_history in database
                if sqsm_info.get("sqsm_days"):
                    from .database import Database
                    db_sqsm = Database()
                    d0 = sqsm_info["sqsm_days"][0]
                    db_sqsm.update_sqsm_history(
                        stock_code=code,
                        source_account="凡尘一灯",  # personal tracking
                        score=d0.get("score", 0),
                        date_str=d0.get("date", ""),
                    )
                    db_sqsm.close()

                stock_item["sqsm_score"] = sqsm_info.get("sqsm_score", "-/-")
                if sqsm_info.get("sqsm_suggestion"):
                    stock_item["sqsm_suggestion"] = sqsm_info["sqsm_suggestion"]

                # Signal grading
                sqsm_days_for_grade = sqsm_info.get("sqsm_days", [])
                grade_info = _grade_signal(stock_item["sqsm_score"], sqsm_days_for_grade)
                stock_item["sqsm_grade"] = grade_info.get("label", "")
                stock_item["sqsm_grade_level"] = grade_info.get("level_stars", "")
                stock_item["sqsm_advice"] = grade_info.get("advice", "")

                # Thesis tracking: detect signal degradation
                stock_item["thesis_status"] = ""
                stock_item["thesis_detail"] = ""
                if len(sqsm_days_for_grade) >= 3:
                    try:
                        cur_score = sqsm_days_for_grade[0].get("score", 0)
                        prev_3day = sqsm_days_for_grade[2].get("score", 0)
                        if cur_score < prev_3day:
                            drop = prev_3day - cur_score
                            stock_item["thesis_status"] = "⚠️ 信号退化"
                            stock_item["thesis_detail"] = f"3日内评分从{prev_3day}/10降至{cur_score}/10，降幅{drop}分"
                        elif cur_score >= 9 and prev_3day >= 9:
                            stock_item["thesis_status"] = "✅ 信号维持"
                            stock_item["thesis_detail"] = f"连续维持高分({cur_score}/10)，原推荐逻辑依然有效"
                        elif cur_score >= 9 and prev_3day < 9:
                            stock_item["thesis_status"] = "📈 信号增强"
                            stock_item["thesis_detail"] = f"评分从{prev_3day}/10升至{cur_score}/10，逻辑加强"
                    except:
                        pass

                # Resonance trend
                days = sqsm_info.get("sqsm_days", [])
                if len(days) >= 1:
                    cur = days[0].get("score", 0)
                    con = 1
                    for d in days[1:]:
                        if (d.get("score", 0) >= 9) == (cur >= 9):
                            con += 1
                        else:
                            break
                    stock_item["sqsm_trend"] = f"已共振{con}天" if cur >= 9 else f"停止共振{con}天"
                else:
                    stock_item["sqsm_trend"] = ""
        except Exception:
            pass

        # AI analysis
        try:
            analysis_result = await analyze_stock(code, mode=mode)
            analysis = analysis_result.get("analysis", {})
            if analysis and "error" not in analysis:
                trend_map = {"up": "上涨", "down": "下跌", "sideways": "震荡", "volatile": "波动"}
                sug_map = {"buy": "建议买入", "sell": "建议卖出", "hold": "建议持有", "wait": "建议观望"}
                stock_item["ai_trend"] = trend_map.get(analysis.get("trend", ""), "")
                stock_item["ai_suggestion"] = sug_map.get(analysis.get("suggestion", ""), "")
                stock_item["ai_summary"] = analysis.get("technical_summary", "")[:80]
                stock_item["ai_entry"] = analysis.get("entry_point", "") or ""
                stock_item["ai_stop_loss"] = analysis.get("stop_loss", "") or ""
                stock_item["ai_target"] = analysis.get("target_price", "") or ""
                risk_map = {"high": "高风险", "medium": "中等风险", "low": "低风险"}
                stock_item["ai_risk"] = risk_map.get(analysis.get("risk_level", ""), "") or ""
                # Urgent action
                action = analysis.get("current_action", {})
                if action.get("should_act"):
                    urgency_map = {"urgent": "紧急", "today": "今日", "this_week": "本周", "no_need": ""}
                    stock_item["ai_action"] = urgency_map.get(action.get("urgency", ""), "") + " " + action.get("action", "")
                else:
                    stock_item["ai_action"] = ""
        except Exception as e:
            logger.warning("AI analysis failed for %s: %s", code, e)

        stock_results.append(stock_item)

    return {
        "stock_data": stock_results,
        "total_pnl": total_pnl,
    }


def _build_feishu_card(user_report: str) -> dict:
    """Wrap the report text into a Feishu interactive card."""
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "📊 股票日报"},
                "template": "blue",
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": user_report,
                },
                {
                    "tag": "hr",
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": f"AI News Radar · {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                        }
                    ],
                },
            ],
        },
    }


def _build_feishu_text(report: str) -> dict:
    """Send as plain text message instead of card."""
    return {
        "msg_type": "text",
        "content": {"text": report},
    }


async def send_webhook(url: str, report: str) -> bool:
    """Send stock report to a webhook URL (Feishu / custom)."""
    if not url:
        logger.debug("No webhook URL, skipping")
        return False

    # Detect Feishu webhook
    is_feishu = "feishu" in url.lower() or "lark" in url.lower() or "open.feishu" in url.lower()

    if is_feishu:
        payload = _build_feishu_card(report)
    else:
        # Generic markdown webhook
        payload = {"msgtype": "markdown", "markdown": {"content": report}}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code in (200, 201, 204):
                logger.info("Webhook sent successfully to %s...", url[:40])
                return True
            else:
                logger.warning(
                    "Webhook returned %d for %s: %s",
                    resp.status_code, url[:40], resp.text[:100],
                )
                return False
    except Exception as e:
        logger.error("Webhook send failed for %s: %s", url[:40], e)
        return False


async def check_and_notify_all_users(dry_run: bool = False, mode: str = "morning") -> dict:
    """Send daily stock report via Feishu private message to eligible users.

    Only sends to users who:
      - have notify_enabled = 1 (master switch)
      - have at least one stock with daily_notify = 1
      - have a valid feishu_open_id

    Webhook is NOT used here — it's reserved for future group broadcast modules.

    Args:
        dry_run: If True, only print report without sending.
        mode: "morning" for trend analysis + buy/sell advice,
              "evening" for today's summary + tomorrow's strategy.
    Returns summary dict with counts.
    """
    db = Database()

    # Only users who have daily_notify enabled & notify_enabled & open_id
    users = db.get_users_with_daily_notify()
    total_users_in_db = len(db.get_all_users_with_stocks())

    result = {
        "total_users": total_users_in_db,
        "daily_eligible": len(users),
        "notified": 0,
        "skipped_no_stocks": 0,
        "skipped_disabled": 0,
        "skipped_no_openid": 0,
        "errors": [],
    }

    for u in users:
        uid = u["id"]
        feishu_id = u.get("feishu_id", "")

        # Get stocks with daily_notify = 1
        stocks = db.get_user_daily_stocks(uid)
        if not stocks:
            result["skipped_no_stocks"] += 1
            logger.info(
                "User %s has no daily-notify stocks, skipping",
                u.get("username", feishu_id),
            )
            continue

        report_data = await build_user_stock_report_async(u, stocks, mode=mode)
        if not report_data:
            result["skipped_no_stocks"] += 1
            continue

        open_id = u.get("feishu_open_id", "") or ""

        if dry_run:
            stock_summary = ", ".join(
                f"{s['name']}({s['sqsm_score']})"
                for s in report_data.get("stock_data", [])
            )
            logger.info(
                "[DRY RUN][%s] Would notify %s (%s) via private card with %d stocks: %s",
                mode,
                u.get("username", feishu_id),
                feishu_id,
                len(stocks),
                stock_summary,
            )
            result["notified"] += 1
            continue

        # Send via beautiful Feishu interactive card
        is_evening = mode == "evening"
        fc = FeishuClient()

        if not fc.is_configured:
            logger.error("Feishu App ID/Secret not configured, cannot send private message")
            result["errors"].append(feishu_id)
            continue

        pm_ok = await fc.send_private_card(
            open_id=open_id,
            stock_data=report_data.get("stock_data", []),
            is_evening=is_evening,
            total_pnl=report_data.get("total_pnl", 0),
        )
        if pm_ok:
            result["notified"] += 1
            logger.info(
                "Daily report card sent via private msg to %s (%d stocks)",
                u.get("username", feishu_id),
                len(stocks),
            )
        else:
            result["errors"].append(feishu_id)
            logger.warning(
                "Failed to send private card to %s", u.get("username", feishu_id)
            )

    db.close()
    return result


async def run_stock_check(dry_run: bool = False, mode: str = "morning"):
    """CLI entry point for stock check."""
    mode_label = "早间研判" if mode == "morning" else "收盘复盘"
    result = await check_and_notify_all_users(dry_run=dry_run, mode=mode)

    print()
    print("  ╔══════════════════════════════════════╗")
    print(f"  ║  📊 {mode_label}完成                   ║")
    print(f"  ║  总用户数: {result['total_users']:>2d}                           ║")
    print(f"  ║  日报可用: {result['daily_eligible']:>2d}                           ║")
    print(f"  ║  已通知:   {result['notified']:>2d}                            ║")
    if result["errors"]:
        print(f"  ║  发送失败: {len(result['errors'])}                            ║")
        print(f"  ║  {', '.join(result['errors'])}")
    print("  ╚══════════════════════════════════════╝")
    print()
