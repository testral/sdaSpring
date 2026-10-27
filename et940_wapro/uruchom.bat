@echo off
rem Przetwarza wszystkie pliki MT940 z katalogu "wejscie" (do użycia w Harmonogramie zadań Windows)
cd /d "%~dp0"
py -3 et940_do_wapro.py %*
if errorlevel 1 (
  echo.
  echo Wystapily bledy - sprawdz logi\et940_wapro.log
)
