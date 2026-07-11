"""Weekly strategy performance report.
Runs Saturday 15:00. Generates and pushes report to Feishu group."""
import sys, asyncio
from datetime import datetime

CHAT_ID = "oc_8792267760e09f7c142bb0157bcf22f0"

async def run():
    from ai_news_radar.strategy_tracker import get_weekly_report
    from ai_news_radar.feishu_client import FeishuClient

    report = get_weekly_report()
    print(f"Weekly SQSM report: {report['sqsm']['total']} signals, win rate {report['sqsm']['win_rate']}%")

    fc = FeishuClient()
    if fc.is_configured:
        ok = await fc.send_strategy_report_card(chat_id=CHAT_ID, report_data=report)
        print(f"Report sent: {ok}")
    else:
        print("Feishu not configured")

if __name__ == "__main__":
    asyncio.run(run())
