"""Strategy signal daily tracking updater.
Runs daily at 19:05 to update prices for all active signals."""
import sys, logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')

def main():
    from ai_news_radar.strategy_tracker import update_daily_tracking
    stats = update_daily_tracking()
    print(f"Tracking done: {stats['updated']} updated, {stats['errors']} errors, {stats['expired']} expired")
    if stats['active'] == 0:
        print("No active signals to track")

if __name__ == "__main__":
    main()
