@echo off
chcp 65001 >NUL
cd /d "%~dp0"
title Painel Kira Game
python painel.py
if errorlevel 1 pause
