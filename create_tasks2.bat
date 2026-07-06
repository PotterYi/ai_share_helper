@echo off
cd /d G:\develop\project\ai_news_radar

schtasks /delete /tn "AI_News_Radar_WeChat" /f >/dev/null 2>&1
schtasks /delete /tn "AI_News_Radar_Screener_PM" /f >/dev/null 2>&1

schtasks /create /tn "AI_News_Radar_WeChat" /tr "G:\develop\apps\anaconda\python.exe G:\develop\project\ai_news_radar\daily_runner.py wechat_track" /sc daily /st 07:00 /f
schtasks /create /tn "AI_News_Radar_Screener_PM" /tr "G:\develop\apps\anaconda\python.exe G:\develop\project\ai_news_radar\daily_runner.py screener_evening" /sc daily /st 19:30 /f

echo All tasks created
