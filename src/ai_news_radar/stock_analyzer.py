"""
Stock & Fund Analyzer — fetches data via AKShare and uses AI for trend analysis.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from .config import get_deepseek_api_key, get_deepseek_base_url
from .utils.helpers import normalize_stock_code

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Data fetching via AKShare
# ─────────────────────────────────────────────


def _fetch_stock_daily(symbol: str, days: int = 90) -> list[dict]:
    """Fetch daily K-line for a stock via AKShare."""
    import akshare as ak

    end = datetime.now()
    start = end - timedelta(days=days)
    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=symbol,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
    except Exception:
        try:
            df = ak.stock_zh_a_daily(
                symbol=symbol,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
        except Exception as e:
            logger.error("Failed to fetch stock data: %s", e)
            return []

    if df is None or df.empty:
        return []

    records = []
    for _, row in df.iterrows():
        records.append({
            "date": str(row.iloc[0]),
            "open": float(row.iloc[1]),
            "close": float(row.iloc[2] if len(row) > 2 else row.iloc[1]),
            "high": float(row.iloc[3] if len(row) > 3 else row.iloc[2]),
            "low": float(row.iloc[4] if len(row) > 4 else row.iloc[3]),
            "volume": float(row.iloc[5]) if len(row) > 5 else 0,
        })
    return records


def _search_stock_code(name: str) -> Optional[str]:
    """Search stock code by name via AKShare."""
    import akshare as ak

    try:
        df = ak.stock_zh_a_spot()
    except Exception:
        try:
            df = ak.stock_zh_a_spot_em()
        except Exception:
            return None

    if "名称" in df.columns:
        match = df[df["名称"].str.contains(name, na=False)]
        if not match.empty:
            code = str(match.iloc[0]["代码"])
            return normalize_stock_code(code)
    elif "code" in df.columns:
        match = df[df["name"].str.contains(name, na=False)]
        if not match.empty:
            raw = str(match.iloc[0]["code"])
            return normalize_stock_code(raw)
    return None


def _get_realtime_price(symbol: str) -> dict:
    """Get realtime stock/ETF quote from AKShare."""
    import akshare as ak

    # Try A-share spot table first (code may have sh/sz prefix, strip it)
    try:
        import math
        df = ak.stock_zh_a_spot()
        plain_code = symbol.replace("sh", "").replace("sz", "")
        row = df[df["代码"] == plain_code]
        if not row.empty:
            r = row.iloc[0]
            price = float(r["最新价"])
            if math.isnan(price) or price == 0:
                open_p = float(r["今开"])
                pre_c = float(r["昨收"])
                price = open_p if (not math.isnan(open_p) and open_p > 0) else (pre_c if not math.isnan(pre_c) else 0)
            change_pct = float(r["涨跌幅"])
            if math.isnan(change_pct): change_pct = 0
            return {
                "price": price,
                "change_pct": change_pct,
                "high": float(r["最高"]),
                "low": float(r["最低"]),
                "open": float(r["今开"]),
                "pre_close": float(r["昨收"]),
                "volume": float(r.get("成交量", 0)),
                "name": str(r["名称"]),
            }
    except Exception:
        pass

    # Try ETF spot table for ETF codes
    try:
        import math
        df = ak.fund_etf_spot_em()
        if "代码" in df.columns:
            row = df[df["代码"] == symbol.replace("sh", "").replace("sz", "")]
        elif "symbol" in df.columns:
            row = df[df["symbol"] == symbol]
        else:
            row = df.iloc[:0]  # empty
        if not row.empty:
            import math
            r = row.iloc[0]
            name_col = "名称" if "名称" in df.columns else "name"
            price = float(r.get("最新价", r.get("price", 0)))
            # 盘前ETF最新价可能为nan, 用今开或昨收代替
            if math.isnan(price) or price == 0:
                open_price = float(r.get("今开", r.get("open", 0)))
                pre_close = float(r.get("昨收", r.get("pre_close", 0)))
                if not math.isnan(open_price) and open_price > 0:
                    price = open_price
                elif not math.isnan(pre_close) and pre_close > 0:
                    price = pre_close
                else:
                    price = 0
            change_pct = float(r.get("涨跌幅", r.get("change_pct", 0)))
            if math.isnan(change_pct):
                change_pct = 0
            high = float(r.get("最高", r.get("high", 0)))
            if math.isnan(high): high = 0
            low = float(r.get("最低", r.get("low", 0)))
            if math.isnan(low): low = 0
            return {
                "price": price,
                "change_pct": change_pct,
                "high": high,
                "low": low,
                "name": str(r.get(name_col, symbol)),
            }
    except Exception:
        pass

    return {}


# ─────────────────────────────────────────────
# Technical indicators
# ─────────────────────────────────────────────


def _calc_ma(records: list[dict], period: int) -> Optional[float]:
    if len(records) < period:
        return None
    closes = [r["close"] for r in records[-period:]]
    return sum(closes) / period


def _find_support_resistance(records: list[dict]) -> dict:
    """Find key support and resistance levels."""
    closes = [r["close"] for r in records]
    highs = [r["high"] for r in records]
    lows = [r["low"] for r in records]
    if not closes:
        return {"support": None, "resistance": None}

    recent = records[-20:] if len(records) >= 20 else records
    peak_candidates = []
    for i in range(1, len(recent) - 1):
        if recent[i]["high"] > recent[i - 1]["high"] and recent[i]["high"] > recent[i + 1]["high"]:
            peak_candidates.append(recent[i]["high"])
    resistance = max(peak_candidates) if peak_candidates else max(highs)

    valley_candidates = []
    for i in range(1, len(recent) - 1):
        if recent[i]["low"] < recent[i - 1]["low"] and recent[i]["low"] < recent[i + 1]["low"]:
            valley_candidates.append(recent[i]["low"])
    support = min(valley_candidates) if valley_candidates else min(lows)

    return {"support": round(support, 2), "resistance": round(resistance, 2)}


# ─────────────────────────────────────────────
# AI Analysis
# ─────────────────────────────────────────────

STOCK_PROMPT = (
    "你是一位专业的股票技术分析师。请分析以下股票数据，并返回 JSON 格式的分析报告。\n\n"
    "股票名称: {name}\n"
    "股票代码: {code}\n\n"
    "近期行情数据:\n{data}\n\n"
    "技术指标:\n{indicators}\n\n"
    "请分析并返回以下 JSON 结构（只返回 JSON，不要 markdown）:\n"
    '{{\n'
    '    "trend": "up / down / sideways / volatile",\n'
    '    "trend_strength": "strong / moderate / weak",\n'
    '    "support_level": 数值,\n'
    '    "resistance_level": 数值,\n'
    '    "ma_analysis": "均线形态分析（中文）",\n'
    '    "volume_analysis": "量能分析（中文）",\n'
    '    "technical_summary": "技术面综合判断（中文，50-100字）",\n'
    '    "suggestion": "buy / sell / hold / wait",\n'
    '    "suggestion_reason": "建议理由（中文，50-100字）",\n'
    '    "entry_point": "建议买入区间",\n'
    '    "stop_loss": "建议止损位",\n'
    '    "target_price": "目标价位",\n'
    '    "risk_level": "high / medium / low",\n'
    '    "current_action": {{\n'
    '        "should_act": true/false,\n'
    '        "action": "买入 / 卖出 / 持有 / 观望",\n'
    '        "reason": "当前操作理由（中文，50-100字）",\n'
    '        "urgency": "urgent / today / this_week / no_need"\n'
    '    }}\n'
    '}}'
)

STOCK_PROMPT_EVENING = (
    "你是一位专业的股票复盘分析师。请分析以下股票今日表现，给出今日总结和明日操作策略。\n\n"
    "股票名称: {name}\n"
    "股票代码: {code}\n\n"
    "近期行情数据:\n{data}\n\n"
    "今日交易情况: {today_summary}\n\n"
    "技术指标:\n{indicators}\n\n"
    "请分析并返回以下 JSON 结构（只返回 JSON，不要 markdown）:\n"
    '{{\n'
    '    "today_performance": "今日走势总结（中文，30-50字，包含今日涨跌/量能特征）",\n'
    '    "today_high": "今日最高价",\n'
    '    "today_low": "今日最低价",\n'
    '    "today_close": "今日收盘价",\n'
    '    "key_levels": "关键价位分析（中文，30-50字，说明支撑/阻力变化）",\n'
    '    "tomorrow_outlook": "明日走势展望（中文，40-80字）",\n'
    '    "tomorrow_strategy": "明日操作策略（中文，40-80字）",\n'
    '    "suggestion": "buy / sell / hold / wait",\n'
    '    "suggestion_reason": "建议理由（中文，30-50字）",\n'
    '    "entry_point": "若建议买入，给出买入区间",\n'
    '    "stop_loss": "建议止损位",\n'
    '    "target_price": "目标价位",\n'
    '    "risk_level": "high / medium / low",\n'
    '    "tomorrow_action": {{\n'
    '        "should_act": true/false,\n'
    '        "action": "买入 / 卖出 / 持有 / 观望",\n'
    '        "reason": "明日操作理由（中文，30-50字）",\n'
    '        "urgency": "urgent / tomorrow / this_week / no_need"\n'
    '    }}\n'
    '}}'
)


async def _call_deepseek(prompt: str) -> dict:
    """Call DeepSeek API for stock analysis."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=get_deepseek_api_key(),
        base_url=get_deepseek_base_url(),
    )
    try:
        response = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "你是一位专业的股票技术分析师。请基于数据给出客观分析，只返回 JSON。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=1000,
        )
        text = response.choices[0].message.content or "{}"
        return _parse_json_response(text)
    except Exception as e:
        logger.error("AI analysis failed: %s", e)
        return {"error": str(e)}


def _parse_json_response(text: str) -> dict:
    """Parse JSON from LLM response, handling markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {"error": "Failed to parse AI response", "raw": text[:300]}


# ─────────────────────────────────────────────
# Main analysis pipeline
# ─────────────────────────────────────────────


async def analyze_stock(symbol_or_name: str, mode: str = "morning") -> dict:
    """Full stock analysis pipeline: fetch, compute indicators, AI analysis.

    Args:
        symbol_or_name: Stock code or Chinese name.
        mode: "morning" for trend analysis + buy/sell advice,
              "evening" for today's summary + tomorrow's strategy.
    """
    result = {"status": "ok", "name": "", "code": "", "error": "", "mode": mode}

    # 1. Resolve symbol
    symbol = symbol_or_name
    if not symbol_or_name.startswith(("sh", "sz")):
        if symbol_or_name.isdigit():
            symbol = normalize_stock_code(symbol_or_name)
        else:
            found = _search_stock_code(symbol_or_name)
            if found:
                symbol = found
                result["name"] = symbol_or_name
            else:
                result["status"] = "error"
                result["error"] = f"未找到股票: {symbol_or_name}"
                return result
    result["code"] = symbol

    # 2. Realtime quote
    realtime = _get_realtime_price(symbol)
    if realtime:
        result["price"] = realtime.get("price", 0)
        result["change_pct"] = realtime.get("change_pct", 0)
        result["name"] = realtime.get("name", result["name"])
    else:
        result["price"] = 0
        result["change_pct"] = 0

    # 3. Historical data
    records = _fetch_stock_daily(symbol, days=90)
    if not records:
        result["status"] = "error"
        result["error"] = f"无法获取 {symbol} 的行情数据"
        return result
    result["records_count"] = len(records)

    # 4. Technical indicators
    ma5 = _calc_ma(records, 5)
    ma10 = _calc_ma(records, 10)
    ma20 = _calc_ma(records, 20)
    ma60 = _calc_ma(records, 60)
    sr = _find_support_resistance(records)
    indicators = {
        "ma5": round(ma5, 2) if ma5 else None,
        "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "support": sr["support"],
        "resistance": sr["resistance"],
    }
    result["indicators"] = indicators

    # 5. Build data summary for AI
    recent = records[-30:] if len(records) >= 30 else records
    data_lines = [
        f"{r['date']} O:{r['open']:.2f} H:{r['high']:.2f} "
        f"L:{r['low']:.2f} C:{r['close']:.2f} V:{r['volume']:.0f}"
        for r in recent
    ]
    indicator_lines = [
        f"MA5: {ma5:.2f}" if ma5 else "MA5: N/A",
        f"MA10: {ma10:.2f}" if ma10 else "MA10: N/A",
        f"MA20: {ma20:.2f}" if ma20 else "MA20: N/A",
        f"MA60: {ma60:.2f}" if ma60 else "MA60: N/A",
        f"支撑位: {sr['support']}",
        f"阻力位: {sr['resistance']}",
        f"近期最高: {max(r['high'] for r in recent):.2f}",
        f"近期最低: {min(r['low'] for r in recent):.2f}",
    ]

    # 6. AI analysis — choose prompt based on mode
    # Build today's summary for evening mode
    today_summary = ""
    if mode == "evening" and len(records) >= 1:
        last = records[-1]
        if len(records) >= 2:
            prev = records[-2]["close"]
            day_change = (last["close"] - prev) / prev * 100
            today_summary = (
                f"今日开盘价: {last['open']:.2f}, "
                f"最高价: {last['high']:.2f}, "
                f"最低价: {last['low']:.2f}, "
                f"收盘价: {last['close']:.2f}, "
                f"日涨跌幅: {day_change:+.2f}%, "
                f"成交量: {last['volume']:.0f}"
            )
        else:
            today_summary = (
                f"今日开盘价: {last['open']:.2f}, "
                f"最高价: {last['high']:.2f}, "
                f"最低价: {last['low']:.2f}, "
                f"收盘价: {last['close']:.2f}, "
                f"成交量: {last['volume']:.0f}"
            )

    if mode == "evening":
        prompt = STOCK_PROMPT_EVENING.format(
            name=result["name"] or symbol,
            code=symbol,
            data="\n".join(data_lines),
            today_summary=today_summary,
            indicators="\n".join(indicator_lines),
        )
    else:
        prompt = STOCK_PROMPT.format(
            name=result["name"] or symbol,
            code=symbol,
            data="\n".join(data_lines),
            indicators="\n".join(indicator_lines),
        )
    analysis = await _call_deepseek(prompt)
    result["analysis"] = analysis

    # 7. Period change
    if len(records) >= 2:
        first_close = records[0]["close"]
        last_close = records[-1]["close"]
        result["period_change"] = round((last_close - first_close) / first_close * 100, 2)
        result["latest_price"] = records[-1]["close"]

    return result


def format_stock_report(result: dict) -> str:
    """Format analysis result into a readable text report."""
    lines = []
    if result["status"] != "ok":
        return f"查询失败: {result.get('error', '未知错误')}"

    name = result.get("name", result.get("code", ""))
    code = result.get("code", "")
    price = result.get("price", result.get("latest_price", 0))
    change = result.get("change_pct", 0)
    change_str = f"{change:+.2f}%" if change else ""

    lines.append("")
    lines.append(f"  {name} ({code})")
    lines.append(f"  最新价: {price:.2f}  涨跌幅: {change_str}")
    lines.append("")

    # Technical indicators
    ind = result.get("indicators", {})
    if ind.get("ma5") is not None:
        def _fmt_ma(v):
            return f"{v:<8.2f}" if v is not None else "N/A     "
        lines.append(f"  技术指标:")
        lines.append(f"    MA5: {_fmt_ma(ind['ma5'])}  MA10: {_fmt_ma(ind['ma10'])}")
        lines.append(f"    MA20: {_fmt_ma(ind['ma20'])} MA60: {_fmt_ma(ind['ma60'])}")
        lines.append(f"    支撑位: {ind['support']:<8.2f}  阻力位: {ind['resistance']:<8.2f}")
        lines.append("")

    # AI Analysis
    analysis = result.get("analysis", {})
    if analysis and "error" not in analysis:
        trend_map = {
            "up": "上涨", "down": "下跌",
            "sideways": "震荡", "volatile": "剧烈波动",
        }
        strength_map = {"strong": "强", "moderate": "中等", "weak": "弱"}
        trend_display = trend_map.get(analysis.get("trend", ""), analysis.get("trend", "未知"))
        strength_display = strength_map.get(analysis.get("trend_strength", ""), "")

        lines.append(f"  AI 趋势分析:")
        lines.append(f"    趋势: {trend_display} ({strength_display})")
        if analysis.get("ma_analysis"):
            lines.append(f"    均线: {analysis['ma_analysis']}")
        if analysis.get("volume_analysis"):
            lines.append(f"    量能: {analysis['volume_analysis']}")
        if analysis.get("technical_summary"):
            lines.append(f"    综合: {analysis['technical_summary']}")
        lines.append("")

        sug_map = {"buy": "买入", "sell": "卖出", "hold": "持有", "wait": "观望"}
        sug_str = sug_map.get(analysis.get("suggestion", ""), analysis.get("suggestion", ""))
        lines.append(f"  操作建议: {sug_str}")
        if analysis.get("suggestion_reason"):
            lines.append(f"    理由: {analysis['suggestion_reason']}")
        if analysis.get("entry_point"):
            lines.append(f"    买入区间: {analysis['entry_point']}")
        if analysis.get("stop_loss"):
            lines.append(f"    止损位: {analysis['stop_loss']}")
        if analysis.get("target_price"):
            lines.append(f"    目标价: {analysis['target_price']}")
        risk_map = {"high": "高风险", "medium": "中等风险", "low": "低风险"}
        lines.append(f"    风险等级: {risk_map.get(analysis.get('risk_level', ''), '')}")
        lines.append("")

        # Current action
        action = analysis.get("current_action", {})
        if action.get("should_act"):
            urgency_map = {
                "urgent": "紧急", "today": "今日",
                "this_week": "本周", "no_need": "无需操作",
            }
            urgency_str = urgency_map.get(action.get("urgency", ""), "")
            lines.append(f"  >> 当前操作: {urgency_str} {action.get('action', '')}")
            if action.get("reason"):
                lines.append(f"    原因: {action['reason']}")
            lines.append("")

    records = result.get("records_count", 0)
    period_change = result.get("period_change", 0)
    lines.append(f"  近{records}个交易日涨跌: {period_change:+.2f}%")

    return "\n".join(lines)


async def analyze_stock_cli(symbol_or_name: str) -> None:
    """CLI entry point for stock analysis."""
    result = await analyze_stock(symbol_or_name)
    print(format_stock_report(result))
