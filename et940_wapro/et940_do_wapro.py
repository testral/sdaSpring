#!/usr/bin/env python3
"""Automatyczna konwersja wyciągów MT940 do plików importu WAPRO Fakir.

Przykłady:
    python et940_do_wapro.py                      # przetwarza katalog wejściowy z config.ini
    python et940_do_wapro.py wyciag.sta           # przetwarza wskazane pliki
    python et940_do_wapro.py --obserwuj 60        # sprawdza katalog co 60 sekund
    python et940_do_wapro.py --podglad wyciag.sta # tylko wypisuje transakcje, nic nie zapisuje
"""

from __future__ import annotations

import argparse
import configparser
import fnmatch
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fakir_eksport  # noqa: E402
from mt940_parser import BladMT940, parsuj_plik  # noqa: E402

KATALOG_SKRYPTU = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger("et940_wapro")


class Konfiguracja:
    def __init__(self, sciezka: Optional[str]):
        cp = configparser.ConfigParser(interpolation=None)
        cp.BOOLEAN_STATES = {**cp.BOOLEAN_STATES, "tak": True, "nie": False, "t": True, "n": False}
        if sciezka:
            if not cp.read(sciezka, encoding="utf-8"):
                raise SystemExit(f"Nie można odczytać pliku konfiguracji: {sciezka}")
            baza = os.path.dirname(os.path.abspath(sciezka))
        else:
            baza = KATALOG_SKRYPTU

        def katalog(klucz: str, domyslny: str) -> str:
            wartosc = cp.get("katalogi", klucz, fallback=domyslny)
            return os.path.normpath(os.path.join(baza, wartosc))

        self.wejscie = katalog("wejscie", "wejscie")
        self.wyjscie = katalog("wyjscie", "do_fakira")
        self.archiwum = katalog("archiwum", "archiwum")
        self.bledne = katalog("bledne", "bledne")
        self.logi = katalog("logi", "logi")

        self.kodowanie_wejscia = cp.get("wejscie", "kodowanie", fallback="auto")
        self.maski = [m.strip() for m in cp.get("wejscie", "maski", fallback="*.sta;*.940;*.mt940;*.txt").split(";") if m.strip()]
        self.pomijaj_przetworzone = cp.getboolean("wejscie", "pomijaj_przetworzone", fallback=True)
        self.blad_przy_niezgodnym_saldzie = cp.getboolean("wejscie", "blad_przy_niezgodnym_saldzie", fallback=True)

        kolumny = cp.get("wyjscie", "kolumny", fallback=",".join(fakir_eksport.DOMYSLNE_KOLUMNY))
        separator = cp.get("wyjscie", "separator", fallback=";")
        if separator.lower() in ("tab", "\\t"):
            separator = "\t"
        self.eksport = fakir_eksport.UstawieniaEksportu(
            kodowanie=cp.get("wyjscie", "kodowanie", fallback="windows-1250"),
            separator=separator,
            separator_dziesietny=cp.get("wyjscie", "separator_dziesietny", fallback=","),
            format_daty=cp.get("wyjscie", "format_daty", fallback="%Y-%m-%d"),
            naglowek=cp.getboolean("wyjscie", "naglowek", fallback=True),
            kolumny=[k.strip() for k in kolumny.replace("\n", ",").split(",") if k.strip()],
            max_dlugosc_tekstu=cp.getint("wyjscie", "max_dlugosc_tekstu", fallback=250),
        )


def skonfiguruj_logi(katalog: str) -> None:
    os.makedirs(katalog, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    plik = logging.FileHandler(os.path.join(katalog, "et940_wapro.log"), encoding="utf-8")
    plik.setFormatter(fmt)
    konsola = logging.StreamHandler()
    konsola.setFormatter(fmt)
    for stary in log.handlers:
        stary.close()
    log.handlers[:] = [plik, konsola]
    log.setLevel(logging.INFO)


def skrot_pliku(sciezka: str) -> str:
    h = hashlib.sha256()
    with open(sciezka, "rb") as f:
        for blok in iter(lambda: f.read(65536), b""):
            h.update(blok)
    return h.hexdigest()


class Rejestr:
    """Rejestr przetworzonych plików (po sumie SHA-256), chroni przed podwójnym importem."""

    def __init__(self, sciezka: str):
        self.sciezka = sciezka
        try:
            with open(sciezka, encoding="utf-8") as f:
                self.dane = json.load(f)
        except FileNotFoundError:
            self.dane = {}

    def zawiera(self, skrot: str) -> Optional[dict]:
        return self.dane.get(skrot)

    def dodaj(self, skrot: str, wpis: dict) -> None:
        self.dane[skrot] = wpis
        tymczasowy = self.sciezka + ".tmp"
        with open(tymczasowy, "w", encoding="utf-8") as f:
            json.dump(self.dane, f, ensure_ascii=False, indent=1)
        os.replace(tymczasowy, self.sciezka)


def przenies(sciezka: str, katalog: str) -> str:
    os.makedirs(katalog, exist_ok=True)
    cel = os.path.join(katalog, os.path.basename(sciezka))
    if os.path.exists(cel):
        baza, ext = os.path.splitext(cel)
        cel = f"{baza}_{datetime.now():%Y%m%d_%H%M%S}{ext}"
    shutil.move(sciezka, cel)
    return cel


def przetworz_plik(sciezka: str, k: Konfiguracja, rejestr: Rejestr, przenos: bool) -> bool:
    nazwa = os.path.basename(sciezka)
    skrot = skrot_pliku(sciezka)
    poprzedni = rejestr.zawiera(skrot)
    if poprzedni and k.pomijaj_przetworzone:
        log.warning("%s: plik był już przetworzony %s (jako %s) - pomijam", nazwa, poprzedni["kiedy"], poprzedni["plik"])
        if przenos:
            przenies(sciezka, k.archiwum)
        return True

    try:
        wyciagi = parsuj_plik(sciezka, k.kodowanie_wejscia)
        for w in wyciagi:
            problem = w.sprawdz_salda()
            if problem:
                if k.blad_przy_niezgodnym_saldzie:
                    raise BladMT940(f"niezgodne salda wyciągu {w.numer}: {problem}")
                log.warning("%s: wyciąg %s - niezgodne salda: %s", nazwa, w.numer, problem)

        os.makedirs(k.wyjscie, exist_ok=True)
        wynikowe = []
        for w in wyciagi:
            cel = os.path.join(k.wyjscie, fakir_eksport.nazwa_pliku(w, nazwa))
            fakir_eksport.zapisz(w, cel, k.eksport)
            wynikowe.append(os.path.basename(cel))
            wplywy = sum((t.kwota for t in w.transakcje if t.kwota >= 0), Decimal(0))
            wyplywy = -sum((t.kwota for t in w.transakcje if t.kwota < 0), Decimal(0))
            log.info(
                "%s: rachunek %s, wyciąg %s, %d transakcji (wpływy %s, wypływy %s %s) -> %s",
                nazwa, w.rachunek, w.numer or "-", len(w.transakcje), wplywy, wyplywy, w.waluta, os.path.basename(cel),
            )
    except (BladMT940, ValueError, OSError) as e:
        log.error("%s: BŁĄD - %s", nazwa, e)
        if przenos:
            log.error("%s: przeniesiono do %s", nazwa, przenies(sciezka, k.bledne))
        return False

    rejestr.dodaj(skrot, {"plik": nazwa, "kiedy": f"{datetime.now():%Y-%m-%d %H:%M:%S}", "wynik": wynikowe})
    if przenos:
        przenies(sciezka, k.archiwum)
    return True


def pliki_w_katalogu(k: Konfiguracja) -> List[str]:
    if not os.path.isdir(k.wejscie):
        os.makedirs(k.wejscie, exist_ok=True)
        return []
    wynik = []
    for nazwa in sorted(os.listdir(k.wejscie)):
        sciezka = os.path.join(k.wejscie, nazwa)
        if not (os.path.isfile(sciezka) and any(fnmatch.fnmatch(nazwa.lower(), m.lower()) for m in k.maski)):
            continue
        # Plik zmieniony przed chwilą może być jeszcze zapisywany (np. przez bankowość) - poczekaj.
        if time.time() - os.path.getmtime(sciezka) < 5:
            continue
        wynik.append(sciezka)
    return wynik


def podglad(pliki: List[str], k: Konfiguracja) -> int:
    for sciezka in pliki:
        for w in parsuj_plik(sciezka, k.kodowanie_wejscia):
            print(f"== {os.path.basename(sciezka)} | rachunek {w.rachunek} | wyciąg {w.numer} | {w.waluta}")
            if w.saldo_poczatkowe and w.saldo_koncowe:
                print(f"   saldo początkowe {w.saldo_poczatkowe.kwota}, końcowe {w.saldo_koncowe.kwota}")
            problem = w.sprawdz_salda()
            if problem:
                print(f"   UWAGA: niezgodne salda: {problem}")
            for t in w.transakcje:
                print(f"   {t.data_ksiegowania} {t.kwota:>14} | {t.kontrahent[:35]:35} | {t.tytul[:60]}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Konwersja wyciągów MT940 do plików importu WAPRO Fakir")
    parser.add_argument("pliki", nargs="*", help="pliki MT940 (domyślnie: wszystkie z katalogu wejściowego)")
    parser.add_argument("-c", "--config", help="plik konfiguracji (domyślnie config.ini obok skryptu)")
    parser.add_argument("--obserwuj", type=int, metavar="SEKUNDY", help="działaj w pętli i sprawdzaj katalog co SEKUNDY")
    parser.add_argument("--podglad", action="store_true", help="tylko pokaż odczytane transakcje")
    parser.add_argument("--wymus", action="store_true", help="przetwórz także pliki już wcześniej przetworzone")
    args = parser.parse_args(argv)

    config = args.config
    if config is None and os.path.exists(os.path.join(KATALOG_SKRYPTU, "config.ini")):
        config = os.path.join(KATALOG_SKRYPTU, "config.ini")
    k = Konfiguracja(config)
    if args.wymus:
        k.pomijaj_przetworzone = False

    if args.podglad:
        return podglad(args.pliki or pliki_w_katalogu(k), k)

    skonfiguruj_logi(k.logi)
    rejestr = Rejestr(os.path.join(k.logi, "przetworzone.json"))

    if args.pliki:
        # Pliki wskazane ręcznie nie są przenoszone do archiwum.
        ok = all([przetworz_plik(p, k, rejestr, przenos=False) for p in args.pliki])
        return 0 if ok else 1

    while True:
        pliki = pliki_w_katalogu(k)
        if not pliki and not args.obserwuj:
            log.info("Brak nowych plików w %s", k.wejscie)
        ok = all([przetworz_plik(p, k, rejestr, przenos=True) for p in pliki])
        if not args.obserwuj:
            return 0 if ok else 1
        time.sleep(args.obserwuj)


if __name__ == "__main__":
    sys.exit(main())
