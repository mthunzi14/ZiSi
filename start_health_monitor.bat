@echo off
cd /d C:\Users\mthun\Downloads\ZiSi_Bot
echo Starting ZiSi Health Monitor...
start "ZiSi Health Monitor" python health_monitor.py
echo Health monitor started. Check health_monitor.log for output.
