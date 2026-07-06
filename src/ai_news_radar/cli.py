"""
CLI entry point for AI News Radar.
"""

import asyncio
import logging
import sys
from datetime import datetime, timedelta
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown

from . import __version__
from .utils.helpers import setup_logging, normalize_stock_code

app = typer.Typer(
    name="ai-news",
    help="AI News Radar - AI-powered news aggregation and analysis",
    add_completion=False,
)

console = Console()
logger = setup_logging()


def _print_banner():
    """Print the ASCII art banner."""
    banner = r"""
    ╔══════════════════════════════════════════╗
    ║        AI News Radar v{version}            ║
    ║   AI-powered news aggregation & analysis ║
    ╚══════════════════════════════════════════╝
    """.format(version=__version__)
    console.print(banner, style="bold cyan")


@app.command()
def run(
    backend: str = typer.Option("auto", help="AI backend: deepseek, openai, anthropic, or auto"),
    analyze: bool = typer.Option(True, help="Run AI analysis on articles"),
    notify: bool = typer.Option(False, help="Send notifications after report"),
    no_report: bool = typer.Option(False, help="Skip report generation"),
):
    """📰 AI News: scrape news + trending repos + analyze + report."""
    _print_banner()

    # Lazy import for old pipeline (keeps stock commands independent)
    from .config import is_configured
    from .models import SourceType

    if analyze and not is_configured():
        console.print(
            "[yellow]Warning: No API key configured. AI analysis will be disabled.[/yellow]"
        )
        console.print(
            "[dim]Set DEEPSEEK_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY in .env to enable analysis.[/dim]"
        )
        analyze = False

    # News mode: exclude fast-growing projects (they have their own command)
    from .engine import Engine
    news_sources = [
        SourceType.ANTHROPIC.value,
        SourceType.GITHUB_TRENDING.value,
        SourceType.HACKER_NEWS.value,
        SourceType.REDDIT_ML.value,
    ]
    engine = Engine(backend=backend, analyze=analyze, notify=notify, source_filter=news_sources)

    async def _run():
        if no_report:
            count = await engine.scrape_only()
            console.print(f"[green]Scraped {count} articles.[/green]")
        else:
            result = await engine.run_full_pipeline()
            _print_result(result)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    finally:
        engine.close()


@app.command()
def projects(
    backend: str = typer.Option("auto", help="AI backend: deepseek, openai, anthropic, or auto"),
    analyze: bool = typer.Option(True, help="Run AI analysis on articles"),
    notify: bool = typer.Option(False, help="Send notifications"),
):
    """🚀 GitHub Projects: find fast-growing AI/ML repositories."""
    _print_banner()
    console.print("[cyan]🚀 GitHub Fast-Growing Projects mode[/cyan]\n")

    # Lazy import for old pipeline
    from .config import is_configured
    from .models import SourceType
    from .engine import Engine

    if analyze and not is_configured():
        console.print(
            "[yellow]Warning: No API key configured. AI analysis will be disabled.[/yellow]"
        )
        analyze = False

    engine = Engine(
        backend=backend,
        analyze=analyze,
        notify=notify,
        source_filter=[SourceType.GITHUB_FAST_GROWING.value],
    )

    async def _run():
        result = await engine.run_full_pipeline()
        _print_result(result)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    finally:
        engine.close()


@app.command()
def stock(
    symbol: str = typer.Argument(..., help="股票代码或名称，如 600589 或 大位科技"),
    analyze: bool = typer.Option(True, help="启用 AI 趋势分析"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="用户标识，显示持仓盈亏"),
):
    """📈 股票分析: 查询股票行情 + AI 趋势分析 + 操作建议"""
    _print_banner()
    console.print(f"[cyan]📈 股票分析: {symbol}[/cyan]\n")

    from .stock_analyzer import analyze_stock, format_stock_report, _get_realtime_price

    async def _run():
        result = await analyze_stock(symbol)

        # Check if user has this stock in portfolio
        if user:
            from .database import Database
            db = Database()
            user_obj = db.get_or_create_user(user, user)
            stocks = db.get_user_stocks(user_obj["id"])
            code = result.get("code", "")
            for s in stocks:
                if s["stock_code"] == code:
                    buy_price = s["buy_price"]
                    qty = s["quantity"]
                    current = result.get("price", 0)
                    if buy_price and current:
                        change = (current - buy_price) / buy_price * 100
                        pnl = (current - buy_price) * (qty or 1)
                        console.print(
                            f"[bold]  持仓:[/bold] 买入价 {buy_price:.2f}  "
                            f"数量 {qty or '-'}  "
                            f"{'[green]' if change >= 0 else '[red]'}"
                            f"盈亏 {change:+.1f}% (¥{pnl:+.0f})"
                            f"[/{'green' if change >= 0 else 'red'}]"
                        )
                        console.print()
                    break
            db.close()

        report = format_stock_report(result)
        console.print(report)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")


# ═══════════════════════════════════════════════
#  Portfolio / Watchlist Management
# ═══════════════════════════════════════════════

@app.command()
def watch(
    symbol: str = typer.Argument(..., help="股票代码或名称，如 600589"),
    price: Optional[float] = typer.Option(None, "--price", "-p", help="买入价格"),
    quantity: Optional[int] = typer.Option(None, "--quantity", "-q", help="持仓数量"),
    notes: Optional[str] = typer.Option(None, "--notes", "-n", help="备注"),
    webhook: Optional[str] = typer.Option(None, "--webhook", "-w", help="飞书通知URL（用于定时推送）"),
    daily: bool = typer.Option(False, "--daily", "-d", help="同时开启该股票的日报私发功能"),
    user: str = typer.Option("default", "--user", "-u", help="用户标识（Feishu ID）"),
):
    """👁️ 添加股票到自选/持仓列表"""
    from .database import Database

    db = Database()
    user_obj = db.get_or_create_user(user, user)
    if webhook:
        db.update_user_webhook(user, webhook)
        console.print(f"[dim]  已设置通知 Webhook[/dim]")
    stock_name = symbol

    # Try to resolve stock name
    if not symbol.startswith(("sh", "sz")):
        try:
            from .stock_analyzer import _search_stock_code
            code = _search_stock_code(symbol)
            if code:
                stock_name = symbol
                symbol = code
            elif symbol.isdigit():
                symbol = normalize_stock_code(symbol)
        except Exception:
            if symbol.isdigit():
                symbol = normalize_stock_code(symbol)

    added = db.add_user_stock(
        user_id=user_obj["id"],
        stock_code=symbol,
        stock_name=stock_name,
        buy_price=price,
        quantity=quantity or 0,
        notes=notes or "",
    )

    if added:
        console.print(f"[green]✅ 已将 {stock_name} ({symbol}) 添加到自选列表[/green]")
        if price:
            console.print(f"   [dim]买入价: {price:.2f}  数量: {quantity or '-'}[/dim]")

        # Auto-enable daily_notify if --daily flag
        if daily:
            db.set_stock_daily_notify(user_obj["id"], symbol, True)
            console.print("   [green]📩 已开启日报私发功能[/green]")
    else:
        console.print(f"[yellow]⚠️  {stock_name} ({symbol}) 已在自选列表中[/yellow]")
        if daily:
            db.set_stock_daily_notify(user_obj["id"], symbol, True)
            console.print("   [green]📩 已开启日报私发功能[/green]")

    db.close()


@app.command()
def unwatch(
    symbol: str = typer.Argument(..., help="股票代码"),
    user: str = typer.Option("default", "--user", "-u", help="用户标识（Feishu ID）"),
):
    """🗑️ 从自选列表中移除股票"""
    from .database import Database

    db = Database()
    user_obj = db.get_or_create_user(user, user)

    symbol = normalize_stock_code(symbol)

    removed = db.remove_user_stock(user_obj["id"], symbol)
    if removed:
        console.print(f"[green]✅ 已移除 {symbol}[/green]")
    else:
        console.print(f"[yellow]⚠️  {symbol} 不在你的自选列表中[/yellow]")
    db.close()


@app.command()
def watch_on(
    symbol: str = typer.Argument(..., help="股票代码"),
    user: str = typer.Option("default", "--user", "-u", help="用户标识（Feishu ID）"),
):
    """🔔 将股票标记为推送关注（每日日报将包含该股票分析）"""
    from .database import Database

    db = Database()
    user_obj = db.get_or_create_user(user, user)
    symbol = normalize_stock_code(symbol)

    ok = db.set_stock_watched(user_obj["id"], symbol, True)
    if ok:
        console.print(f"[green]✅ {symbol} 已加入推送关注清单[/green]")
        console.print("   [dim]每日日报将包含该股票的分析[/dim]")
    else:
        console.print(f"[yellow]⚠️  {symbol} 不在你的自选列表中，请先用 watch 添加[/yellow]")
    db.close()


@app.command()
def watch_off(
    symbol: str = typer.Argument(..., help="股票代码"),
    user: str = typer.Option("default", "--user", "-u", help="用户标识（Feishu ID）"),
):
    """🔕 将股票从推送关注清单移除（日报不再推送该股票）"""
    from .database import Database

    db = Database()
    user_obj = db.get_or_create_user(user, user)
    symbol = normalize_stock_code(symbol)

    ok = db.set_stock_watched(user_obj["id"], symbol, False)
    if ok:
        console.print(f"[yellow]🔕 {symbol} 已从推送关注清单移除[/yellow]")
        console.print("   [dim]该股票仍在自选列表中，但不再出现在每日日报推送中[/dim]")
    else:
        console.print(f"[yellow]⚠️  {symbol} 不在你的自选列表中[/yellow]")
    db.close()


@app.command()
def portfolio(
    user: Optional[str] = typer.Option(None, "--user", "-u", help="用户标识（不填显示所有用户）"),
):
    """📋 查看自选/持仓列表（含推送关注状态）"""
    from .database import Database

    db = Database()

    if user:
        users_data = [db.get_or_create_user(user, user)]
    else:
        users_data = db.get_all_users_with_stocks()

    if not users_data:
        console.print("[yellow]暂无用户数据[/yellow]")
        db.close()
        return

    for u in users_data:
        uid = u["id"]
        stocks = db.get_user_stocks(uid)
        if not stocks:
            continue

        watched_count = sum(1 for s in stocks if s.get("watched", 1))
        console.print()
        console.print(Panel.fit(
            f"  👤 {u['username']} ({u['feishu_id']})  |  "
            f"持仓 {len(stocks)} 只  |  "
            f"[green]推送关注 {watched_count} 只[/green]",
            style="bold cyan",
        ))
        console.print()

        table = Table(show_lines=True, header_style="bold")
        table.add_column("关注", width=6)
        table.add_column("代码", width=12)
        table.add_column("名称", width=16)
        table.add_column("买入价", width=10)
        table.add_column("数量", width=8)
        table.add_column("当前价", width=10)
        table.add_column("盈亏", width=12)
        table.add_column("备注", width=20)

        for s in stocks:
            code = s["stock_code"]
            name = s["stock_name"] or code
            buy_price = s["buy_price"]
            qty = s["quantity"]
            notes = s.get("notes", "") or ""
            watched = s.get("watched", 1)

            # Watch icon
            watch_icon = "[green]★[/green]" if watched else "[dim]☆[/dim]"

            # Get current price and P&L
            current_price = None
            pnl_str = ""
            try:
                from .stock_analyzer import _get_realtime_price
                quote = _get_realtime_price(code)
                if quote:
                    current_price = quote["price"]
                    if buy_price and buy_price > 0 and current_price:
                        change = (current_price - buy_price) / buy_price * 100
                        pnl = (current_price - buy_price) * (qty or 1)
                        mark = "+" if change >= 0 else ""
                        pnl_str = f"{mark}{change:.1f}%  (¥{pnl:+.0f})"
            except Exception:
                pass

            buy_str = f"{buy_price:.2f}" if buy_price else "-"
            qty_str = str(qty) if qty else "-"
            cur_str = f"{current_price:.2f}" if current_price else "-"

            # Color P&L
            if pnl_str:
                if "+" in pnl_str and pnl_str != "0":
                    pnl_str = f"[green]{pnl_str}[/green]"
                elif "-" in pnl_str:
                    pnl_str = f"[red]{pnl_str}[/red]"

            table.add_row(
                watch_icon,
                code,
                name,
                buy_str,
                qty_str,
                cur_str,
                pnl_str,
                notes[:18],
            )

        console.print(table)

    # Summary
    if not user:
        total_users = db.get_all_users_with_stocks()
        total_stocks = sum(
            len(db.get_user_stocks(u["id"])) for u in total_users
        )
        total_watched = sum(
            len(db.get_user_watched_stocks(u["id"])) for u in total_users
        )
        console.print(f"\n[dim]共 {len(total_users)} 位用户，持仓 {total_stocks} 只，推送关注 {total_watched} 只[/dim]")
        console.print("  [dim][green]★[/green] 推送关注  |  [dim]☆[/dim] 不推送[/dim]")

    db.close()


@app.command()
def watchlist(
    user: Optional[str] = typer.Option(None, "--user", "-u", help="用户标识（不填显示所有用户）"),
):
    """📋 查看推送关注清单（仅显示已标记为关注的股票）"""
    from .database import Database

    db = Database()

    if user:
        users_data = [db.get_or_create_user(user, user)]
    else:
        users_data = db.get_all_users_with_stocks()

    if not users_data:
        console.print("[yellow]暂无用户数据[/yellow]")
        db.close()
        return

    for u in users_data:
        uid = u["id"]
        stocks = db.get_user_watched_stocks(uid)
        if not stocks:
            continue

        console.print()
        console.print(Panel.fit(
            f"  👤 {u['username']} ({u['feishu_id']})  |  "
            f"推送关注 {len(stocks)} 只",
            style="bold cyan",
        ))
        console.print()

        table = Table(show_lines=True, header_style="bold")
        table.add_column("代码", width=12)
        table.add_column("名称", width=16)
        table.add_column("买入价", width=10)
        table.add_column("数量", width=8)
        table.add_column("当前价", width=10)
        table.add_column("盈亏", width=12)
        table.add_column("备注", width=20)

        for s in stocks:
            code = s["stock_code"]
            name = s["stock_name"] or code
            buy_price = s["buy_price"]
            qty = s["quantity"]
            notes = s.get("notes", "") or ""

            current_price = None
            pnl_str = ""
            try:
                from .stock_analyzer import _get_realtime_price
                quote = _get_realtime_price(code)
                if quote:
                    current_price = quote["price"]
                    if buy_price and buy_price > 0 and current_price:
                        change = (current_price - buy_price) / buy_price * 100
                        pnl = (current_price - buy_price) * (qty or 1)
                        mark = "+" if change >= 0 else ""
                        pnl_str = f"{mark}{change:.1f}%  (¥{pnl:+.0f})"
            except Exception:
                pass

            buy_str = f"{buy_price:.2f}" if buy_price else "-"
            qty_str = str(qty) if qty else "-"
            cur_str = f"{current_price:.2f}" if current_price else "-"

            if pnl_str:
                if "+" in pnl_str and pnl_str != "0":
                    pnl_str = f"[green]{pnl_str}[/green]"
                elif "-" in pnl_str:
                    pnl_str = f"[red]{pnl_str}[/red]"

            table.add_row(code, name, buy_str, qty_str, cur_str, pnl_str, notes[:18])

        console.print(table)

    # Summary
    if not user:
        total_users = db.get_all_users_with_stocks()
        total_watched = sum(
            len(db.get_user_watched_stocks(u["id"])) for u in total_users
        )
        console.print(f"\n[dim]共 {len(total_users)} 位用户，推送关注 {total_watched} 只股票[/dim]")

    db.close()


@app.command()
def buy(
    symbol: str = typer.Argument(..., help="股票代码"),
    price: float = typer.Argument(..., help="买入价格"),
    quantity: int = typer.Argument(..., help="买入数量"),
    notes: Optional[str] = typer.Option(None, "--notes", "-n", help="备注"),
    user: str = typer.Option("default", "--user", "-u", help="用户标识"),
):
    """💳 记录买入交易"""
    from .database import Database

    db = Database()
    user_obj = db.get_or_create_user(user, user)

    symbol = normalize_stock_code(symbol)

    # Add to watchlist if not already
    db.add_user_stock(user_obj["id"], symbol, buy_price=price, quantity=quantity)

    # Record transaction
    tx_id = db.add_transaction(
        user_obj["id"], symbol, "buy", price, quantity, notes or ""
    )

    console.print(f"[green]✅ 记录买入 {symbol}[/green]")
    console.print(f"   价格: {price:.2f}  数量: {quantity}")
    if notes:
        console.print(f"   备注: {notes}")
    console.print(f"   交易ID: {tx_id}")


@app.command()
def sell(
    symbol: str = typer.Argument(..., help="股票代码"),
    price: float = typer.Argument(..., help="卖出价格"),
    quantity: int = typer.Argument(..., help="卖出数量"),
    notes: Optional[str] = typer.Option(None, "--notes", "-n", help="备注"),
    user: str = typer.Option("default", "--user", "-u", help="用户标识"),
):
    """💸 记录卖出交易"""
    from .database import Database

    db = Database()
    user_obj = db.get_or_create_user(user, user)

    symbol = normalize_stock_code(symbol)

    tx_id = db.add_transaction(
        user_obj["id"], symbol, "sell", price, quantity, notes or ""
    )
    console.print(f"[green]✅ 记录卖出 {symbol}[/green]")
    console.print(f"   价格: {price:.2f}  数量: {quantity}")
    if notes:
        console.print(f"   备注: {notes}")
    console.print(f"   交易ID: {tx_id}")
    db.close()


# ═══════════════════════════════════════════════
#  Stock Check & Notification
# ═══════════════════════════════════════════════

@app.command()
def set_webhook(
    url: str = typer.Argument(..., help="飞书/自定义 Webhook URL"),
    user: str = typer.Option("default", "--user", "-u", help="用户标识"),
):
    """🔗 设置用户通知 Webhook（用于接收股票日报推送）"""
    from .database import Database

    db = Database()
    ok = db.update_user_webhook(user, url)
    if ok:
        console.print(f"[green]✅ 已设置 {user} 的 Webhook[/green]")
        console.print(f"   [dim]URL: {url[:50]}...[/dim]")
    else:
        # Create user first
        db.get_or_create_user(user, user)
        db.update_user_webhook(user, url)
        console.print(f"[green]✅ 已创建用户并设置 {user} 的 Webhook[/green]")
    db.close()


@app.command()
def set_openid(
    open_id: str = typer.Argument(..., help="飞书用户的 open_id（用于私聊推送）"),
    user: str = typer.Option("default", "--user", "-u", help="用户标识（Feishu ID）"),
):
    """👤 设置用户的飞书 open_id（开启私聊推送功能）"""
    from .database import Database

    db = Database()
    db.get_or_create_user(user, user)
    db.set_user_open_id(user, open_id)
    console.print(f"[green]✅ 已设置 {user} 的飞书 open_id[/green]")
    console.print(f"   [dim]open_id: {open_id[:20]}...[/dim]")
    console.print("   [dim]设置完毕后，推送将以私聊消息发送[/dim]")
    db.close()


@app.command()
def notify_on(
    user: str = typer.Option("default", "--user", "-u", help="用户标识"),
):
    """🔔 开启股票日报推送通知"""
    from .database import Database

    db = Database()
    db.get_or_create_user(user, user)
    db.set_notify_enabled(user, True)
    console.print(f"[green]✅ 已开启 {user} 的推送通知[/green]")
    console.print("   [dim]每天 07:00 / 19:00 将收到股票日报推送[/dim]")
    db.close()


@app.command()
def notify_off(
    user: str = typer.Option("default", "--user", "-u", help="用户标识"),
):
    """🔕 关闭股票日报推送通知"""
    from .database import Database

    db = Database()
    db.set_notify_enabled(user, False)
    console.print(f"[yellow]🔕 已关闭 {user} 的推送通知[/yellow]")
    db.close()


@app.command()
def notify_status():
    """📋 查看所有用户的通知开关状态"""
    from .database import Database

    db = Database()

    # All users
    stats = db.get_stats()
    users = db.get_all_users_with_stocks()
    enabled_users = db.get_users_with_notify_enabled()

    table = Table(title="用户通知开关状态", show_lines=False)
    table.add_column("用户", style="bold")
    table.add_column("飞书ID", width=16)
    table.add_column("推送开关", width=12)
    table.add_column("投递方式", width=14)
    table.add_column("Webhook/OpenID", width=30)
    table.add_column("持仓数", width=8)

    # Collect all unique users from both queries
    seen = set()
    for u in users:
        uid = u["id"]
        seen.add(uid)
        feishu_id = u.get("feishu_id", "")
        name = u.get("username", feishu_id)
        webhook = u.get("webhook_url", "") or ""
        open_id = u.get("feishu_open_id", "") or ""
        notify = u.get("notify_enabled", 0)
        stock_count = u.get("stock_count", 0)

        toggle_str = "[green]🟢 开启[/green]" if notify else "[dim]⚪ 关闭[/dim]"
        if open_id:
            delivery = "[green]私聊[/green]"
            target_display = f"open_id: {open_id[:12]}..."
        elif webhook:
            delivery = "[yellow]Webhook[/yellow]"
            target_display = webhook[:27] + "..." if len(webhook) > 27 else webhook
        else:
            delivery = "[dim]未配置[/dim]"
            target_display = "[dim]未设置[/dim]"

        table.add_row(name, feishu_id, toggle_str, delivery, target_display, str(stock_count))

    console.print()
    console.print(table)
    console.print()
    console.print(f"  [dim]共 {len(seen)} 位用户，{len(enabled_users)} 人已开启推送[/dim]")
    console.print()

    if enabled_users:
        console.print("[green]🟢 已开启推送的用户:[/green]")
        for u in enabled_users:
            name = u.get("username", u.get("feishu_id", ""))
            open_id = u.get("feishu_open_id", "") or ""
            webhook = u.get("webhook_url", "") or ""
            if open_id:
                delivery = f"[green]私聊(open_id={open_id[:12]}...)[/green]"
            elif webhook:
                delivery = f"[yellow]Webhook({webhook[:30]}...)[/yellow]"
            else:
                delivery = "[dim]未配置投递方式[/dim]"
            console.print(f"    • {name}  →  {delivery}")
    console.print()

    db.close()


@app.command()
def daily_on(
    symbol: str = typer.Argument(..., help="股票代码"),
    user: str = typer.Option("default", "--user", "-u", help="用户标识（Feishu ID）"),
):
    """📩 开启该股票的日报私发功能（每日推送将包含该股票分析）"""
    from .database import Database

    db = Database()
    user_obj = db.get_or_create_user(user, user)
    symbol = normalize_stock_code(symbol)

    ok = db.set_stock_daily_notify(user_obj["id"], symbol, True)
    if ok:
        console.print(f"[green]✅ {symbol} 已开启日报私发[/green]")
        console.print("   [dim]每天 07:00 / 19:00 将通过飞书私信发送该股票分析[/dim]")
    else:
        console.print(f"[yellow]⚠️  {symbol} 不在你的自选列表中，请先用 watch 添加[/yellow]")
    db.close()


@app.command()
def daily_off(
    symbol: str = typer.Argument(..., help="股票代码"),
    user: str = typer.Option("default", "--user", "-u", help="用户标识（Feishu ID）"),
):
    """📩 关闭该股票的日报私发功能（不再在日报中推送）"""
    from .database import Database

    db = Database()
    user_obj = db.get_or_create_user(user, user)
    symbol = normalize_stock_code(symbol)

    ok = db.set_stock_daily_notify(user_obj["id"], symbol, False)
    if ok:
        console.print(f"[yellow]🕐 {symbol} 已关闭日报私发[/yellow]")
        console.print("   [dim]该股票仍在自选列表中，但每日日报不再私发推送[/dim]")
    else:
        console.print(f"[yellow]⚠️  {symbol} 不在你的自选列表中[/yellow]")
    db.close()


@app.command()
def daily_status(
    user: Optional[str] = typer.Option(None, "--user", "-u", help="用户标识（不填显示所有用户）"),
):
    """📋 查看日报私发功能状态（哪些股票开启了日报私发）"""
    from .database import Database

    db = Database()

    if user:
        users_data = [db.get_or_create_user(user, user)]
    else:
        users_data = db.get_all_users_with_stocks()

    if not users_data:
        console.print("[yellow]暂无用户数据[/yellow]")
        db.close()
        return

    total_users_with_daily = 0
    for u in users_data:
        uid = u["id"]
        daily_stocks = db.get_user_daily_stocks(uid)
        if not daily_stocks:
            continue

        total_users_with_daily += 1
        notify_on = u.get("notify_enabled", 0)
        master_str = "[green]🟢 总开关已开[/green]" if notify_on else "[red]🔴 总开关已关[/red]"

        console.print()
        console.print(Panel.fit(
            f"  📩 {u['username']} ({u['feishu_id']})  |  "
            f"日报私发 {len(daily_stocks)} 只  |  {master_str}",
            style="bold cyan",
        ))
        console.print()

        table = Table(show_lines=False, header_style="bold")
        table.add_column("代码", width=14)
        table.add_column("名称", width=18)
        table.add_column("买入价", width=10)
        table.add_column("数量", width=8)
        table.add_column("备注", width=24)

        for s in daily_stocks:
            code = s["stock_code"]
            name = s["stock_name"] or code
            buy_price = s["buy_price"]
            qty = s["quantity"]
            notes = s.get("notes", "") or ""

            buy_str = f"{buy_price:.2f}" if buy_price else "-"
            qty_str = str(qty) if qty else "-"
            table.add_row(code, name, buy_str, qty_str, notes[:22])

        console.print(table)

    if user:
        user_obj = db.get_or_create_user(user, user)
        all_stocks = db.get_user_stocks(user_obj["id"])
        daily_s = db.get_user_daily_stocks(user_obj["id"])
        daily_codes = {s["stock_code"] for s in daily_s}
        non_daily = [s for s in all_stocks if s["stock_code"] not in daily_codes]
        if non_daily:
            console.print()
            console.print("[dim]以下股票未开启日报私发（可用 daily-on 开启）:[/dim]")
            for s in non_daily:
                console.print(f"  [dim]• {s['stock_code']} ({s.get('stock_name', '') or '未知'})[/dim]")

    console.print()
    console.print(f"[dim]共 {total_users_with_daily} 位用户开启了日报私发功能[/dim]")
    console.print()
    db.close()


@app.command()
def group_add(
    chat_id: str = typer.Argument(..., help="飞书群聊的 chat_id"),
    name: str = typer.Option("", "--name", "-n", help="群名称（便于识别）"),
    module: str = typer.Option("default", "--module", "-m", help="关联模块"),
):
    """👥 注册一个飞书群聊用于群通知"""
    from .database import Database

    db = Database()
    ok = db.add_group_chat(chat_id, name, module)
    if ok:
        display = name or chat_id[:12] + "..."
        console.print(f"[green]✅ 群聊已注册: {display}[/green]")
        console.print(f"   [dim]chat_id: {chat_id}[/dim]")
        console.print(f"   [dim]module: {module}[/dim]")
    db.close()


@app.command()
def group_remove(
    chat_id: str = typer.Argument(..., help="飞书群聊的 chat_id"),
):
    """👥 注销一个飞书群聊"""
    from .database import Database

    db = Database()
    ok = db.remove_group_chat(chat_id)
    if ok:
        console.print(f"[yellow]🗑️ 群聊已注销: {chat_id[:16]}...[/yellow]")
    else:
        console.print(f"[yellow]⚠️ 未找到该群聊: {chat_id[:16]}...[/yellow]")
    db.close()


@app.command()
def group_on(
    chat_id: str = typer.Argument(..., help="飞书群聊的 chat_id"),
):
    """🔔 开启群聊通知"""
    from .database import Database

    db = Database()
    ok = db.set_group_chat_enabled(chat_id, True)
    if ok:
        console.print(f"[green]✅ 群聊通知已开启[/green]")
    else:
        console.print(f"[yellow]⚠️ 未找到该群聊[/yellow]")
    db.close()


@app.command()
def group_off(
    chat_id: str = typer.Argument(..., help="飞书群聊的 chat_id"),
):
    """🔕 关闭群聊通知"""
    from .database import Database

    db = Database()
    ok = db.set_group_chat_enabled(chat_id, False)
    if ok:
        console.print(f"[yellow]🔕 群聊通知已关闭[/yellow]")
    else:
        console.print(f"[yellow]⚠️ 未找到该群聊[/yellow]")
    db.close()


@app.command()
def group_list(
    module: Optional[str] = typer.Option(None, "--module", "-m", help="按模块筛选"),
):
    """📋 查看所有注册的群聊"""
    from .database import Database

    db = Database()
    groups = db.get_group_chats(module=module)

    if not groups:
        console.print("[yellow]暂无注册的群聊[/yellow]")
        db.close()
        return

    console.print()
    table = Table(title="注册群聊列表", show_lines=False)
    table.add_column("群名称", width=20)
    table.add_column("chat_id", width=36)
    table.add_column("通知开关", width=12)
    table.add_column("模块", width=16)
    table.add_column("注册时间", width=20)

    for g in groups:
        name = g.get("group_name", "") or "(未命名)"
        cid = g["chat_id"]
        cid_display = cid[:16] + "..." if len(cid) > 16 else cid
        enabled = "[green]🟢 开启[/green]" if g.get("enabled") else "[dim]⚪ 关闭[/dim]"
        mod = g.get("module", "default")
        created = g.get("created_at", "")

        table.add_row(name, cid_display, enabled, mod, created)

    console.print(table)
    console.print(f"\n[dim]共 {len(groups)} 个群聊[/dim]")
    console.print()
    db.close()


@app.command()
def group_send(
    chat_id: str = typer.Argument(..., help="飞书群聊的 chat_id"),
    message: str = typer.Argument(..., help="要发送的消息内容"),
    title: str = typer.Option("群通知", "--title", "-t", help="卡片标题"),
):
    """📤 发送一条测试消息到群聊"""
    from .feishu_client import FeishuClient

    async def _send():
        fc = FeishuClient()
        if not fc.is_configured:
            console.print("[red]❌ 飞书 App ID/Secret 未配置[/red]")
            return

        ok = await fc.send_group_message(
            chat_id=chat_id,
            report=message,
            title=title,
            template="blue",
        )
        if ok:
            console.print(f"[green]✅ 消息已发送到群聊 {chat_id[:12]}...[/green]")
        else:
            console.print(f"[red]❌ 发送失败[/red]")

    asyncio.run(_send())


@app.command()
def check_stocks(
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="仅打印不发送"),
    mode: str = typer.Option("morning", "--mode", "-m", help="推送模式: morning=早间研判 evening=收盘复盘"),
):
    """📊 检查所有用户的股票并发送日报"""
    from .stock_notifier import run_stock_check

    asyncio.run(run_stock_check(dry_run=dry_run, mode=mode))


@app.command()
def schedule_stocks(
    morning: str = typer.Option("07:00", help="早间推送时间 (HH:MM)"),
    evening: str = typer.Option("19:00", help="晚间推送时间 (HH:MM)"),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="仅打印不发送"),
):
    """⏰ 启动定时股票日报推送 + 公众号跟踪（APScheduler）"""
    # 检测 Windows 计划任务, 防止重复触发
    import subprocess as _sp
    _check = _sp.run('schtasks /query /fo CSV', shell=True, capture_output=True, text=True)
    if 'AI_News_Radar_Morning' in _check.stdout:
        console.print("[red]检测到 Windows 计划任务已存在! [/red]")
        console.print("[red]勿重复运行 schedule-stocks, 否则会重复推送. [/red]")
        console.print("[yellow]请用任务计划程序管理: AI_News_Radar_* [/yellow]")
        return

    from .stock_scheduler import StockScheduler

    scheduler = StockScheduler(
        morning_time=morning,
        evening_time=evening,
        dry_run=dry_run,
    )

    async def _run():
        scheduler.start()
        # Block forever
        while True:
            await asyncio.sleep(3600)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]⏹ 定时推送已停止[/yellow]")
    finally:
        scheduler.stop()


@app.command()
def wechat_track(
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="仅打印不发送"),
    account: Optional[str] = typer.Option(None, "--account", "-a", help="公众号名称（不填则检查全部）"),
):
    """📢 执行公众号股票推荐跟踪检查并推送美化卡片到群"""
    from .stock_scheduler import StockScheduler

    accounts = [account] if account else StockScheduler.WECHAT_ACCOUNTS
    chat_id = "oc_8792267760e09f7c142bb0157bcf22f0"

    async def _run():
        from .scrapers.wechat_article import (
            check_for_new_article, fetch_and_save, _load_akshare_spot_data, _is_valid_stock,
        )
        from .database import Database
        from .feishu_client import FeishuClient

        console.print(f"[cyan]📢 微信公众号股票推荐跟踪[/cyan]")
        console.print()

        # Step 1: Check new articles
        for acc in accounts:
            console.print(f"  [dim]📱 {acc}...[/dim]")
            try:
                new_article = await check_for_new_article(account=acc)
                if new_article:
                    save_result = await fetch_and_save(new_article["url"], account=acc)
                    console.print(f"  [green]✅ 新文章: {save_result.get('title', '')[:50]}[/green]")
                    console.print(f"  [green]   识别到 {save_result.get('stocks_found', 0)} 只股票[/green]")
                else:
                    console.print(f"  [dim]无新文章[/dim]")
            except Exception as e:
                console.print(f"  [red]出错: {e}[/red]")

        # Step 2: Build card data
        console.print(f"\n[cyan]构建卡片数据...[/cyan]")
        db = Database()
        spot_data = _load_akshare_spot_data()

        account_sections = []
        tracking_dict = {}

        for acc in accounts:
            latest = db.get_latest_wechat_article(acc)
            if not latest:
                continue
            title = latest.get("title", "")
            aid = latest["id"]
            is_analyzed = latest.get("is_analyzed", 0)

            # 用 is_analyzed 判断: 0=未分析(需要发卡), 1=已分析(发过卡了)
            if is_analyzed == 0:
                refs = db.get_article_stock_refs(article_id=aid)
                sections = {}
                for r in refs:
                    if not _is_valid_stock(r["stock_code"]): continue
                    sd = spot_data.get(r["stock_code"])
                    if not sd: continue
                    sec = r.get("section", "") or "其他"
                    sections.setdefault(sec, []).append({
                        "code": r["stock_code"],
                        "name": sd.get("name","") or r.get("stock_name","") or r["stock_code"][:10],
                        "price": sd.get("price",0), "high": sd.get("high",0), "low": sd.get("low",0),
                    })
                status = "new" if sections else "no_stocks"
            else:
                sections = {}
                status = "analyzed"
            account_sections.append((acc, status, title, sections))

            for t in db.get_active_tracked_stocks(source_account=acc):
                if not _is_valid_stock(t["stock_code"]): continue
                sd = spot_data.get(t["stock_code"])
                if not sd: continue
                c = t["stock_code"]
                d1p = t.get("day1_price")
                cur = sd.get("price", 0)

                # Capture Day1 data if not set
                if not d1p and cur > 0:
                    db.update_tracking_daily_data(
                        stock_code=c, source_account=acc,
                        day1_price=cur, day1_change_pct=sd.get("change_pct", 0),
                    )
                    d1p = cur

                if c not in tracking_dict or sd.get("amount",0) > tracking_dict[c].get("amount",0):
                    track_day = int(t.get("track_day", 0)) + 1
                    tracking_dict[c] = {
                        "name": sd.get("name","") or t.get("stock_name","") or c[:10],
                        "price": cur,
                        "day1_price": d1p,
                        "amount": sd.get("amount",0),
                        "source": acc,
                        "track_day": track_day,
                    }

        db.close()

        # Compute 十全十美 scores (异常不影响卡片发送)
        if tracking_dict:
            try:
                from .stock_scheduler import _compute_sqsm_scores
                console.print(f"  [cyan]计算十全十美指标 ({len(tracking_dict)}只)...[/cyan]")
                sqsm_scores = await _compute_sqsm_scores(list(tracking_dict.keys()), spot_data)
                for code, s_data in tracking_dict.items():
                    sqsm = sqsm_scores.get(code, {})
                    s_data["sqsm_score"] = sqsm.get("bull_ratio", "-/-")
                    s_data["sqsm_resonance"] = sqsm.get("total", 0) > 8  # 9分及以上共振
            except Exception:
                console.print(f"  [red]十全十美计算出错，跳过指标显示[/red]")

        sorted_tracking = sorted(tracking_dict.values(), key=lambda x: x.get("amount",0), reverse=True)[:10]
        total_tracking = len(tracking_dict)

        new_count = sum(1 for _,s,_,_ in account_sections if s == "new")
        no_stock_count = sum(1 for _,s,_,_ in account_sections if s == "no_stocks")
        analyzed_count = sum(1 for _,s,_,_ in account_sections if s == "analyzed")
        console.print(f"  新文章: {new_count}, 无有效股票: {no_stock_count}, 已分析: {analyzed_count}")
        console.print(f"  持续跟踪: {total_tracking} 只")
        console.print()

        if dry_run:
            console.print("[dim]DRY RUN - 卡片已就绪，未发送[/dim]")
            return

        # Step 3: Send
        fc = FeishuClient()
        if not fc.is_configured:
            console.print("[red]❌ 飞书未配置[/red]")
            return

        ok = await fc.send_tracking_card(
            chat_id=chat_id,
            new_stocks_data=account_sections,
            tracking_data=sorted_tracking,
            total_tracking=total_tracking,
        )
        if ok:            console.print(f"[green]✅ 美化日报已推送到群[/green]")        else:            console.print(f"[red]❌ 推送失败[/red]")
        console.print()
        console.print("[dim]每天早上 07:00 自动执行全部公众号检查并推送到群[/dim]")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")


@app.command()
def scrape():
    """Only scrape news feeds, skip analysis and reporting."""
    from .engine import Engine
    engine = Engine(analyze=False, notify=False)

    async def _scrape():
        count = await engine.scrape_only()
        console.print(f"[green]Scraped and stored {count} articles.[/green]")

    try:
        asyncio.run(_scrape())
    finally:
        engine.close()


@app.command()
def analyze(
    limit: int = typer.Option(50, help="Max articles to analyze"),
    backend: str = typer.Option("auto", help="AI backend: deepseek, openai, anthropic, or auto"),
):
    """Analyze unprocessed articles using AI."""
    from .engine import Engine
    engine = Engine(backend=backend, analyze=True)

    async def _analyze():
        count = await engine.analyze_only(limit=limit)
        console.print(f"[green]Analyzed {count} articles.[/green]")

    try:
        asyncio.run(_analyze())
    finally:
        engine.close()


@app.command()
def digest(
    top_n: int = typer.Option(15, help="Number of top articles in digest"),
    notify: bool = typer.Option(False, help="Send notifications"),
    output: Optional[str] = typer.Option(None, help="Save report to file"),
):
    """Generate a news digest from today's articles."""
    from .engine import Engine
    engine = Engine(analyze=False, notify=notify)

    async def _digest():
        report = await engine.report_only(top_n=top_n)
        if report:
            # Display to terminal
            md = Markdown(report.content)
            console.print(md)

            if output:
                with open(output, "w", encoding="utf-8") as f:
                    f.write(report.content)
                console.print(f"\n[green]Report saved to {output}[/green]")
        else:
            console.print("[yellow]No articles found for today. Run 'ai-news scrape' first.[/yellow]")

    try:
        asyncio.run(_digest())
    finally:
        engine.close()


@app.command()
def wechat(
    url: str = typer.Argument(..., help="微信公众号文章链接"),
    account: str = typer.Option("凡尘一灯", "--account", "-a", help="公众号名称"),
    analyze: bool = typer.Option(True, "--analyze/--no-analyze", help="是否解析股票推荐"),
):
    """📱 抓取微信公众号文章并解析股票推荐"""
    from .scrapers.wechat_article import fetch_and_save, format_stock_summary

    async def _run():
        console.print(f"[cyan]📱 抓取公众号文章: {account}[/cyan]")
        console.print(f"   [dim]URL: {url}[/dim]")
        console.print()

        result = await fetch_and_save(url, account=account)

        if result["status"] == "already_exists":
            console.print(f"[yellow]⚠️ 该文章已存在数据库中[/yellow]")
            db2 = Database()
            refs = db2.get_article_stock_refs(article_id=result["article_id"])
            if refs:
                console.print(f"\n[bold]已解析的股票推荐:[/bold]")
                console.print(format_stock_summary(refs))
            db2.close()
            return

        if result["status"] == "error":
            console.print(f"[red]❌ 抓取失败: {result.get('message', '未知错误')}[/red]")
            return

        # Success
        console.print(f"[green]✅ 文章抓取成功![/green]")
        console.print(f"   标题: {result['title']}")
        console.print(f"   文章ID: {result['article_id']}")
        console.print(f"   识别到 {result['stocks_found']} 只股票推荐")
        console.print()

        if result["stocks_found"] > 0:
            console.print("[bold]📊 文章中提到的股票:[/bold]")
            console.print(format_stock_summary(result["stocks"]))

            # Show by section
            console.print()
            console.print("[bold]📂 按板块分类:[/bold]")
            sections = {}
            for s in result["stocks"]:
                sec = s.get("section", "其他")
                if sec not in sections:
                    sections[sec] = []
                sections[sec].append(s)
            for sec_name, sec_stocks in sections.items():
                console.print(f"  [cyan]📂 {sec_name}[/cyan]")
                for s in sec_stocks:
                    fn = s.get("stock_full_name", "") or s.get("stock_name", "")
                    console.print(f"    📈 {fn} ({s['stock_code']})")
            console.print()

        console.print("[dim]提示: 运行 'ai-news wechat-recommend' 查看近期推荐汇总[/dim]")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")


@app.command()
def wechat_list(
    account: Optional[str] = typer.Option(None, "--account", "-a", help="公众号名称"),
    limit: int = typer.Option(10, "--limit", "-l", help="显示条数"),
):
    """📋 查看已抓取的微信公众号文章列表"""
    from .database import Database

    db = Database()
    articles = db.get_wechat_articles(account=account, limit=limit)

    if not articles:
        console.print("[yellow]暂无文章数据[/yellow]")
        db.close()
        return

    table = Table(title="微信公众号文章列表", show_lines=False)
    table.add_column("ID", width=6)
    table.add_column("标题", width=40)
    table.add_column("公众号", width=16)
    table.add_column("抓取时间", width=20)

    for a in articles:
        table.add_row(
            str(a["id"]),
            a["title"][:38] if a["title"] else "(无标题)",
            a["account"],
            a["fetched_at"][:16] if a["fetched_at"] else "",
        )

    console.print()
    console.print(table)
    console.print()
    db.close()


@app.command()
def wechat_recommend(
    days: int = typer.Option(3, "--days", "-d", help="最近几天"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="推送到飞书用户"),
):
    """📈 查看近期微信公众号推荐股票汇总"""
    from .database import Database
    from rich.table import Table
    from rich.panel import Panel

    db = Database()
    stocks = db.get_wechat_recommended_stocks(days=days)

    if not stocks:
        console.print(f"[yellow]近 {days} 天暂无推荐文章中的股票记录[/yellow]")
        db.close()
        return

    console.print()
    console.print(Panel.fit(
        f"  📈 近 {days} 天微信公众号推荐股票汇总",
        style="bold cyan",
    ))
    console.print()

    table = Table(show_lines=True, header_style="bold")
    table.add_column("股票代码", width=12)
    table.add_column("名称", width=18)
    table.add_column("提及次数", width=10)
    table.add_column("所属板块", width=30)
    table.add_column("最近文章", width=40)

    for s in stocks:
        code = s["stock_code"]
        full_name = s.get("stock_full_name", "") or ""
        count = s.get("mention_count", 0)
        sections = s.get("sections", "") or ""
        article = s.get("latest_article", "") or ""
        table.add_row(code, full_name, str(count), sections[:28], article[:38])

    console.print(table)
    console.print()

    if user:
        from .feishu_client import FeishuClient

        # Build summary message
        lines = [f"**📈 近 {days} 天公众号推荐股票汇总**", ""]
        for s in stocks:
            fn = s.get("stock_full_name", "") or s["stock_code"]
            cnt = s.get("mention_count", 1)
            lines.append(f"- {fn} ({s['stock_code']})  提及 {cnt} 次")
        msg = "\n".join(lines)

        async def _send():
            fc = FeishuClient()
            user_obj = db.get_user_by_feishu(user)
            if user_obj and user_obj.get("feishu_open_id"):
                ok = await fc.send_private_message(user_obj["feishu_open_id"], msg, is_evening=False)
                if ok:
                    console.print(f"[green]✅ 已推送给 {user}[/green]")

        asyncio.run(_send())

    db.close()


@app.command()
def list(
    source: Optional[str] = typer.Option(
        None, help="Filter by source: anthropic, github_trending, github_fast_growing, hacker_news, reddit_ml"
    ),
    category: Optional[str] = typer.Option(None, help="Filter by category"),
    min_score: float = typer.Option(0.0, help="Minimum importance score (0.0-1.0)"),
    sentiment: Optional[str] = typer.Option(None, help="Filter by sentiment: positive, neutral, negative"),
    limit: int = typer.Option(30, help="Max articles to show"),
    offset: int = typer.Option(0, help="Offset for pagination"),
):
    """List articles with optional filters."""
    from .database import Database

    db = Database()
    articles = db.get_articles(
        source_type=source,
        category=category,
        min_importance=min_score,
        sentiment=sentiment,
        limit=limit,
        offset=offset,
        analyzed_only=True,
    )

    if not articles:
        console.print("[yellow]No articles found matching the filters.[/yellow]")
        return

    table = Table(title="Articles", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Title", style="bold", max_width=60)
    table.add_column("Source", width=14)
    table.add_column("Importance", width=10)
    table.add_column("Category", width=14)

    for i, article in enumerate(articles, 1):
        score_style = (
            "red" if article.importance >= 0.8
            else "yellow" if article.importance >= 0.5
            else "dim"
        )
        table.add_row(
            str(i),
            article.title[:60],
            article.source_type.value,
            f"[{score_style}]{article.importance:.2f}[/{score_style}]",
            article.category.value,
        )

    console.print(table)


@app.command()
def stats():
    """Show database statistics."""
    from .database import Database

    db = Database()
    stats = db.get_stats()

    console.print(Panel.fit("Database Statistics", style="bold cyan"))
    console.print(f"  Total articles:    {stats['total_articles']}")
    console.print(f"  Analyzed:          {stats['analyzed_articles']}")
    console.print(f"  Added today:       {stats['articles_today']}")
    console.print(f"  By source:")

    source_names = {
        "anthropic": "Anthropic Blog",
        "github_trending": "GitHub Trending",
        "github_fast_growing": "GitHub Fast-Growing",
        "hacker_news": "Hacker News",
        "reddit_ml": "Reddit r/ML",
    }

    for src, count in sorted(stats["by_source"].items()):
        name = source_names.get(src, src)
        bar = "█" * min(count, 30)
        console.print(f"    {name:20s} {count:4d} {bar}")


@app.command()
def schedule(
    daily_time: str = typer.Option("09:00", help="Daily report time (HH:MM)"),
    interval: int = typer.Option(4, help="Scrape interval in hours"),
    backend: str = typer.Option("auto", help="AI backend"),
    analyze: bool = typer.Option(True, help="Enable AI analysis"),
    notify: bool = typer.Option(False, help="Enable notifications"),
):
    """Start the scheduler for periodic news collection."""
    _print_banner()
    console.print(
        f"[cyan]Scheduler starting: scrape every {interval}h, daily report at {daily_time}[/cyan]"
    )
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    from .scheduler import run_scheduler
    run_scheduler(
        backend=backend,
        analyze=analyze,
        notify=notify,
        daily_time=daily_time,
        scrape_interval_hours=interval,
    )


@app.command()
def version():
    """Show version information."""
    console.print(f"AI News Radar v{__version__}")


@app.command()
def sqsm(
    code: str = typer.Argument(..., help="股票代码, 如 600172 或 sh600172"),
):
    """📊 查询股票十全十美实时共振指标（盘中可估算）"""
    from .utils.helpers import normalize_stock_code
    from .sqsm_indicator import ShiQuanShiMei
    from .stock_analyzer import _fetch_stock_daily
    from datetime import datetime, time
    import numpy as np

    symbol = normalize_stock_code(code)
    console.print(f"\n[bold cyan]🔍 十全十美指标查询: {symbol}[/bold cyan]")
    console.print()

    recs = _fetch_stock_daily(symbol, days=500)
    if len(recs) < 80:
        console.print(f"[red]数据不足: {len(recs)}天, 需要80天[/red]")
        return

    c = np.array([r['close'] for r in recs], dtype=float)
    h = np.array([r['high'] for r in recs], dtype=float)
    l = np.array([r['low'] for r in recs], dtype=float)
    v = np.array([r['volume'] for r in recs], dtype=float)
    sqsm = ShiQuanShiMei()

    now = datetime.now()
    in_trading = (9,30) <= (now.hour, now.minute) <= (15,0) and not ((11,30) <= (now.hour, now.minute) <= (13,0))

    # Yesterday exact score
    res_yest = sqsm.calculate(c[:-1], h[:-1], l[:-1], v[:-1])
    yest_score = res_yest.get('total', 0) if 'error' not in res_yest else '?'

    today_score = None
    # Try real-time data if in trading hours
    if in_trading:
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot()
            row = df[df['代码'] == symbol]
            if not row.empty:
                r = row.iloc[0]
                cur_price = float(r['最新价'])
                cur_high = max(float(r['最高']), recs[-1]['high'])
                cur_low = min(float(r['最低']), recs[-1]['low'])
                cur_open = float(r['今开'])
                # Use yesterday volume as estimate
                cur_vol = recs[-1]['volume']

                c_est = np.append(c, cur_price)
                h_est = np.append(h, cur_high)
                l_est = np.append(l, cur_low)
                v_est = np.append(v, cur_vol)

                res_today = sqsm.calculate(c_est, h_est, l_est, v_est)
                if 'error' not in res_today:
                    today_score = res_today.get('total', 0)
                    today_latest = res_today.get('latest', {})
                    console.print(f"  [green]实时价: {cur_price:.2f}[/green]")
        except Exception as e:
            pass

    # At least show the latest available data
    res_last = sqsm.calculate(c, h, l, v)
    last_score = res_last.get('total', 0) if 'error' not in res_last else '?'
    last_latest = res_last.get('latest', {}) if 'error' not in res_last else {}
    last_date = recs[-1]['date']

    display_score = today_score if today_score is not None else last_score

    indicator_names = ['MACD','KDJ','RSI','LWR','BBI','ZLMM','DBCD','CGZ','ZLGJ','ZJL']
    show_map = today_latest if today_score is not None else last_latest

    console.print(f"  最新可用: {last_date}（{len(recs)}天）")
    console.print(f"  昨日({recs[-2]['date']}): {yest_score}/10")
    score_color = "green" if display_score >= 9 else ("yellow" if display_score >= 7 else "white")
    console.print(f"  [{score_color}]今日: {display_score}/10[/]")
    console.print()

    # Show individual indicators
    console.print("  指标状态:")
    for name in indicator_names:
        val = show_map.get(name, False)
        icon = "[green]🟢[/green]" if val else "[red]🔴[/red]"
        console.print(f"    {icon} {name}")

    console.print()
    if display_score >= 9:
        console.print("  [bold green]✅ 9分共振[/bold green]")
    elif display_score >= 7:
        console.print("  [yellow]⚡ 7-8分接近共振[/yellow]")
    else:
        console.print("  [dim]❌ 未共振[/dim]")

    if display_score >= 9 and yest_score < 9:
        console.print("  [bold green]✨ 首日9分共振！[/bold green]")

    source = "实时估算" if today_score is not None else "最近收盘"
    console.print(f"\n  [dim]数据来源: {source} | {datetime.now().strftime('%H:%M')}[/dim]")
    console.print()


def _print_result(result):
    """Pretty-print pipeline results."""
    console.print()

    # Summary panel
    duration = (result.finished_at - result.started_at).total_seconds() if result.finished_at else 0
    summary = Text()
    summary.append(f"Pipeline completed in {duration:.1f}s\n", style="bold green")
    summary.append(f"  Fetched: {result.total_fetched} | ")
    summary.append(f"New: {result.total_new} | ")
    summary.append(f"Analyzed: {result.analyzed_count}")
    console.print(Panel(summary, title="Result", style="green"))

    if result.report:
        console.print()
        md = Markdown(result.report.content)
        console.print(md)

    if result.errors:
        console.print("\n[red]Errors:[/red]")
        for error in result.errors:
            console.print(f"  [red]- {error}[/red]")


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
