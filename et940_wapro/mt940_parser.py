"""Parser wyciągów bankowych w formacie MT940 (SWIFT) z obsługą wariantów polskich banków.

Obsługiwane elementy:
  * koperta SWIFT ({1:...}{2:...}{4: ... -}) lub sam blok 4,
  * wiele wyciągów w jednym pliku,
  * pola :20:, :25:, :28C:, :60F:/:60M:, :61:, :86:, :62F:/:62M:, :64:,
  * pole :86: w formacie strukturalnym ZBP (podpola ~NN, ^NN, <NN, ?NN)
    oraz w formacie niestrukturalnym (czysty tekst),
  * kodowania: UTF-8, Windows-1250, CP852, ISO-8859-2 (wykrywane automatycznie).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

POLSKIE_ZNAKI = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"
KODOWANIA_KANDYDACI = ("utf-8", "windows-1250", "cp852", "iso-8859-2")

TAG_RE = re.compile(r"^:(\d{2}[A-Z]?|NS):(.*)$")
SALDO_RE = re.compile(r"^([CD])(\d{6})([A-Z]{3})(\d+(?:,\d*)?)")
TRANSAKCJA_RE = re.compile(
    r"^(?P<data_waluty>\d{6})"
    r"(?P<data_ksiegowania>\d{4})?"
    r"(?P<znak>RC|RD|EC|ED|C|D)"
    r"(?P<kod_funduszy>[A-Z])?"
    r"(?P<kwota>\d+(?:,\d*)?)"
    r"(?P<typ>[NSF][A-Z0-9]{3})"
    r"(?P<referencja>[^\n]*?)"
    r"(?://(?P<referencja_banku>[^\n]*))?"
    r"(?:\n(?P<dodatkowe>.*))?$",
    re.DOTALL,
)
PODPOLA_86_RE = re.compile(r"^(\d{3})([~^<?])(\d{2})")


class BladMT940(ValueError):
    """Plik nie jest poprawnym wyciągiem MT940."""


@dataclass
class Saldo:
    data: date
    waluta: str
    kwota: Decimal  # ze znakiem: ujemne = saldo debetowe


@dataclass
class Transakcja:
    data_waluty: date
    data_ksiegowania: date
    kwota: Decimal  # ze znakiem: dodatnia = uznanie (wpływ), ujemna = obciążenie (wypływ)
    waluta: str
    kod_typu: str = ""
    referencja: str = ""
    referencja_banku: str = ""
    kod_operacji: str = ""
    opis_operacji: str = ""
    tytul: str = ""
    kontrahent: str = ""
    adres_kontrahenta: str = ""
    rachunek_kontrahenta: str = ""
    szczegoly_86: str = ""
    podpola_86: Dict[str, str] = field(default_factory=dict)

    @property
    def uznanie(self) -> bool:
        return self.kwota >= 0


@dataclass
class Wyciag:
    referencja: str = ""
    rachunek: str = ""
    numer: str = ""
    saldo_poczatkowe: Optional[Saldo] = None
    saldo_koncowe: Optional[Saldo] = None
    saldo_dostepne: Optional[Saldo] = None
    transakcje: List[Transakcja] = field(default_factory=list)

    @property
    def waluta(self) -> str:
        for s in (self.saldo_poczatkowe, self.saldo_koncowe):
            if s:
                return s.waluta
        return "PLN"

    @property
    def data(self) -> Optional[date]:
        if self.saldo_koncowe:
            return self.saldo_koncowe.data
        if self.transakcje:
            return max(t.data_ksiegowania for t in self.transakcje)
        return None

    def sprawdz_salda(self) -> Optional[str]:
        """Zwraca opis niezgodności sald albo None, gdy wszystko się zgadza lub brak sald."""
        if not (self.saldo_poczatkowe and self.saldo_koncowe):
            return None
        oczekiwane = self.saldo_poczatkowe.kwota + sum((t.kwota for t in self.transakcje), Decimal(0))
        if oczekiwane != self.saldo_koncowe.kwota:
            return (
                f"saldo początkowe {self.saldo_poczatkowe.kwota} + obroty "
                f"= {oczekiwane}, a saldo końcowe w pliku = {self.saldo_koncowe.kwota}"
            )
        return None


def dekoduj(dane: bytes, kodowanie: str = "auto") -> str:
    if kodowanie and kodowanie.lower() != "auto":
        return dane.decode(kodowanie, errors="replace")
    if dane.startswith(b"\xef\xbb\xbf"):
        return dane[3:].decode("utf-8", errors="replace")
    try:
        return dane.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # Wybieramy kodowanie, w którym pojawia się najwięcej polskich liter.
    najlepszy, wynik = "", -1
    for kod in KODOWANIA_KANDYDACI[1:]:
        tekst = dane.decode(kod, errors="replace")
        punkty = sum(tekst.count(z) for z in POLSKIE_ZNAKI) - 10 * tekst.count("\ufffd")
        if punkty > wynik:
            najlepszy, wynik = tekst, punkty
    return najlepszy


def _kwota(tekst: str) -> Decimal:
    tekst = tekst.replace(",", ".")
    if tekst.endswith("."):
        tekst += "0"
    return Decimal(tekst)


def _data_rrmmdd(tekst: str) -> date:
    rok, mies, dzien = int(tekst[0:2]), int(tekst[2:4]), int(tekst[4:6])
    return date(2000 + rok if rok < 80 else 1900 + rok, mies, dzien)


def _parsuj_saldo(tekst: str) -> Saldo:
    m = SALDO_RE.match(tekst.strip())
    if not m:
        raise BladMT940(f"Nieprawidłowe saldo: {tekst!r}")
    znak, data_s, waluta, kwota = m.groups()
    wartosc = _kwota(kwota)
    return Saldo(_data_rrmmdd(data_s), waluta, -wartosc if znak == "D" else wartosc)


def _parsuj_61(tekst: str, waluta: str) -> Transakcja:
    m = TRANSAKCJA_RE.match(tekst.strip("\n"))
    if not m:
        raise BladMT940(f"Nieprawidłowe pole :61: {tekst!r}")
    data_waluty = _data_rrmmdd(m["data_waluty"])
    data_ksiegowania = data_waluty
    if m["data_ksiegowania"]:
        mies, dzien = int(m["data_ksiegowania"][:2]), int(m["data_ksiegowania"][2:])
        rok = data_waluty.year
        # Przełom roku: data waluty w grudniu, księgowanie w styczniu (i odwrotnie).
        if data_waluty.month == 12 and mies == 1:
            rok += 1
        elif data_waluty.month == 1 and mies == 12:
            rok -= 1
        data_ksiegowania = date(rok, mies, dzien)
    kwota = _kwota(m["kwota"])
    # D = obciążenie, C = uznanie; RD = storno obciążenia (uznanie), RC = storno uznania (obciążenie).
    if m["znak"] in ("D", "ED", "RC"):
        kwota = -kwota
    return Transakcja(
        data_waluty=data_waluty,
        data_ksiegowania=data_ksiegowania,
        kwota=kwota,
        waluta=waluta,
        kod_typu=m["typ"],
        referencja=(m["referencja"] or "").strip(),
        referencja_banku=(m["referencja_banku"] or "").strip(),
    )


def _polacz(podpola: Dict[str, str], klucze) -> str:
    """Skleja kolejne podpola. Banki dzielą długi tekst na kawałki o stałej długości
    (zwykle 27-35 znaków), więc pełny kawałek doklejamy bez spacji (słowo mogło zostać
    przecięte), a krótszy traktujemy jako zakończony i wstawiamy spację."""
    wynik = ""
    for k in klucze:
        kawalek = podpola.get(k, "")
        if not kawalek.strip():
            continue
        if wynik and not (wynik[-1].isspace() or kawalek[0].isspace() or len(poprzedni) >= 27):
            wynik += " "
        wynik += kawalek
        poprzedni = kawalek
    return " ".join(wynik.split())


def _uzupelnij_z_86(t: Transakcja, linie: List[str]) -> None:
    surowe = "".join(linie)
    m = PODPOLA_86_RE.match(surowe)
    if not m:
        t.szczegoly_86 = " ".join(l.strip() for l in linie).strip()
        t.tytul = t.szczegoly_86
        return

    t.kod_operacji, sep = m.group(1), m.group(2)
    t.szczegoly_86 = surowe
    podpola: Dict[str, str] = {}
    for kawalek in surowe[3:].split(sep)[1:]:
        if len(kawalek) >= 2 and kawalek[:2].isdigit():
            klucz = kawalek[:2]
            podpola[klucz] = podpola.get(klucz, "") + kawalek[2:]
    t.podpola_86 = podpola

    # Układ podpól wg standardu ZBP (stosowany przez większość polskich banków):
    #   00 opis operacji, 20-26 tytuł, 27-29 i 60-63 nazwa/adres kontrahenta,
    #   30 nr rozliczeniowy banku kontrahenta, 31 nr rachunku, 32-33 nazwa kontrahenta,
    #   38 IBAN kontrahenta.
    t.opis_operacji = podpola.get("00", "").strip()
    t.tytul = _polacz(podpola, ["20", "21", "22", "23", "24", "25", "26"])
    nazwa = _polacz(podpola, ["32", "33"])
    adres = _polacz(podpola, ["27", "28", "29", "60", "61", "62", "63"])
    if not nazwa:
        nazwa, adres = adres, ""
    t.kontrahent = nazwa
    t.adres_kontrahenta = adres
    iban = podpola.get("38", "").replace(" ", "").strip()
    if not iban:
        iban = (podpola.get("30", "") + podpola.get("31", "")).replace(" ", "").strip()
    t.rachunek_kontrahenta = iban
    if not t.tytul:
        t.tytul = t.opis_operacji


def _rachunek(tekst: str) -> str:
    tekst = tekst.strip().lstrip("/")
    # Format "nr_rozliczeniowy/nr_rachunku" sklejamy do jednego numeru.
    return tekst.replace("/", "").replace(" ", "")


def _wytnij_blok4(tekst: str) -> str:
    """Zostawia treść bloków {4:...} z koperty SWIFT; bez koperty zwraca tekst bez zmian."""
    if "{4:" not in tekst:
        return tekst
    bloki = re.findall(r"\{4:(.*?)\n-\}", tekst, re.DOTALL)
    return "\n-\n".join(bloki)


def parsuj_tekst(tekst: str) -> List[Wyciag]:
    tekst = tekst.replace("\r\n", "\n").replace("\r", "\n")
    tekst = _wytnij_blok4(tekst)

    # Składamy pola (tag, lista linii) z uwzględnieniem linii kontynuacji.
    pola: List[tuple] = []
    for linia in tekst.split("\n"):
        if not linia.strip():
            continue
        m = TAG_RE.match(linia)
        if m:
            pola.append((m.group(1), [m.group(2)]))
        elif linia.strip() in ("-", "-}") or linia.startswith("{") or linia.startswith("}"):
            pola.append(("KONIEC", []))
        elif pola and pola[-1][0] != "KONIEC":
            pola[-1][1].append(linia)
        # Linie przed pierwszym tagiem (nagłówki banku) są pomijane.

    wyciagi: List[Wyciag] = []
    biezacy: Optional[Wyciag] = None
    ostatnia: Optional[Transakcja] = None

    def nowy() -> Wyciag:
        w = Wyciag()
        wyciagi.append(w)
        return w

    for tag, linie in pola:
        tresc = "\n".join(linie)
        if tag == "KONIEC":
            biezacy, ostatnia = None, None
            continue
        if tag == "20":
            biezacy, ostatnia = nowy(), None
            biezacy.referencja = tresc.strip()
            continue
        if biezacy is None:
            biezacy = nowy()
        if tag == "25":
            biezacy.rachunek = _rachunek(tresc)
        elif tag in ("28", "28C"):
            biezacy.numer = tresc.strip()
        elif tag in ("60F", "60M"):
            biezacy.saldo_poczatkowe = _parsuj_saldo(tresc)
        elif tag in ("62F", "62M"):
            biezacy.saldo_koncowe = _parsuj_saldo(tresc)
            ostatnia = None
        elif tag == "64":
            biezacy.saldo_dostepne = _parsuj_saldo(tresc)
            ostatnia = None
        elif tag == "61":
            ostatnia = _parsuj_61(tresc, biezacy.waluta)
            biezacy.transakcje.append(ostatnia)
        elif tag == "86" and ostatnia is not None:
            _uzupelnij_z_86(ostatnia, linie)
        # Pozostałe tagi (:13:, :21:, :65:, :NS:, :86: wyciągu) są ignorowane.

    wyciagi = [w for w in wyciagi if w.rachunek or w.transakcje]
    if not wyciagi:
        raise BladMT940("Nie znaleziono żadnego wyciągu MT940 w pliku")
    for w in wyciagi:
        for t in w.transakcje:
            t.waluta = w.waluta
    return wyciagi


def parsuj_plik(sciezka: str, kodowanie: str = "auto") -> List[Wyciag]:
    with open(sciezka, "rb") as f:
        return parsuj_tekst(dekoduj(f.read(), kodowanie))
