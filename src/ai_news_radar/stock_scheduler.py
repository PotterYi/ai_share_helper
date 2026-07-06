"""
Stock daily report scheduler — uses APScheduler for reliable cron-based scheduling.

Replaces the old asyncio.sleep() busy-loop with proper cron triggers.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore

from .stock_notifier import check_and_notify_all_users
from .scrapers.wechat_article import run_wechat_daily

logger = logging.getLogger(__name__)


async def _compute_sqsm_scores(stock_codes: list, spot_data: dict) -> dict:
    """Compute 十全十美 scores for a list of stocks with concurrency control."""
    from .sqsm_indicator import ShiQuanShiMei
    from .stock_analyzer import _fetch_stock_daily
    import numpy as np

    sem = asyncio.Semaphore(3)
    result = {}

    async def _calc_one(code: str):
        async with sem:
            # Check spot_data first
            sd = spot_data.get(code)
            if not sd:
                return
            try:
                records = _fetch_stock_daily(code, days=500)
                if len(records) < 60:
                    return
                c = np.array([r["close"] for r in records])
                h = np.array([r["high"] for r in records])
                l = np.array([r["low"] for r in records])
                v = np.array([r["volume"] for r in records])
                sqsm = ShiQuanShiMei()
                res = sqsm.calculate(c, h, l, v)
                if "error" not in res:
                    result[code] = res
            except Exception:
                pass

    tasks = [_calc_one(code) for code in stock_codes]
    await asyncio.gather(*tasks)
    return result


class StockScheduler:
    """APScheduler-based scheduler for stock daily report push.

    Schedules two jobs per day:
      - morning (default 07:00): trend analysis + buy/sell advice
      - evening (default 19:00): today's summary + tomorrow's strategy
    """

    def __init__(
        self,
        morning_time: str = "07:00",
        evening_time: str = "19:00",
        dry_run: bool = False,
    ):
        self.morning_time = morning_time
        self.evening_time = evening_time
        self.dry_run = dry_run
        self._scheduler: Optional[AsyncIOScheduler] = None

        # Parse times
        m_h, m_m = map(int, morning_time.split(":"))
        e_h, e_m = map(int, evening_time.split(":"))
        self._morning_hour = m_h
        self._morning_minute = m_m
        self._evening_hour = e_h
        self._evening_minute = e_m
        self.wechat_time = "07:00"  # WeChat article check always at 07:00

    # 公众号列表 — 可自由扩展
    WECHAT_ACCOUNTS = ["凡尘一灯", "涨公主的后花园"]

    async def _wechat_morning_job(self):
        """Execute WeChat daily check, then send beautiful interactive card.

        工作流:
          1. 07:00 WeWeRSS 检查 → 新文章入库(is_analyzed=0), 提取股票引用
          2. 卡片构建 → 根据 is_analyzed 判断:
               is_analyzed=0 + 有有效股票 → "new" → 发卡 → 标记 is_analyzed=1
               is_analyzed=0 + 无有效股票 → "no_stocks" → 发卡 → 标记 is_analyzed=1
               is_analyzed=1           → "analyzed" (卡片已发过)
          3. 持续跟踪数据(无论文章状态, 每天更新)
        """
        from ..scrapers.wechat_article import (
            check_for_new_article, fetch_and_save, _load_akshare_spot_data, _is_valid_stock,
        )
        from ..database import Database
        from ..feishu_client import FeishuClient
        from datetime import datetime as dt

        label = "公众号股票推荐跟踪"
        logger.info("[APScheduler] Starting %s for %d accounts...", label, len(self.WECHAT_ACCOUNTS))
        print(f"\n[cyan]{dt.now().strftime('%H:%M')}  开始{label}[/cyan]")

        # Step 1: WeWeRSS 检查 → 新文章入库 (is_analyzed=0)
        newly_fetched_ids = set()
        for account in self.WECHAT_ACCOUNTS:
            print(f"  [dim]📱 {account}...[/dim]")
            try:
                new_article = await check_for_new_article(account=account)
                if new_article:
                    if new_article.get("already_saved"):
                        print(f"  [dim]  {account}: 已有今日文章 \"{new_article.get('title', '')[:30]}\"[/dim]")
                    else:
                        save_result = await fetch_and_save(new_article["url"], account=account)
                        newly_fetched_ids.add(save_result.get("article_id", 0))
                        print(f"  [green]  {account}: 新文章 \"{save_result.get('title', '')[:30]}\"[/green]")
                        print(f"  [green]  识别到 {save_result.get('stocks_found', 0)} 只股票[/green]")
                else:
                    print(f"  [dim]  {account}: 无新文章[/dim]")
            except Exception as e:
                logger.error("[APScheduler] %s check failed for %s: %s", label, account, e)
                print(f"  [red]  {account}: 检查出错 — {e}[/red]")

        # Step 2: 构建卡片
        print(f"  [cyan]构建卡片数据...[/cyan]")
        db = Database()
        spot_data = _load_akshare_spot_data()

        account_sections = []  # [(account, status, title, sections)]
        tracking_dict = {}
        articles_to_mark_analyzed = []  # 发卡后标记已分析的article_id列表

        for account in self.WECHAT_ACCOUNTS:
            latest = db.get_latest_wechat_article(account)
            if not latest:
                continue
            title = latest.get("title", "")
            aid = latest["id"]
            is_analyzed = latest.get("is_analyzed", 0)

            # 判断状态
            if is_analyzed == 0:
                # 未分析的新文章 → 检查是否有有效股票
                refs = db.get_article_stock_refs(article_id=aid)
                sections = {}
                for r in refs:
                    if not _is_valid_stock(r["stock_code"]):
                        continue
                    sd = spot_data.get(r["stock_code"])
                    if not sd:
                        continue
                    sec = r.get("section", "") or "其他"
                    # 用 spot_data 中的真实名称（从AKShare获取），不用文章提取的可能乱码的名称
                    real_name = sd.get("name", "")
                    sections.setdefault(sec, []).append({
                        "code": r["stock_code"],
                        "name": real_name or r.get("stock_name", "") or r["stock_code"][:10],
                        "price": sd.get("price", 0),
                        "high": sd.get("high", 0),
                        "low": sd.get("low", 0),
                    })
                if sections:
                    status = "new"
                    articles_to_mark_analyzed.append(aid)
                else:
                    status = "no_stocks"
                    articles_to_mark_analyzed.append(aid)
                account_sections.append((account, status, title, sections))
            else:
                # 已分析（卡片已发过）
                account_sections.append((account, "analyzed", title, {}))

            # 持续跟踪数据（无论文章是否已分析, 每天更新）
            for t in db.get_active_tracked_stocks(source_account=account):
                if not _is_valid_stock(t["stock_code"]):
                    continue
                sd = spot_data.get(t["stock_code"])
                if not sd:
                    continue
                c = t["stock_code"]
                d1p = t.get("day1_price")
                cur = sd.get("price", 0)

                if not d1p and cur > 0:
                    db.update_tracking_daily_data(
                        stock_code=c, source_account=account,
                        day1_price=cur, day1_change_pct=sd.get("change_pct", 0),
                    )
                    d1p = cur

                if c not in tracking_dict or sd.get("amount", 0) > tracking_dict[c].get("amount", 0):
                    track_day = int(t.get("track_day", 0)) + 1
                    tracking_dict[c] = {
                        "code": c,
                        "name": sd.get("name", "") or t.get("stock_name", "") or c[:10],
                        "price": cur,
                        "day1_price": d1p,
                        "amount": sd.get("amount", 0),
                        "source": account,
                        "track_day": track_day,
                    }

        db.close()

        # Compute 十全十美 scores for tracked stocks
        if tracking_dict:
            print(f"  [cyan]计算十全十美指标 ({len(tracking_dict)}只)...[/cyan]")
            sqsm_scores = await _compute_sqsm_scores(list(tracking_dict.keys()), spot_data)
            for code, s_data in tracking_dict.items():
                sqsm = sqsm_scores.get(code, {})
                s_data["sqsm_score"] = sqsm.get("bull_ratio", "-/-")
                s_data["sqsm_resonance"] = sqsm.get("total", 0) > 8  # 9分及以上共振

        # Sort tracking by amount, take top 10
        sorted_tracking = sorted(tracking_dict.values(), key=lambda x: x.get("amount", 0), reverse=True)[:10]
        total_tracking = len(tracking_dict)

        if not account_sections and not sorted_tracking:
            print(f"  [red]无数据可生成报告[/red]")
            return

        # Step 3: Send beautiful card
        card_sent = False
        if self.dry_run:
            print(f"\n  [dim]DRY RUN - 账户={len(account_sections)}, 跟踪={total_tracking}只[/dim]")
            new_count = sum(1 for _, s, _, _ in account_sections if s == "new")
            no_stock_count = sum(1 for _, s, _, _ in account_sections if s == "no_stocks")
            print(f"  新文章={new_count}, 无股票={no_stock_count}, 已分析={len(account_sections)-new_count-no_stock_count}")
            print(f"  持续跟踪表格: {len(sorted_tracking)} 只")
            card_sent = True  # dry-run 也视为已处理
        else:
            fc = FeishuClient()
            if fc.is_configured:
                ok = await fc.send_tracking_card(
                    chat_id="oc_8792267760e09f7c142bb0157bcf22f0",
                    new_stocks_data=account_sections,
                    tracking_data=sorted_tracking,
                    total_tracking=total_tracking,
                )
                if ok:
                    print(f"  [green]✅ 美化日报已推送到群[/green]")
                    card_sent = True
                else:
                    print(f"  [red]❌ 推送失败[/red]")
            else:
                print(f"  [red]❌ 飞书未配置[/red]")

        # 卡片发送成功后, 标记未分析的文章为已分析(is_analyzed=1)
        if card_sent and articles_to_mark_analyzed:
            from ..database import Database
            db_mark = Database()
            for aid in articles_to_mark_analyzed:
                db_mark.mark_wechat_analyzed(aid)
                logger.info("[APScheduler] Marked article %d as analyzed", aid)
            db_mark.close()
            print(f"  [dim]已标记 {len(articles_to_mark_analyzed)} 篇文章为已分析[/dim]")

        logger.info("[APScheduler] %s done: %d accounts, %d tracking", label, len(self.WECHAT_ACCOUNTS), total_tracking)

    def _get_job_list(self) -> list[str]:
        """Get human-readable job list for the banner."""
        jobs = []
        jobs.append(f"Morning stock report @ {self.morning_time}")
        jobs.append(f"Evening stock report @ {self.evening_time}")
        if "wechat" in self.wechat_time:
            jobs.append(f"WeChat tracking @ {self.wechat_time}")
        else:
            jobs.append(f"WeChat tracking @ {self.wechat_time}")
        return jobs

    async def _morning_job(self):
        """Execute morning stock report push."""
        mode_label = "早间研判"
        logger.info("[APScheduler] Starting %s (trend analysis + advice)...", mode_label)
        console_msg = (
            f"\n[cyan]{datetime.now().strftime('%H:%M')}  "
            f"开始{mode_label} (定时任务 @ {self.morning_time})[/cyan]"
        )
        print(console_msg)
        result = await check_and_notify_all_users(dry_run=self.dry_run, mode="morning")
        logger.info(
            "[APScheduler] %s done: notified=%d errors=%d",
            mode_label, result.get("notified", 0), len(result.get("errors", [])),
        )

    async def _evening_job(self):
        """Execute evening stock report push."""
        mode_label = "收盘复盘"
        logger.info("[APScheduler] Starting %s (summary + strategy)...", mode_label)
        console_msg = (
            f"\n[cyan]{datetime.now().strftime('%H:%M')}  "
            f"开始{mode_label} (定时任务 @ {self.evening_time})[/cyan]"
        )
        print(console_msg)
        result = await check_and_notify_all_users(dry_run=self.dry_run, mode="evening")
        logger.info(
            "[APScheduler] %s done: notified=%d errors=%d",
            mode_label, result.get("notified", 0), len(result.get("errors", [])),
        )

    async def _screener_evening_job(self):
        from ..stock_screener import run_daily_screener, format_report
        from ..feishu_client import FeishuClient
        label = "十全十美盘后筛选"
        logger.info("[APScheduler] Starting %s...", label)
        print(f"\n[cyan]{datetime.now().strftime('%H:%M')}  开始{label}[/cyan]")
        try:
            top10 = await run_daily_screener(mode="evening")
            if self.dry_run:
                print(format_report(top10, mode="evening"))
                return
            fc = FeishuClient()
            if fc.is_configured:
                ok = await fc.send_screener_card(chat_id="oc_8792267760e09f7c142bb0157bcf22f0", screener_data=top10)
                print(f"  {'[green]✅ 盘后推荐已推送[/green]' if ok else '[red]❌ 推送失败[/red]'}")
            else:
                print("  [red]❌ 飞书未配置[/red]")
            logger.info("[APScheduler] %s done: %d stocks", label, len(top10))
        except Exception as e:
            logger.error("[APScheduler] %s failed: %s", label, e)
            import traceback
            print(f"  [red]出错: {e}[/red]")
            traceback.print_exc()

    def start(self):
        """Start the APScheduler with morning and evening cron triggers."""
        logger.info(
            "StockScheduler starting: morning=%s evening=%s dry_run=%s",
            self.morning_time, self.evening_time, self.dry_run,
        )

        jobstores = {"default": MemoryJobStore()}
        self._scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="Asia/Shanghai")

        # Add morning job
        self._scheduler.add_job(
            self._morning_job,
            trigger=CronTrigger(
                hour=self._morning_hour,
                minute=self._morning_minute,
                timezone="Asia/Shanghai",
            ),
            id="stock_morning",
            name=f"Morning stock report @ {self.morning_time}",
            replace_existing=True,
            misfire_grace_time=300,  # 5min grace window
        )

        # Add evening job
        self._scheduler.add_job(
            self._evening_job,
            trigger=CronTrigger(
                hour=self._evening_hour,
                minute=self._evening_minute,
                timezone="Asia/Shanghai",
            ),
            id="stock_evening",
            name=f"Evening stock report @ {self.evening_time}",
            replace_existing=True,
            misfire_grace_time=300,
        )

        # Add 十全十美 stock screener evening job (19:30) — 仅盘后推送，早间不推送
        self._scheduler.add_job(
            self._screener_evening_job,
            trigger=CronTrigger(
                hour=19, minute=30, timezone="Asia/Shanghai",
            ),
            id="sqsm_screener_evening",
            name="十全十美 evening screener @ 19:30",
            replace_existing=True,
            misfire_grace_time=600,
        )

        # Add WeChat tracking job (always at 07:00, same as morning but runs first)
        self._scheduler.add_job(
            self._wechat_morning_job,
            trigger=CronTrigger(
                hour=7,
                minute=0,
                timezone="Asia/Shanghai",
            ),
            id="wechat_tracking",
            name=f"WeChat tracking @ 07:00",
            replace_existing=True,
            misfire_grace_time=600,
        )

        self._scheduler.start()

        # Print job info
        jobs = self._scheduler.get_jobs()
        print()
        print("  ╔══════════════════════════════════════╗")
        print("  ║  ⏰ 调度任务列表 (APScheduler)        ║")
        for j in sorted(jobs, key=lambda x: x.name):
            print(f"  ║  {j.name:35s} ║")
        print(f"  ║  {'dry_run=True' if self.dry_run else '实时推送':35s} ║")
        print("  ║  🔄 等待触发中...                   ║")
        print("  ║  按 Ctrl+C 停止                       ║")
        print("  ╚══════════════════════════════════════╝")
        print()

    def stop(self):
        """Gracefully shut down the scheduler."""
        if self._scheduler and self._scheduler.running:
            logger.info("StockScheduler shutting down...")
            self._scheduler.shutdown(wait=True)
            logger.info("StockScheduler stopped.")

    @property
    def running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running
