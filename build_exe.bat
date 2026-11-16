@echo off
chcp 65001 >NUL
cd /d "%~dp0"
title Gerando PainelKiraGame.exe
echo.
echo Gerando o executavel do Painel Kira Game...
echo (usa o mesmo ambiente da ferramenta: Python + hacktools)
echo.
pipenv --version >NUL 2>&1
if errorlevel 1 (
  echo [ERRO] pipenv nao encontrado. Rode o run_windows.bat uma vez antes.
  pause
  exit /b 1
)
echo Instalando o PyInstaller no ambiente da ferramenta...
pipenv run pip install --quiet --upgrade pyinstaller
if errorlevel 1 goto erro
echo.
echo Empacotando (pode levar alguns minutos)...
pipenv run pyinstaller --noconfirm --clean --onedir --name PainelKiraGame ^
  --distpath . --workpath build_exe --specpath build_exe ^
  --collect-all hacktools --collect-all ndspy --collect-all pyarmips --collect-all pyxdelta ^
  --collect-submodules PIL --hidden-import click --hidden-import tqdm --hidden-import colorsys ^
  --exclude-module auto ^
  --hidden-import zlib --hidden-import struct --hidden-import datetime --hidden-import zipfile ^
  --hidden-import tempfile --hidden-import csv --hidden-import difflib --hidden-import hashlib ^
  --hidden-import html --hidden-import uuid --hidden-import statistics --hidden-import sqlite3 ^
  --hidden-import glob --hidden-import fnmatch --hidden-import configparser --hidden-import decimal ^
  --hidden-import fractions --hidden-import random --hidden-import secrets --hidden-import gzip ^
  --hidden-import tarfile --hidden-import wave --hidden-import xml.etree.ElementTree ^
  --hidden-import unicodedata --hidden-import locale --hidden-import platform ^
  --hidden-import getpass --hidden-import concurrent.futures --hidden-import http.client ^
  --hidden-import urllib.request --hidden-import email --hidden-import ssl --hidden-import string ^
  --hidden-import textwrap --hidden-import pprint --hidden-import copy --hidden-import math ^
  --hidden-import itertools --hidden-import functools --hidden-import collections ^
  --hidden-import heapq --hidden-import bisect ^
  painel.py
if errorlevel 1 goto erro
rmdir /s /q build_exe 2>NUL
echo.
set "VER=?"
for /f "tokens=2 delims==" %%v in ('findstr /b /c:"VERSAO =" painel.py') do set "VER=%%~v"
set "VER=%VER: =%"
set "VER=%VER:"=%"
echo ============================================================
echo  Pronto! Painel Kira Game versao %VER%
echo  Abra: PainelKiraGame\PainelKiraGame.exe
echo  Daqui pra frente, pra atualizar o painel basta trocar os
echo  arquivos .py na pasta do projeto e abrir o programa de novo.
echo  Pra levar pra outro PC, copie a pasta do projeto inteira
echo  (ela ja inclui a pasta PainelKiraGame).
echo ============================================================
pause
exit /b 0
:erro
echo.
echo [ERRO] Algo deu errado. Copie as mensagens acima e mande pro Claude.
pause
exit /b 1
