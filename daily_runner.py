"""Windows Scheduled Task wrapper - runs tasks as subprocesses."""
import sys, subprocess, os, asyncio, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
CHAT_ID = "oc_8792267760e09f7c142bb0157bcf22f0"
CLI = [PYTHON, "-m", "ai_news_radar.cli"]
LOG = os.path.join(BASE, "logs", "task_runner.log")
RUN_SCREENER = os.path.join(BASE, "run_screener.py")
RUN_ZLZY = os.path.join(BASE, "run_zlzy.py")

def log(msg):
    try:
        os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except: pass
    try: print(msg)
    except: pass

async def main():
    task = sys.argv[1] if len(sys.argv) > 1 else ""
    log(f"Task: {task}")

    if task == "zlzy_evening":
        log("ZLZY evening...")
        r = subprocess.run([PYTHON, RUN_ZLZY], timeout=1800, cwd=BASE)
        log(f"Done: ret={r.returncode}")
        if r.returncode != 0:
            cmd = [PYTHON, "-c",
                   "import asyncio; from ai_news_radar.feishu_client import FeishuClient; "
                   f"asyncio.run(FeishuClient().send_zlzy_card(chat_id='{CHAT_ID}', zlzy_data=[]))"]
            subprocess.run(cmd, cwd=BASE, timeout=30)

    elif task == "screener_evening":
        log("Screener evening...")
        r = subprocess.run([PYTHON, RUN_SCREENER, "evening"], timeout=1800, cwd=BASE)
        log(f"Done: ret={r.returncode}")
        # Send empty card on failure
        if r.returncode != 0:
            cmd = [PYTHON, "-c",
                   "import asyncio; from ai_news_radar.feishu_client import FeishuClient; "
                   f"asyncio.run(FeishuClient().send_screener_card(chat_id='{CHAT_ID}', screener_data=[]))"]
            subprocess.run(cmd, cwd=BASE, timeout=30)

    elif task == "wechat_track":
        r = subprocess.run([*CLI, "wechat-track"], timeout=600, cwd=BASE)
        log(f"WeChat: ret={r.returncode}")
        sys.exit(0)
    else:
        log(f"Unknown: {task}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
