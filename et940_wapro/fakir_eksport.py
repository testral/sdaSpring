"""Zapis transakcji z wyciągu MT940 do pliku tekstowego (CSV) importowanego w WAPRO Fakir.

Fakir (moduł eBank) wczytuje wyciągi wg zdefiniowanego przez użytkownika schematu
pliku importu: separator, kodowanie, format daty i numer kolumny dla każdego pola.
Ten moduł generuje plik o stałym, przewidywalnym układzie, więc schemat w Fakirze
definiuje się raz i działa on dla wyciągów z każdego banku.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Dict, List

from mt940_parser import Transakcja, Wyciag


@dataclass
class UstawieniaEksportu:
    kodowanie: str = "windows-1250"
    separator: str = ";"
    separator_dziesietny: str = ","
    format_daty: str = "%Y-%m-%d"
    naglowek: bool = True
    kolumny: List[str] = field(default_factory=lambda: list(DOMYSLNE_KOLUMNY))
    max_dlugosc_tekstu: int = 250


def _kwota(k: Decimal, u: UstawieniaEksportu) -> str:
    return f"{k:.2f}".replace(".", u.separator_dziesietny)


# Nazwa kolumny -> (nagłówek, funkcja wyliczająca wartość).
KOLUMNY: Dict[str, Callable[[Wyciag, Transakcja, UstawieniaEksportu], str]] = {
    "rachunek": lambda w, t, u: w.rachunek,
    "nr_wyciagu": lambda w, t, u: w.numer,
    "data_wyciagu": lambda w, t, u: w.data.strftime(u.format_daty) if w.data else "",
    "data_operacji": lambda w, t, u: t.data_ksiegowania.strftime(u.format_daty),
    "data_waluty": lambda w, t, u: t.data_waluty.strftime(u.format_daty),
    "kwota": lambda w, t, u: _kwota(t.kwota, u),
    "kwota_bez_znaku": lambda w, t, u: _kwota(abs(t.kwota), u),
    "wplyw": lambda w, t, u: _kwota(t.kwota, u) if t.kwota >= 0 else "",
    "wyplyw": lambda w, t, u: _kwota(-t.kwota, u) if t.kwota < 0 else "",
    # Strona konta bankowego (np. 130) w księgach firmy: wpływ = Wn, wypływ = Ma.
    "strona": lambda w, t, u: "WN" if t.kwota >= 0 else "MA",
    "znak": lambda w, t, u: "C" if t.kwota >= 0 else "D",
    "waluta": lambda w, t, u: t.waluta,
    "kontrahent": lambda w, t, u: t.kontrahent,
    "adres_kontrahenta": lambda w, t, u: t.adres_kontrahenta,
    "rachunek_kontrahenta": lambda w, t, u: t.rachunek_kontrahenta,
    "tytul": lambda w, t, u: t.tytul,
    "kod_operacji": lambda w, t, u: t.kod_operacji,
    "opis_operacji": lambda w, t, u: t.opis_operacji,
    "kod_typu": lambda w, t, u: t.kod_typu,
    "referencja": lambda w, t, u: t.referencja,
    "referencja_banku": lambda w, t, u: t.referencja_banku,
}

DOMYSLNE_KOLUMNY = [
    "rachunek",
    "nr_wyciagu",
    "data_operacji",
    "data_waluty",
    "kwota",
    "wplyw",
    "wyplyw",
    "waluta",
    "kontrahent",
    "adres_kontrahenta",
    "rachunek_kontrahenta",
    "tytul",
    "kod_operacji",
    "opis_operacji",
    "referencja_banku",
]

_BIALE_ZNAKI = re.compile(r"\s+")


def _oczysc(wartosc: str, u: UstawieniaEksportu) -> str:
    """Usuwa znaki, które rozbiłyby wiersz: separator, cudzysłowy, nowe linie."""
    wartosc = wartosc.replace(u.separator, " ").replace('"', "'")
    wartosc = _BIALE_ZNAKI.sub(" ", wartosc).strip()
    return wartosc[: u.max_dlugosc_tekstu]


def wiersze(wyciag: Wyciag, u: UstawieniaEksportu) -> List[str]:
    nieznane = [k for k in u.kolumny if k not in KOLUMNY]
    if nieznane:
        raise ValueError(f"Nieznane kolumny w konfiguracji: {', '.join(nieznane)}")
    wynik = []
    if u.naglowek:
        wynik.append(u.separator.join(u.kolumny))
    for t in wyciag.transakcje:
        wynik.append(u.separator.join(_oczysc(KOLUMNY[k](wyciag, t, u), u) for k in u.kolumny))
    return wynik


def nazwa_pliku(wyciag: Wyciag, nazwa_zrodla: str) -> str:
    baza = os.path.splitext(os.path.basename(nazwa_zrodla))[0]
    rachunek = wyciag.rachunek[-8:] if wyciag.rachunek else "brak"
    numer = re.sub(r"[^0-9A-Za-z]+", "-", wyciag.numer).strip("-") or "0"
    data = wyciag.data.strftime("%Y%m%d") if wyciag.data else "bezdaty"
    return f"{baza}_{rachunek}_{data}_{numer}.csv"


def zapisz(wyciag: Wyciag, sciezka: str, u: UstawieniaEksportu) -> None:
    tresc = "\r\n".join(wiersze(wyciag, u)) + "\r\n"
    tymczasowy = sciezka + ".tmp"
    with open(tymczasowy, "w", encoding=u.kodowanie, errors="replace", newline="") as f:
        f.write(tresc)
    os.replace(tymczasowy, sciezka)
