"""Weekly strategy performance report.
Runs Saturday 15:00. Generates and pushes report to Feishu group."""
import sys, asyncio
from datetime import datetime

CHAT_ID = "oc_8792267760e09f7c142bb0157bcf22f0"

async def run():
    from ai_news_radar.strategy_tracker import get_weekly_report
    from ai_news_radar.feishu_client import FeishuClient

    report = get_weekly_report()
    print(f"Weekly report: {report['overall']['total']} signals total")
    print(f"  SQSM: {report['sqsm']['total']} signals, win rate {report['sqsm']['win_rate']}%")
    print(f"  ZLZY: {report['zlzy']['total']} signals, win rate {report['zlzy']['win_rate']}%")

    # Add strategy name keys for the card
    for s in report.get('all_signals', []):
        s['strategy'] = s.get('strategy_type', '')
    report['sqsm_signals'] = [s for s in report.get('all_signals', []) if s.get('strategy_type') == 'sqsm']
    report['zlzy_signals'] = [s for s in report.get('all_signals', []) if s.get('strategy_type') == 'zlzy']

    fc = FeishuClient()
    if fc.is_configured:
        ok = await fc.send_strategy_report_card(chat_id=CHAT_ID, report_data=report)
        print(f"Report sent: {ok}")
    else:
        print("Feishu not configured")

if __name__ == "__main__":
    asyncio.run(run())
