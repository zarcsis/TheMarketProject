@echo off
rem Starts the Telegram remote at Windows logon.
rem Put a shortcut to this file into the Startup folder (Win+R, then: shell:startup).
cd /d "%~dp0.."
uv run mb remote start
