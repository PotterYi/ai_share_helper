"""
WeChat public account article scraper — fetches article content
from mp.weixin.qq.com URLs and extracts stock recommendations.

Usage:
    async def main():
        result = await fetch_and_save("https://mp.weixin.qq.com/s/...")
        print(result)
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional

import httpx

from ..database import Database
from .stock_extractor import search_stock, extract_sections

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://mp.weixin.qq.com/",
}


def _extract_title(html: str) -> str:
    """Extract article title from WeChat HTML."""
    m = re.search(r'<meta[^>]*property=[\'"]og:title[\'"][^>]*content=[\'"](.*?)[\'"]', html)
    if m:
        return m.group(1)
    m = re.search(r'<title[^>]*>(.*?)</title>', html)
    if m:
        return m.group(1)
    return ""


def _extract_content(html: str) -> str:
    """Extract article body text from WeChat HTML."""
    m = re.search(r'rich_media_content[^>]*>(.*?)</div>', html, re.DOTALL)
    if not m:
        return ""
    raw = m.group(1)
    raw = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL)
    raw = re.sub(r'<style[^>]*>.*?</style>', '', raw, flags=re.DOTALL)
    raw = re.sub(r'<br\s*/?>', '\n', raw)
    raw = re.sub(r'</p>', '\n', raw)
    raw = re.sub(r'</div>', '\n', raw)
    raw = re.sub(r'</section>', '\n', raw)
    text = re.sub(r'<[^>]+>', '', raw)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = text.strip()
    return text


def _extract_publish_time(html: str):
    """Extract publish timestamp from WeChat HTML (var ct = '...')."""
    m = re.search(r'var\s+ct\s*=\s*["\'](\d+)["\']', html)
    if m:
        try:
            ts = int(m.group(1))
            return datetime.fromtimestamp(ts).isoformat()
        except (ValueError, OSError):
            pass
    return None


async def fetch_article(url: str, account: str = "凡尘一灯"):
    """Fetch a WeChat article by URL and extract its content."""
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=_HEADERS)
            if resp.status_code != 200:
                logger.warning("WeChat fetch returned %d for %s", resp.status_code, url)
                return None
            html = resp.text
            if not html:
                logger.warning("Empty response from %s", url)
                return None
            title = _extract_title(html)
            content = _extract_content(html)
            posted_at = _extract_publish_time(html)
            if not content:
                logger.warning("Failed to extract content from %s", url)
                return None
            result = {
                "account": account,
                "title": title or url[-20:],
                "url": url,
                "content": content,
                "posted_at": posted_at or datetime.now().isoformat(),
            }
            logger.info("Fetched: %s (%d chars)", result["title"][:40], len(content))
            return result
    except httpx.TimeoutException:
        logger.error("Timeout fetching %s", url)
        return None
    except Exception as e:
        logger.error("Error fetching %s: %s", url, e)
        return None


def analyze_article_stocks(article_data: dict) -> list:
    """Analyze article content and extract stock references with section context."""
    stocks = []
    seen_codes = set()
    sections = extract_sections(article_data["content"])
    for sec in sections:
        for s in sec.get("stocks", []):
            code = s.get("stock_code", "")
            if code in seen_codes:
                continue
            seen_codes.add(code)
            s["section"] = sec["section"]
            stocks.append(s)
    if not stocks:
        for s in search_stock(article_data["content"]):
            code = s.get("stock_code", "")
            if code in seen_codes:
                continue
            seen_codes.add(code)
            s["section"] = "全文"
            stocks.append(s)
    return stocks


async def fetch_and_save(url: str, account: str = "凡尘一灯") -> dict:
    """Fetch a WeChat article, analyze stock mentions, save to database."""
    db = Database()
    result = {"status": "ok", "article_id": 0, "stocks_found": 0, "title": ""}

    article = await fetch_article(url, account=account)
    if not article:
        db.close()
        return {"status": "error", "message": "Failed to fetch article"}

    # Save (INSERT OR IGNORE handles duplicates)
    article_id = db.save_wechat_article(
        account=article["account"],
        title=article["title"],
        url=article["url"],
        content=article["content"],
        posted_at=article["posted_at"],
    )
    result["article_id"] = article_id
    result["title"] = article["title"]

    stocks = analyze_article_stocks(article)
    for s in stocks:
        db.save_article_stock_ref(
            article_id=article_id,
            stock_code=s["stock_code"],
            stock_name=s.get("stock_name", ""),
            stock_full_name=s.get("stock_full_name", ""),
            section=s.get("section", ""),
            mention_snippet=s.get("mention_snippet", "")[:200],
            mention_type=s.get("mention_type", "mentioned"),
            confidence=s.get("confidence", 0.7),
        )
        # Register in tracking system (creates new or resets 15-day timer)
        if _is_valid_stock(s["stock_code"]):
            fn = s.get("stock_full_name", "") or s.get("stock_name", "") or s["stock_code"]
            was_reset = db.ensure_tracking_stock(
                stock_code=s["stock_code"],
                stock_name=fn,
                source_account=article["account"],
                article_id=article_id,
            )
            if was_reset:
                logger.info("  Tracking reset for %s (15 days from today)", fn)
    result["stocks_found"] = len(stocks)
    result["stocks"] = stocks

    # Mark article as analyzed
    import sqlite3
    conn2 = sqlite3.connect(db.db_path)
    conn2.execute("UPDATE wechat_articles SET is_analyzed = 1 WHERE id = ?", (article_id,))
    conn2.commit()
    conn2.close()

    logger.info("Saved '%s' with %d stock refs", article["title"][:30], len(stocks))
    db.close()
    return result


# ─── WeChat Article Search (WeWeRSS) ──────────────────────────

# WeWeRSS 订阅源映射表（公众号名 → WeWeRSS feed ID）
_WEWERSS_MAP = {
    "凡尘一灯": "MP_WXS_3622404709",
    "涨公主的后花园": "MP_WXS_3191660407",
}


async def search_latest_article(account: str = "凡尘一灯") -> Optional[dict]:
    """Search for the latest article URL.

    Primary: WeWeRSS (self-hosted, stable)
    Fallback: Sogou WeChat search (unstable)
    """
    url = await _search_via_wewerss(account)
    if url:
        return url

    logger.info("WeWeRSS unavailable, trying Sogou fallback for %s", account)
    return await _search_sogou_direct(account)


async def _search_via_wewerss(account: str) -> Optional[dict]:
    """Get latest article via local WeWeRSS instance.

    WeWeRSS runs at http://localhost:4000 and exposes
    Atom feeds at /feeds/{feed_id}.xml
    """
    import httpx
    import re

    feed_id = _WEWERSS_MAP.get(account)
    if not feed_id:
        logger.warning("No WeWeRSS feed ID configured for %s", account)
        return None

    feed_url = f"http://localhost:4000/feeds/{feed_id}.xml"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(feed_url)
            if resp.status_code != 200:
                logger.warning("WeWeRSS returned %d for %s", resp.status_code, feed_url)
                return None

            html = resp.text

            # Parse Atom feed - extract titles and links
            titles = re.findall(r'<title[^>]*><!\[CDATA\[(.*?)\]\]></title>', html)
            links = re.findall(r'<link[^>]*href="(https?://mp\.weixin\.qq\.com/s/[^"]+)"', html)

            if not titles or not links:
                logger.warning("No articles found in WeWeRSS feed for %s", account)
                return None

            latest = {
                "url": links[0],
                "title": titles[0],
            }
            logger.info("Found via WeWeRSS: %s -> %s", titles[0][:40], links[0][:50])
            return latest

    except httpx.ConnectError:
        logger.error("WeWeRSS is not running (http://localhost:4000)")
        return None
    except Exception as e:
        logger.error("WeWeRSS search error: %s", e)
        return None


async def _search_sogou_direct(account: str) -> Optional[dict]:
    """Direct Sogou search via httpx (works sometimes)."""
    import urllib.parse
    import html as html_mod

    search_url = "https://weixin.sogou.com/weixin?type=1&query=" + urllib.parse.quote(account)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://weixin.sogou.com/",
    }

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.get(search_url, headers=headers)
        if resp.status_code != 200:
            return None
        html = resp.text

        # Check if blocked
        if len(html) < 500 or "antispider" in str(resp.url):
            return None

        found = []
        for m in __import__("re").finditer(
            r'<a[^>]*href="(https://mp\.weixin\.qq\.com/s/[^"]+)"', html
        ):
            url = m.group(1)
            url = url.replace("amp;", "")
            url = html_mod.unescape(url)
            if url not in [x["url"] for x in found]:
                found.append({"url": url})

        if not found:
            return None
        logger.info("Found via Sogou direct: %s", found[0]["url"][:50])
        return found[0]


async def check_for_new_article(account: str = "凡尘一灯") -> Optional[dict]:
    """Check if there's a new article from the account via WeWeRSS (or Sogou fallback).

    The caller should track pre-existing article IDs to determine "analyzed" status.
    """
    from ..database import Database

    db = Database()
    latest = await search_latest_article(account)
    if latest:
        articles = db.get_wechat_articles(account=account, limit=5)
        existing_urls = {a["url"] for a in articles}
        db.close()
        if latest["url"] in existing_urls:
            return None
        logger.info("New article: %s", latest["url"][:40])
        return latest

    db.close()
    logger.debug("No new article found for %s", account)
    return None


# ─── Stock Analysis Helpers ──────────────────────────────────────

_TRACK_DAYS = 15

_SKIP_CODES = {"066062", "511003", "0034", "021062", "510002", "510020"}


def _is_valid_stock(code: str) -> bool:
    """Filter out invalid stock codes (license numbers, etc.)."""
    if not code:
        return False
    raw = code.replace("sh", "").replace("sz", "")
    if raw in _SKIP_CODES:
        return False
    if len(raw) != 6:
        return False
    return True


def _load_akshare_spot_data() -> dict:
    """Load A-share spot data. Keys: full code with exchange prefix (e.g. 'sh603269')."""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()
        result = {}
        nc = len(df.columns)
        for _, row in df.iterrows():
            code = str(row.iloc[0])
            # col indices: 0=代码 1=名称 2=最新价 4=涨跌幅 6=最高 7=最低 10/11=成交额/量
            result[code] = {
                "name": str(row.iloc[1]),
                "price": float(row.iloc[2]) if nc > 2 else 0,
                "high": float(row.iloc[6]) if nc > 6 else 0,
                "low": float(row.iloc[7]) if nc > 7 else 0,
                "change_pct": float(row.iloc[4]) if nc > 4 else 0,
                "amount": float(row.iloc[12]) if nc > 12 else 0,  # 成交额 as scale proxy (col[12])
            }
        logger.info("AKShare spot data loaded: %d stocks", len(result))
        return result
    except Exception as e:
        logger.warning("Failed to load AKShare spot data: %s", e)
        return {}


async def _get_stock_snapshot(stock_code: str) -> dict:
    """Get quick price + trend analysis for a stock."""
    from ..stock_analyzer import analyze_stock, _get_realtime_price
    result = {"price": 0, "change_pct": 0, "high": 0, "low": 0, "trend": "", "suggestion": ""}
    try:
        quote = _get_realtime_price(stock_code)
        if quote:
            result["price"] = quote.get("price", 0)
            result["change_pct"] = quote.get("change_pct", 0)
            result["high"] = quote.get("high", 0)
            result["low"] = quote.get("low", 0)

        analysis_result = await analyze_stock(stock_code)
        analysis = analysis_result.get("analysis", {})
        if analysis and "error" not in analysis:
            trend_map = {"up": "涨", "down": "跌", "sideways": "震", "volatile": "波"}
            sug_map = {"buy": "买入", "sell": "卖出", "hold": "持有", "wait": "观望"}
            result["trend"] = trend_map.get(analysis.get("trend", ""), "?")
            result["suggestion"] = sug_map.get(analysis.get("suggestion", ""), "?")
    except Exception:
        pass
    return result


# ─── Daily Tracking Report ───────────────────────────────────────

async def build_tracking_report(account: str = "凡尘一灯", spot_data: dict = None) -> Optional[str]:
    """Build per-account section: 最新文章 + 今日新跟踪股票 only.

    The combined tracking table is built separately by build_combined_tracking_table().

    Args:
        account: WeChat account name.
        spot_data: pre-loaded AKShare data dict (to avoid reloading).

    Returns:
        Markdown string for this account's section.
    """
    from ..database import Database

    if spot_data is None:
        spot_data = _load_akshare_spot_data()

    db = Database()
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append(f"**📢 微信公众号推荐股票跟踪日报**")
    lines.append(f"  {date_str}")
    lines.append("")
    lines.append(f"**公众号：{account}**")
    lines.append("")

    # ── Latest Article + Today's New Stocks ──
    latest = db.get_latest_wechat_article(account)
    if latest:
        title = latest.get("title", "") or "(无标题)"
        lines.append(f"**📄 最新文章：{title[:50]}**")
        lines.append(f"  📅 {latest.get('posted_at', '')[:10]}")
        lines.append("")

        refs = db.get_article_stock_refs(article_id=latest["id"])
        valid = [r for r in refs if _is_valid_stock(r["stock_code"])]

        if valid:
            lines.append("**📌 今日新跟踪股票**")
            lines.append("")

            sections = {}
            for r in valid:
                code = r["stock_code"]
                if code not in spot_data:
                    continue
                sec = r.get("section", "") or "其他"
                sections.setdefault(sec, []).append(r)

            if sections:
                table_header = f"  {'名称':<12s} {'当前价':<8s} {'最高':<8s} {'最低':<8s}"
                for sec, sec_stocks in sections.items():
                    sec_clean = sec.replace(".", "").strip()
                    lines.append(f"  📂 {sec_clean}")
                    lines.append(table_header)
                    lines.append(f"  {'─'*12} {'─'*8} {'─'*8} {'─'*8}")
                    for r in sec_stocks:
                        code = r["stock_code"]
                        fn = r.get("stock_full_name", "") or r.get("stock_name", "") or code[:10]
                        sd = spot_data.get(code, {})
                        cur = sd.get("price", 0)
                        hi = sd.get("high", 0)
                        lo = sd.get("low", 0)
                        lines.append(f"  {fn[:10]:<12s} {cur:<8.2f} {hi:<8.2f} {lo:<8.2f}")
                    lines.append("")
            else:
                lines.append("  (今日文章中的股票暂无行情数据)")
                lines.append("")
        else:
            lines.append("  (今日文章暂未识别到股票)")
            lines.append("")
    else:
        lines.append("  (暂无文章记录)")
        lines.append("")

    db.close()
    return "\n".join(lines)


async def build_combined_tracking_table(accounts: list[str]) -> Optional[str]:
    """Build a single combined tracking table across all accounts.

    Shows top 10 stocks by trading amount (scale proxy), with source account column.

    Args:
        accounts: List of WeChat account names to include.

    Returns:
        Markdown string for the combined tracking table section.
    """
    from ..database import Database

    spot_data = _load_akshare_spot_data()
    db = Database()
    lines = []
    lines.append("**📊 持续跟踪股票（规模前十）**")
    lines.append("")

    all_stocks = {}  # {stock_code: {..}}
    for account in accounts:
        tracked = db.get_active_tracked_stocks(source_account=account, track_days=_TRACK_DAYS)
        for t in tracked:
            if not _is_valid_stock(t["stock_code"]):
                continue
            code = t["stock_code"]
            sd = spot_data.get(code)
            if not sd:
                continue

            if code not in all_stocks:
                all_stocks[code] = {
                    "code": code,
                    "name": t.get("stock_name", "") or code[:10],
                    "track_day": int(t.get("track_day", 0)) + 1,
                    "day1_price": t.get("day1_price"),
                    "price": sd.get("price", 0),
                    "high": sd.get("high", 0),
                    "low": sd.get("low", 0),
                    "change_pct": sd.get("change_pct", 0),
                    "amount": sd.get("amount", 0),
                    "source": account,
                }
            else:
                # Already exists — keep the one with larger amount
                existing_amt = all_stocks[code].get("amount", 0)
                new_amt = sd.get("amount", 0)
                if new_amt > existing_amt:
                    all_stocks[code] = {
                        "code": code,
                        "name": t.get("stock_name", "") or code[:10],
                        "track_day": int(t.get("track_day", 0)) + 1,
                        "day1_price": t.get("day1_price"),
                        "price": sd.get("price", 0),
                        "high": sd.get("high", 0),
                        "low": sd.get("low", 0),
                        "change_pct": sd.get("change_pct", 0),
                        "amount": sd.get("amount", 0),
                        "source": account,
                    }

    if not all_stocks:
        lines.append("  (暂无跟踪股票)")
    else:
        # Sort by amount descending, take top 10
        sorted_stocks = sorted(all_stocks.values(), key=lambda x: x.get("amount", 0), reverse=True)
        top10 = sorted_stocks[:10]

        header = f"  {'名称':<10s} {'当前价':<8s} {'最高':<8s} {'最低':<8s} {'Day1价':<8s} {'对比':<8s} {'来源':<12s}"
        lines.append(header)
        lines.append(f"  {'─'*10} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*12}")

        for s in top10:
            cur = s["price"]
            hi = s["high"]
            lo = s["low"]
            d1p = s["day1_price"]
            day = s["track_day"]

            if d1p and d1p > 0 and cur > 0:
                since = f"{(cur-d1p)/d1p*100:+.1f}%"
            else:
                since = "-"
                if not d1p:
                    db.update_tracking_daily_data(
                        stock_code=s["code"], source_account=s["source"],
                        day1_price=cur, day1_change_pct=s["change_pct"],
                    )
                    d1p = cur
                    since = "+0.0%"

            d1s = f"{d1p:.2f}" if d1p else "-"
            source_display = s["source"][:10]
            lines.append(
                f"  {s['name'][:8]:<10s} {cur:<8.2f} {hi:<8.2f} {lo:<8.2f} {d1s:<8s} {since:<8s} {source_display:<12s}"
            )

        lines.append("")
        lines.append(f"  🔹 共 {len(all_stocks)} 只跟踪中，展示成交额最大的10只")
        lines.append(f"  🔹 重复推荐的股票自动重置15天跟踪期")

    lines.append("")
    lines.append("  ---")
    lines.append("  由 AI News Radar 自动跟踪")

    db.close()
    return "\n".join(lines)


async def run_wechat_daily(dry_run: bool = False, account: str = "凡尘一灯") -> dict:
    """Run the full WeChat daily check pipeline for a specific account.

    1. Check for new article
    2. Build tracking report
    3. Send to group chat

    Args:
        dry_run: If True, print report without sending.
        account: WeChat public account name (supports multiple accounts).
    """
    from ..feishu_client import FeishuClient

    result = {
        "new_article": False,
        "new_stocks": 0,
        "tracking_count": 0,
        "report_sent": False,
    }

    # Step 1: Check for new article
    new_article = await check_for_new_article(account=account)
    if new_article:
        save_result = await fetch_and_save(new_article["url"], account=account)
        result["new_article"] = True
        result["new_stocks"] = save_result.get("stocks_found", 0)
        result["article_title"] = save_result.get("title", "")

    # Step 2+3: Build and send report
    report = await build_tracking_report(account=account)
    if not report:
        return result

    db = Database()
    tracked = db.get_active_tracked_stocks(source_account=account, track_days=_TRACK_DAYS)
    result["tracking_count"] = len(tracked)
    db.close()

    if dry_run:
        print()
        print("=" * 60)
        print("  [DRY RUN] WeChat Daily Report")
        print("=" * 60)
        print(report)
        result["report_sent"] = True
        return result

    # Send to group
    db = Database()
    groups = db.get_enabled_group_chats(module="default")
    db.close()

    if groups:
        fc = FeishuClient()
        if fc.is_configured:
            for group in groups:
                ok = await fc.send_group_message(
                    chat_id=group["chat_id"],
                    report=report,
                    title="📢 微信公众号推荐股票跟踪日报",
                    template="green",
                )
                if ok:
                    result["report_sent"] = True

    return result


def format_stock_summary(stocks: list) -> str:
    """Format stock recommendations for CLI display."""
    if not stocks:
        return "  本篇文章未识别到股票提及"
    lines = []
    current_section = ""
    for s in stocks:
        sec = s.get("section", "")
        if sec and sec != current_section:
            current_section = sec
            lines.append(f"  \U0001f4c2 {sec}")
        code = s["stock_code"]
        full_name = s.get("stock_full_name", "") or ""
        name = s.get("stock_name", "") or ""
        snippet = s.get("mention_snippet", "") or ""
        label = full_name if full_name else name
        display = f"    \U0001f4c8 {label} ({code})"
        if snippet:
            display += f"  — {snippet[:60]}"
        lines.append(display)
    return "\n".join(lines)
