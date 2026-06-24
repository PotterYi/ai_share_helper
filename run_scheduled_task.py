"""
Windows Scheduled Task wrapper — calls existing CLI commands.
Usage: python run_scheduled_task.py <task_name>
"""
import subprocess, sys, os

BASE = [sys.executable, '-m', 'ai_news_radar.cli']
CHAT_ID = 'oc_8792267760e09f7c142bb0157bcf22f0'
PY = sys.executable
SCRIPT = os.path.join(os.path.dirname(__file__), 'run_scheduled_task.py')

TASKS = {
    'wechat_track':      [*BASE, 'wechat-track'],
    'screener_morning':  [PY, SCRIPT, '_run_screener_inner', 'morning'],
    'screener_evening':  [PY, SCRIPT, '_run_screener_inner', 'evening'],
}

async def run_screener(mode):
    from ai_news_radar.stock_screener import run_daily_screener
    from ai_news_radar.feishu_client import FeishuClient
    label = '早间' if mode == 'morning' else '盘后'
    print(f'  十全十美{label}筛选...')
    top10 = await run_daily_screener(mode=mode)
    fc = FeishuClient()
    if fc.is_configured and top10:
        ok = await fc.send_screener_card(chat_id=CHAT_ID, screener_data=top10)
        print(f'  {"OK" if ok else "FAIL"} {label}')
    elif not top10:
        print('  无筛选结果')

import asyncio

async def main():
    task = sys.argv[1] if len(sys.argv) > 1 else ''
    print(f'Task: {task}')
    if task in TASKS:
        if 'screener' in task:
            mode = 'morning' if 'morning' in task else 'evening'
            await run_screener(mode)
        else:
            r = subprocess.run(TASKS[task], cwd=os.path.dirname(__file__))
            sys.exit(r.returncode)
    elif task == '_run_screener_inner':
        await run_screener(sys.argv[2])
    else:
        print(f'Unknown: {task}'); sys.exit(1)
    print('Done')

if __name__ == '__main__':
    asyncio.run(main())
