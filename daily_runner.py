"""Windows Scheduled Task wrapper - runs tasks as subprocesses.
Logs auto-rotate at 512KB (keep 3 backups).
Failed tasks send a Feishu alert to the group chat.
"""
import sys, subprocess, os, asyncio, datetime, logging, logging.handlers

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
CHAT_ID = "oc_8792267760e09f7c142bb0157bcf22f0"
CLI = [PYTHON, "-m", "ai_news_radar.cli"]
LOG = os.path.join(BASE, "logs", "task_runner.log")
RUN_SCREENER = os.path.join(BASE, "run_screener.py")
RUN_ZLZY = os.path.join(BASE, "run_zlzy.py")
RUN_STRATEGY_TRACK = os.path.join(BASE, "run_strategy_track.py")
RUN_STRATEGY_REPORT = os.path.join(BASE, "run_strategy_report.py")
RUN_ZLZY_TRACK = os.path.join(BASE, "run_zlzy_track.py")


_CLEANUP_SCRIPT = (
    "import asyncio, sys; sys.path.insert(0, '.'); "
    "from ai_news_radar.database import Database; db = Database(); "
    "stats = db.cleanup_old_data(); print(f'Cleanup: {stats}'); db.close()"
)


def _setup_rotating_log():
    """Setup rotating file handler for task_runner.log."""
    os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
    logger = logging.getLogger("task_runner")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            LOG, maxBytes=524_288, backupCount=3, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(handler)
    return logger


_runner_log = _setup_rotating_log()


def log(msg):
    try:
        _runner_log.info(msg)
    except:
        pass
    try:
        print(msg)
    except:
        pass


def _notify_failure(task_name: str, retcode: int):
    """Send a failure alert to Feishu group."""
    try:
        import json, urllib.request
        from ai_news_radar.feishu_client import FeishuClient
        import asyncio
        msg = (f"⚠️ **定时任务运行失败**\n"
               f"任务: {task_name}\n"
               f"返回码: {retcode}\n"
               f"时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
        async def _alert():
            fc = FeishuClient()
            if fc.is_configured:
                await fc.send_group_message(chat_id=CHAT_ID, report=msg, title="任务失败告警", template="red")
        asyncio.run(_alert())
    except:
        pass


def _run_and_check(task_name: str, args: list, timeout: int = 1800):
    """Run a subprocess, check return code, alert on failure."""
    log(f"{task_name}...")
    r = subprocess.run(args, timeout=timeout, cwd=BASE)
    log(f"Done: ret={r.returncode}")
    if r.returncode != 0:
        _notify_failure(task_name, r.returncode)
    return r.returncode

async def main():
    task = sys.argv[1] if len(sys.argv) > 1 else ""
    log(f"Task: {task}")

    if task == "zlzy_evening":
        _run_and_check("ZLZY evening", [PYTHON, RUN_ZLZY])

    elif task == "screener_evening":
        _run_and_check("Screener evening", [PYTHON, RUN_SCREENER, "evening"])

    elif task == "zlzy_track":
        _run_and_check("ZLZY track pool", [PYTHON, RUN_ZLZY_TRACK], timeout=600)

    elif task == "strategy_track":
        _run_and_check("Strategy signal tracking", [PYTHON, RUN_STRATEGY_TRACK], timeout=600)

    elif task == "strategy_report":
        _run_and_check("Strategy weekly report", [PYTHON, RUN_STRATEGY_REPORT], timeout=600)

    elif task == "strategy_cleanup":
        _run_and_check("DB cleanup & VACUUM", [PYTHON, "-c", _CLEANUP_SCRIPT], timeout=300)

    elif task == "wechat_track":
        r = subprocess.run([*CLI, "wechat-track"], timeout=600, cwd=BASE)
        log(f"WeChat: ret={r.returncode}")
        sys.exit(0)
    else:
        log(f"Unknown: {task}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
