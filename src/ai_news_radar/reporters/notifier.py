"""
Notification dispatcher for Telegram, Email, and Webhook.
"""

import logging
from typing import Optional

from ..config import get_telegram_config, get_email_config, get_notifications_config
from ..models import DailyReport, Article

logger = logging.getLogger(__name__)


class Notifier:
    """Send notifications via configured channels."""

    def __init__(self):
        self.config = get_notifications_config()
        self.notif_config = self.config.get("notifications", {})

    async def send_daily_report(self, report: DailyReport) -> dict[str, bool]:
        """Send daily report to all enabled channels."""
        results = {}

        # Always output to terminal
        results["terminal"] = True

        # Telegram
        if self.notif_config.get("telegram", {}).get("enabled"):
            try:
                results["telegram"] = await self._send_telegram(
                    self._format_for_telegram(report)
                )
            except Exception as e:
                logger.error("Telegram send failed: %s", e)
                results["telegram"] = False

        # Email
        if self.notif_config.get("email", {}).get("enabled"):
            try:
                results["email"] = await self._send_email(report)
            except Exception as e:
                logger.error("Email send failed: %s", e)
                results["email"] = False

        # Webhook
        if self.notif_config.get("webhook", {}).get("enabled"):
            try:
                results["webhook"] = await self._send_webhook(
                    self._format_for_webhook(report)
                )
            except Exception as e:
                logger.error("Webhook send failed: %s", e)
                results["webhook"] = False

        return results

    async def send_breaking_news(self, article: Article) -> bool:
        """Send immediate notification for breaking news."""
        message = (
            "Breaking AI News! "
            f"Importance: {article.importance:.0%}\n"
            f"Title: {article.title}\n"
            f"Source: {article.source_type.value}\n"
            f"Link: {article.url}"
        )
        if article.summary:
            message += "\nSummary: " + article.summary

        sent = False
        if self.notif_config.get("telegram", {}).get("on_breaking_news"):
            try:
                sent = await self._send_telegram(message)
            except Exception:
                pass
        return sent

    async def _send_telegram(self, message: str) -> bool:
        """Send message via Telegram Bot API."""
        import httpx

        config = get_telegram_config()
        token = config["bot_token"]
        chat_id = config["chat_id"]

        if not token or not chat_id:
            logger.warning("Telegram not configured (missing token or chat_id)")
            return False

        # Truncate if too long (Telegram limit: 4096)
        if len(message) > 4000:
            message = message[:4000] + "\n\n... (truncated)"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": False,
                },
            )
            if resp.status_code == 200:
                logger.info("Telegram notification sent")
                return True
            else:
                logger.error(
                    "Telegram API error: %d - %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return False

    async def _send_email(self, report: DailyReport) -> bool:
        """Send report via email (SMTP)."""
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        config = get_email_config()

        if not config["user"] or not config["to_address"]:
            logger.warning("Email not configured")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[AI News Radar] {report.title}"
        msg["From"] = config["from_address"] or config["user"]
        msg["To"] = config["to_address"]

        # Plain text fallback + HTML
        text_part = MIMEText(report.content, "plain", "utf-8")
        html_content = self._markdown_to_html(report.content)
        html_part = MIMEText(html_content, "html", "utf-8")
        msg.attach(text_part)
        msg.attach(html_part)

        try:
            with smtplib.SMTP(config["smtp_host"], config["smtp_port"]) as server:
                server.starttls()
                server.login(config["user"], config["password"])
                server.send_message(msg)
            logger.info("Email sent to %s", config["to_address"])
            return True
        except Exception as e:
            logger.error("Email send failed: %s", e)
            return False

    async def _send_webhook(self, message: str) -> bool:
        """Send message via webhook (WeChat Work, DingTalk, Lark, etc.)."""
        import httpx

        url = self.notif_config.get("webhook", {}).get("url", "")
        if not url:
            logger.warning("Webhook not configured")
            return False

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json={"msgtype": "markdown", "markdown": {"content": message}},
            )
            if resp.status_code == 200:
                logger.info("Webhook notification sent")
                return True
            else:
                logger.error("Webhook error: %d", resp.status_code)
                return False

    def _format_for_telegram(self, report: DailyReport) -> str:
        """Format report for Telegram (limited Markdown support)."""
        lines = [
            "*" + report.title + "*",
            "",
            "_Generated at " + report.generated_at.strftime("%Y-%m-%d %H:%M") + "_",
            "_Total: " + str(report.article_count) + " articles_",
            "",
        ]

        if report.top_articles:
            lines.append("*Top Stories:*")
            for i, article in enumerate(report.top_articles[:10]):
                title = article.title.replace("*", "").replace("_", "")
                lines.append(
                    str(i + 1) + ". [" + title + "](" + article.url + ") "
                    "(" + article.source_type.value + ", imp: "
                    + format(article.importance, ".0%") + ")"
                )

        return "\n".join(lines)

    def _format_for_webhook(self, report: DailyReport) -> str:
        """Format report for webhook platforms."""
        return self._format_for_telegram(report)

    def _markdown_to_html(self, markdown_text: str) -> str:
        """Simple Markdown to HTML conversion."""
        try:
            import markdown
            return markdown.markdown(markdown_text)
        except ImportError:
            # Basic fallback
            html = markdown_text.replace("\n\n", "</p><p>").replace("\n", "<br>")
            return "<html><body><p>" + html + "</p></body></html>"
