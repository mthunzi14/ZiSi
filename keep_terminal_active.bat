@echo off
REM This keeps laptop awake (no sleep)
REM Run once at startup

REM Prevent screen from turning off
powercfg /change monitor-timeout-ac 0
powercfg /change monitor-timeout-dc 0

REM Prevent sleep
powercfg /change disk-timeout-ac 0
powercfg /change disk-timeout-dc 0
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0

echo ✅ Power settings configured - laptop will NOT sleep
pause