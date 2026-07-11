"""ZLZY tracking pool — 每日16:00推送主力捉妖跟踪池到群。"""
import sys, asyncio

CHAT_ID = "oc_8792267760e09f7c142bb0157bcf22f0"

async def run():
    from ai_news_radar.zlzy_tracker import get_zlzy_tracking_pool
    from ai_news_radar.feishu_client import FeishuClient
    
    pool = get_zlzy_tracking_pool()
    print(f"ZLZY tracking pool: {pool['total']} signals, wins={pool['wins']}, losses={pool['losses']}")
    
    fc = FeishuClient()
    if fc.is_configured:
        ok = await fc.send_zlzy_track_card(chat_id=CHAT_ID, pool_data=pool)
        print(f"ZLZY track card sent: {ok}")
    else:
        print("Feishu not configured")

if __name__ == "__main__":
    asyncio.run(run())
