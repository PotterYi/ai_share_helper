"""
Feishu API client — sends private messages to individual users
via Feishu Open API (im.message.create).
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

import httpx

from .config import get_feishu_app_id, get_feishu_app_secret

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


class FeishuClient:
    """Feishu Open API client for sending private messages."""

    def __init__(self):
        self.app_id = get_feishu_app_id()
        self.app_secret = get_feishu_app_secret()
        self._token: Optional[str] = None
        self._token_expires_at: float = 0

    @property
    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    async def _get_tenant_token(self) -> str:
        """Get or refresh tenant_access_token.

        参考飞书文档: https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal

        POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal
        Body: { "app_id": "...", "app_secret": "..." }
        Response: { "code": 0, "msg": "ok", "tenant_access_token": "...", "expire": 7200 }
        """
        if self._token and datetime.now().timestamp() < self._token_expires_at:
            return self._token

        if not self.is_configured:
            raise RuntimeError("Feishu App ID/Secret not configured")

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        _TOKEN_URL,
                        json={"app_id": self.app_id, "app_secret": self.app_secret},
                    )
                    data = resp.json()

                    if resp.status_code != 200:
                        logger.warning(
                            "Token request failed (attempt %d/3): HTTP %d %s",
                            attempt + 1, resp.status_code, resp.text[:200],
                        )
                        await asyncio.sleep(1 * (attempt + 1))
                        continue

                    if data.get("code") != 0:
                        logger.warning(
                            "Token request failed (attempt %d/3): code=%s msg=%s",
                            attempt + 1, data.get("code"), data.get("msg", ""),
                        )
                        await asyncio.sleep(1 * (attempt + 1))
                        continue

                    self._token = data["tenant_access_token"]
                    self._token_expires_at = datetime.now().timestamp() + (
                        data.get("expire", 7200) - 60  # Refresh 60s early
                    )
                    logger.debug("Feishu tenant_access_token acquired (expires in %ds)", data.get("expire", 7200))
                    return self._token

            except httpx.TimeoutException:
                logger.warning("Token request timed out (attempt %d/3)", attempt + 1)
                await asyncio.sleep(2 * (attempt + 1))
            except Exception as e:
                logger.warning("Token request error (attempt %d/3): %s", attempt + 1, e)
                await asyncio.sleep(2 * (attempt + 1))

        raise RuntimeError(
            f"Failed to get tenant_access_token after 3 attempts"
        )

    async def _build_card_content(self, report: str, is_evening: bool = False,
                                    title: str = "", template: str = "") -> str:
        """Build Feishu interactive card JSON string.

        Args:
            report: Markdown content.
            is_evening: If True, uses "收盘复盘" header (only when title is empty).
            title: Override card title. If empty, auto-detect based on is_evening.
            template: Card color template. If empty, auto-detect based on is_evening.

        Returns:
            JSON string for the Feishu card.
        """
        if not title:
            header_title = "收盘复盘" if is_evening else "早间研判"
        else:
            header_title = title
        if not template:
            template_val = "indigo" if is_evening else "blue"
        else:
            template_val = template

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📊 {header_title}"},
                "template": template_val,
            },
            "elements": [
                {"tag": "markdown", "content": report},
                {"tag": "hr"},
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
        }
        return json.dumps(card, ensure_ascii=False)

    async def send_private_message(
        self, open_id: str, report: str, is_evening: bool = False
    ) -> bool:
        """Send a private message to a Feishu user by open_id.

        Args:
            open_id: The Feishu user's open_id.
            report: Markdown report content.
            is_evening: Whether this is an evening (summary) report.

        Returns:
            True if sent successfully.
        """
        if not self.is_configured:
            logger.error("Feishu App ID/Secret not configured")
            return False
        if not open_id:
            logger.error("No open_id provided for private message")
            return False

        try:
            token = await self._get_tenant_token()
            content = await self._build_card_content(report, is_evening=is_evening)

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{_MSG_URL}?receive_id_type=open_id",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "receive_id": open_id,
                        "msg_type": "interactive",
                        "content": content,
                    },
                )
                data = resp.json()
                if resp.status_code == 200 and data.get("code") == 0:
                    logger.info("Private message sent to open_id=%s...", open_id[:8])
                    return True
                else:
                    logger.warning(
                        "Failed to send private message to %s: code=%s msg=%s",
                        open_id[:8],
                        data.get("code"),
                        data.get("msg", ""),
                    )
                    return False
        except Exception as e:
            logger.error("Error sending private message: %s", e)
            return False

    async def send_private_card(
        self,
        open_id: str,
        stock_data: list,
        is_evening: bool = False,
        total_pnl: float = 0,
    ) -> bool:
        """Send a beautiful interactive card for private daily report.

        Args:
            open_id: Feishu user's open_id.
            stock_data: List of dicts with keys:
                name, code, buy_price, quantity, current_price,
                change_pct, pnl, sqsm_score, sqsm_suggestion,
                ai_trend, ai_suggestion, ai_summary
            is_evening: Whether evening report.
            total_pnl: Total portfolio P&L.

        Returns:
            True if sent successfully.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        title = "收盘复盘" if is_evening else "早间研判"
        template_val = "indigo" if is_evening else "blue"

        elements = []
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**{now_str}**"}})
        elements.append({"tag": "hr"})

        for s in stock_data:
            # Stock header with P&L
            pnl = s.get("pnl", 0)
            arrow = "\U0001f4c8" if pnl >= 0 else "\U0001f4c9"
            pnl_color = "\U0001f534" if pnl >= 0 else "\U0001f7e2"
            name = s.get("name", "")
            code = s.get("code", "")
            price = s.get("current_price", 0)
            buy_price = s.get("buy_price", 0)
            change = s.get("change_pct", 0)
            qty = s.get("quantity", 0)

            # Stock line: name and price
            price_line = f"{arrow} **{name}** ({code})"
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": price_line}})

            # Price info line
            if buy_price and price:
                change_color = "🔴" if change >= 0 else "🟢"
                ch_str = f"{change:+.1f}%" if change else "-"
                pnl_str = f"{pnl_color} ¥{pnl:+.0f}" if qty > 0 else ""
                price_info = f"  买入 {buy_price:.2f} → 现在 {price:.2f}  {change_color} {ch_str}"
                if pnl_str:
                    price_info += f"  {pnl_str}"
            else:
                price_info = f"  当前价: {price:.2f}" if price else "  暂无行情"
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": price_info}})

            # 十全十美 score + trend + institutional
            sqsm = s.get("sqsm_score", "-/-")
            sqsm_sug = s.get("sqsm_suggestion", "")
            sqsm_line = f"  十全十美: {sqsm}"
            if sqsm_sug:
                icon = "\U0001f7e2" if "买入" in sqsm_sug else "\U0001f534"
                sqsm_line += f"  {icon} {sqsm_sug}"
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": sqsm_line}})

            # Resonance trend (新字段1)
            sqsm_trend = s.get("sqsm_trend", "")
            if sqsm_trend:
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"  \U0001f4c8 {sqsm_trend}"}})

            # Institutional buy/sell (新字段2&3)
            inst_loaded = s.get("inst_loaded", False)
            inst_buy = s.get("inst_buy", 0)
            inst_sell = s.get("inst_sell", 0)
            if inst_loaded:
                if inst_buy or inst_sell:
                    inst_line_parts = []
                    if inst_buy:
                        inst_line_parts.append(f"机构买入 ¥{inst_buy/10000:.0f}万")
                    if inst_sell:
                        inst_line_parts.append(f"机构卖出 ¥{inst_sell/10000:.0f}万")
                    if inst_buy > inst_sell:
                        inst_icon = "\U0001f7e2"
                    elif inst_sell > inst_buy:
                        inst_icon = "\U0001f534"
                    else:
                        inst_icon = "\U0001f7e2"
                    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"  {inst_icon} {' | '.join(inst_line_parts)}"}})
                else:
                    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "  ⚪ 暂无机构龙虎榜数据"}})

            # AI suggestion
            ai_sug = s.get("ai_suggestion", "")
            ai_trend = s.get("ai_trend", "")
            ai_summary = s.get("ai_summary", "")
            ai_entry = s.get("ai_entry", "")
            ai_stop = s.get("ai_stop_loss", "")
            ai_target = s.get("ai_target", "")
            ai_risk = s.get("ai_risk", "")
            ai_action = s.get("ai_action", "")

            if ai_sug or ai_trend:
                ai_line = f"  AI分析: {ai_trend}"
                if ai_sug:
                    ai_line += f"  |  {ai_sug}"
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": ai_line}})

            # Entry / Stop / Target
            parts_ks = []
            if ai_entry:
                parts_ks.append(f"入场: {ai_entry}")
            if ai_stop:
                parts_ks.append(f"止损: {ai_stop}")
            if ai_target:
                parts_ks.append(f"目标: {ai_target}")
            if parts_ks:
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"  {' | '.join(parts_ks)}"}})

            # Risk level
            if ai_risk:
                risk_icon = "\U0001f534" if "高" in ai_risk else "⚡" if "中" in ai_risk else "\U0001f7e2"
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"  {risk_icon} {ai_risk}"}})

            # Urgent action
            if ai_action:
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"  ⏰ {ai_action}"}})

            if ai_summary:
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"  {ai_summary[:80]}"}})

        elements.append({"tag": "hr"})

        # Total P&L
        if total_pnl != 0:
            summary_icon = "\U0001f534" if total_pnl >= 0 else "\U0001f7e2"
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"{summary_icon} **总盈亏: ¥{total_pnl:+.0f}**"},
            })
            elements.append({"tag": "hr"})

        elements.append({
            "tag": "note", "elements": [
                {"tag": "plain_text", "content": f"AI News Radar · {now_str}  |  {'收盘复盘' if is_evening else '早间研判'}"}
            ],
        })

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"\U0001f4ca {title}"},
                "template": template_val,
            },
            "elements": elements,
        }

        content = json.dumps(card, ensure_ascii=False)

        try:
            token = await self._get_tenant_token()
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{_MSG_URL}?receive_id_type=open_id",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={"receive_id": open_id, "msg_type": "interactive", "content": content},
                )
                data = resp.json()
                if resp.status_code == 200 and data.get("code") == 0:
                    logger.info("Private card sent to %s...", open_id[:8])
                    return True
                else:
                    logger.warning("Private card failed for %s: code=%s msg=%s",
                                   open_id[:8], data.get("code"), data.get("msg", ""))
                    return False
        except Exception as e:
            logger.error("Error sending private card: %s", e)
            return False

    async def send_group_message(
        self,
        chat_id: str,
        report: str,
        title: str = "📢 群通知",
        template: str = "blue",
    ) -> bool:
        """Send a message to a Feishu group chat by chat_id.

        Args:
            chat_id: The Feishu group chat_id.
            report: Markdown report content.
            title: Card header title.
            template: Card color template (blue, green, red, etc.)

        Returns:
            True if sent successfully.
        """
        if not self.is_configured:
            logger.error("Feishu App ID/Secret not configured")
            return False
        if not chat_id:
            logger.error("No chat_id provided for group message")
            return False

        try:
            token = await self._get_tenant_token()
            content = await self._build_card_content(report, title=title, template=template)

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{_MSG_URL}?receive_id_type=chat_id",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "receive_id": chat_id,
                        "msg_type": "interactive",
                        "content": content,
                    },
                )
                data = resp.json()
                if resp.status_code == 200 and data.get("code") == 0:
                    logger.info("Group message sent to chat_id=%s...", chat_id[:8])
                    return True
                else:
                    logger.warning(
                        "Failed to send group message to %s: code=%s msg=%s",
                        chat_id[:8],
                        data.get("code"),
                        data.get("msg", ""),
                    )
                    return False
        except Exception as e:
            logger.error("Error sending group message: %s", e)
            return False

    async def send_to_user(
        self,
        open_id: str,
        webhook_url: str,
        report: str,
        is_evening: bool = False,
    ) -> bool:
        any_ok = False

        # Channel 1: Private message via Feishu API
        if open_id and self.is_configured:
            pm_ok = await self.send_private_message(open_id, report, is_evening=is_evening)
            if pm_ok:
                any_ok = True
                logger.info("Private message sent to open_id=%s...", open_id[:8])
            else:
                logger.warning("Private message failed for open_id=%s...", open_id[:8])

        # Channel 2: Group webhook broadcast
        if webhook_url:
            from .stock_notifier import send_webhook
            wh_ok = await send_webhook(webhook_url, report)
            if wh_ok:
                any_ok = True
                logger.info("Webhook sent to %s...", webhook_url[:40])
            else:
                logger.warning("Webhook failed for %s...", webhook_url[:40])

        return any_ok

    async def send_tracking_card(
        self,
        chat_id: str,
        new_stocks_data: list,
        tracking_data: list,
        total_tracking: int = 0,
    ) -> bool:
        """Send a beautiful interactive card with column_set tables for tracking report.

        Args:
            chat_id: Feishu group chat_id.
            new_stocks_data: List of (account, status, title, sections) tuples.
                status: "new" (has new stocks) or "analyzed" (already analyzed, no new stocks)
                sections: {section_name: [{name, price, high, low}]}
            tracking_data: List of dicts with keys:
                name, price, day1_price, source, track_day
            total_tracking: Total number of tracked stocks.

        Returns:
            True if sent successfully.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        elements = []

        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**{now_str}**"}})
        elements.append({"tag": "hr"})

        # Per-account sections
        for item in new_stocks_data:
            account, status, title, sections = item
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**公众号：{account}**"}})

            if status == "new":
                # New article with stocks
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"\U0001f4c4 **最新文章：** {title[:40]}"}})

                for sec_name, stocks in sections.items():
                    sc = sec_name.replace(".", "").strip()
                    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"\U0001f4c2 *{sc}*"}})
                    elements.append({
                        "tag": "column_set", "flex_mode": "none", "columns": [
                            {"tag": "column", "width": "weighted", "weight": 2, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "**名称**"}}]},
                            {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "**当前价**"}}]},
                            {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "**最高**"}}]},
                            {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "**最低**"}}]},
                        ],
                    })
                    for i, s in enumerate(stocks):
                        bg = "grey" if i % 2 == 0 else "default"
                        hi = f"{s['high']:.2f}" if s.get('high') else "-"
                        lo = f"{s['low']:.2f}" if s.get('low') else "-"
                        name_display = f"{s['name'][:8]}({s.get('code','')[-6:]})"
                        elements.append({
                            "tag": "column_set", "flex_mode": "none", "background_style": bg, "columns": [
                                {"tag": "column", "width": "weighted", "weight": 2, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": name_display}}]},
                                {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"{s['price']:.2f}"}}]},
                                {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": hi}}]},
                                {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": lo}}]},
                            ],
                        })
            elif status == "no_stocks":
                # 新文章但无有效股票
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"\U0001f4c4 **最新文章：** {title[:40]}"}})
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "📭 该文章不包含有效新增股票"}})
            else:
                # Already analyzed (卡片已发过)
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"\U0001f4c4 **最新文章：** {title[:40]}"}})
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "✅ 该文章已分析，无新增股票"}})

        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"\U0001f4ca **持续跟踪股票（规模前十）**"}})

        if tracking_data:
            elements.append({
                "tag": "column_set", "flex_mode": "none", "columns": [
                    {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "**名称**"}}]},
                    {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "**当前价**"}}]},
                    {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "**Day1价**"}}]},
                    {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "**对比**"}}]},
                    {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "**跟踪**"}}]},
                    {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "**十全十美**"}}]},
                    {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "**来源**"}}]},
                ],
            })
            for i, s in enumerate(tracking_data):
                bg = "grey" if i % 2 == 0 else "default"
                cur = s['price']
                d1p = s.get('day1_price')
                if d1p and d1p > 0 and cur > 0:
                    ch = (cur - d1p) / d1p * 100
                    ct = f"\U0001f534 +{ch:.1f}%" if ch >= 0 else f"\U0001f7e2 {ch:.1f}%"
                else:
                    ct = "-"
                d1s = f"{d1p:.2f}" if d1p else "-"
                track_day = s.get('track_day', 1)
                day_str = f"第{track_day}天"
                sqsm = s.get("sqsm_score", "-/-")
                sqsm_display = f"\U0001f48e{sqsm}" if s.get("sqsm_resonance") else sqsm
                elements.append({
                    "tag": "column_set", "flex_mode": "none", "background_style": bg, "columns": [
                        {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"{s['name'][:6]}({s.get('code','')[-6:]})"}}]},
                        {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"{cur:.2f}"}}]},
                        {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": d1s}}]},
                        {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": ct}}]},
                        {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": day_str}}]},
                        {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": sqsm_display}}]},
                        {"tag": "column", "width": "weighted", "weight": 1, "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": s['source'][:8]}}]},
                    ],
                })

        # Summary hints
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"\U0001f539 共 {total_tracking} 只跟踪中，展示成交额最大的10只"}})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\U0001f539 重复推荐的股票自动重置15天跟踪期"}})
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "note", "elements": [
                {"tag": "plain_text", "content": f"AI News Radar 自动跟踪 {now_str} 共{total_tracking}只跟踪中"}
            ],
        })

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "\U0001f4e2 微信公众号推荐股票跟踪日报"},
                "template": "green",
            },
            "elements": elements,
        }

        content = json.dumps(card, ensure_ascii=False)
        return await self._send_raw_message(chat_id, content)

    async def send_screener_card(
        self,
        chat_id: str,
        screener_data: list,
    ) -> bool:
        """Send a beautiful card for 十全十美 stock screener results.

        Args:
            chat_id: Feishu group chat_id.
            screener_data: List of dicts with keys:
                name, code, mcap, trn, ch5d, sqsm
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        hour = datetime.now().hour
        mode_label = "早间" if hour < 12 else "盘后"
        inst_label = "昨日机构净买" if hour < 12 else "今日机构净买"

        elements = []

        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**{now_str}**"}})
        elements.append({"tag": "hr"})

        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"\U0001f4ca **十全十美股票推荐（{mode_label}筛选）**"}})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "流通市值100~1000亿  |  换手率>5%  |  5日内有涨停或>7%  |  今日盘中首日9分共振"}})
        elements.append({"tag": "hr"})

        if not screener_data:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "今日无符合条件的十全十美推荐股票"}})
        else:
            for i, s in enumerate(screener_data, 1):
                name_display = s.get("name", "")[:8]
                code_display = s.get("code", "")
                mcap = f"{s.get('mcap', 0):.0f}亿"
                trn = f"{s.get('trn', 0):.1f}%"
                ch5 = float(s.get('ch5d', 0))
                ch5_str = f"{ch5:+.1f}%"
                sqsm = s.get("sqsm", "-/-")
                sqsm_display = f"\U0001f48e{sqsm}" if sqsm == "10/10" else sqsm
                inst = s.get("jnst", "-")
                peak_day = str(s.get("peak_day", ""))[-5:]
                peak_ch = s.get("peak_change", 0)
                peak_str = f"{peak_day} +{peak_ch:.1f}%" if peak_day and peak_ch > 0 else ""

                lines = []
                lines.append(f"\U0001f7e2 **#{i} {name_display}** ({code_display[-6:]})")
                lines.append(f"\U0001f4b0 市值{mcap}  |  \U0001f504 换手{trn}  |  {sqsm_display}")
                lines.append(f"\U0001f4c8 5日累积: {ch5_str}" + (f"  (峰值 {peak_str})" if peak_str else ""))
                lines.append(f"\U0001f3c6 {inst_label}: {inst}")

                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})

        elements.append({"tag": "hr"})
        elements.append({
            "tag": "note", "elements": [
                {"tag": "plain_text", "content": f"AI News Radar 自动筛选 {now_str}  共{len(screener_data)}只推荐"}
            ],
        })

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "\U0001f4ca 十全十美股票推荐"},
                "template": "blue",
            },
            "elements": elements,
        }

        content = json.dumps(card, ensure_ascii=False)
        return await self._send_raw_message(chat_id, content)

    async def send_zlzy_card(self, chat_id: str, zlzy_data: list) -> bool:
        """Send a beautiful card for 主力作妖 stock screener results.

        Args:
            chat_id: Feishu group chat_id.
            zlzy_data: List of dicts with keys:
                name, code, mcap, trn, ch5d, zlzy
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        elements = []

        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**{now_str}**"}})
        elements.append({"tag": "hr"})

        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\U0001f47b **主力作妖股票推荐（早间筛选）**"}})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "无市值条件筛选  |  5日内有涨停或>7%  |  主力作妖信号触发"}})
        elements.append({"tag": "hr"})

        if not zlzy_data:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "今日无符合条件的主力作妖推荐股票"}})
        else:
            for i, s in enumerate(zlzy_data, 1):
                name_display = s.get("name", "")[:8]
                code_display = s.get("code", "")
                mcap = f"{s.get('mcap', 0):.0f}亿"
                trn = f"{s.get('trn', 0):.1f}%"
                ch5 = float(s.get('ch5d', 0))
                ch5_str = f"{ch5:+.1f}%"
                zlzy_sig = s.get("zlzy", "-")
                inst = s.get("jnst", "-")
                peak_day = str(s.get("peak_day", ""))[-5:]
                peak_ch = s.get("peak_change", 0)
                peak_str = f"{peak_day} +{peak_ch:.1f}%" if peak_day and peak_ch > 0 else ""

                lines = []
                lines.append(f"\U0001f7e2 **#{i} {name_display}** ({code_display[-6:]})")
                lines.append(f"\U0001f4b0 市值{mcap}  |  \U0001f504 换手{trn}  |  \U0001f47b作妖:{zlzy_sig}")
                lines.append(f"\U0001f4c8 5日累积: {ch5_str}" + (f"  (峰值 {peak_str})" if peak_str else ""))
                lines.append(f"\U0001f3c6 机构: {inst}")

                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})

        elements.append({"tag": "hr"})
        elements.append({
            "tag": "note", "elements": [
                {"tag": "plain_text", "content": f"AI News Radar 自动筛选 {now_str}  共{len(zlzy_data)}只推荐"}
            ],
        })

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "\U0001f47b 主力作妖股票推荐"},
                "template": "purple",
            },
            "elements": elements,
        }

        content = json.dumps(card, ensure_ascii=False)
        return await self._send_raw_message(chat_id, content)

    async def _send_raw_message(self, chat_id: str, content: str) -> bool:
        """Low-level: send an already-serialized card to a group chat."""
        try:
            token = await self._get_tenant_token()
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{_MSG_URL}?receive_id_type=chat_id",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={"receive_id": chat_id, "msg_type": "interactive", "content": content},
                )
                data = resp.json()
                if resp.status_code == 200 and data.get("code") == 0:
                    logger.info("Card sent to %s...", chat_id[:8])
                    return True
                else:
                    logger.warning("Card failed for %s: code=%s msg=%s", chat_id[:8], data.get("code"), data.get("msg", ""))
                    return False
        except Exception as e:
            logger.error("Error sending card: %s", e)
            return False
