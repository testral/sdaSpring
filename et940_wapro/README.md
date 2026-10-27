# MT940 → WAPRO Fakir (finanse i księgowość)

Skrypt automatycznie odczytuje wyciągi bankowe w formacie **MT940** (pliki `.sta`, `.940`,
`.mt940`, `.txt`) i zamienia je na ujednolicony plik tekstowy (CSV), który wczytuje się
w **WAPRO Fakir** przez moduł *eBank → import wyciągów*.

Po co pośredni plik CSV zamiast bezpośredniego importu MT940 w Fakirze?

* Każdy bank wypełnia pole `:86:` (tytuł, kontrahent, rachunek) trochę inaczej. Bez skryptu
  dla każdego banku trzeba osobno ustawiać schemat w Fakirze. Skrypt ujednolica wszystkie
  warianty, więc w Fakirze wystarcza **jedna definicja pliku importu** dla wszystkich banków.
* Skrypt sprawdza poprawność wyciągu (saldo początkowe + obroty = saldo końcowe).
* Skrypt pilnuje, żeby ten sam plik nie został zaimportowany dwa razy.
* Nie zapisuje niczego bezpośrednio do bazy WAPRO, więc nie ma ryzyka jej uszkodzenia.

## Wymagania

* Windows z zainstalowanym **Pythonem 3.8+** (https://www.python.org/downloads/, przy
  instalacji zaznacz *Add python.exe to PATH*). Nie są potrzebne żadne dodatkowe biblioteki.

## Jak to działa

```
wejscie\      <- tu trafiają pliki MT940 z banku
   │  python et940_do_wapro.py   (ręcznie, uruchom.bat lub Harmonogram zadań)
   ├─> do_fakira\   pliki CSV do zaimportowania w Fakirze (jeden plik na wyciąg)
   ├─> archiwum\    przetworzone pliki MT940
   ├─> bledne\      pliki, których nie dało się odczytać lub z niezgodnymi saldami
   └─> logi\        et940_wapro.log + rejestr przetworzonych plików
```

Obsługiwane warianty MT940: koperta SWIFT `{1:}{2:}{4:}` lub sam blok danych, wiele
wyciągów w pliku, pole `:86:` strukturalne (podpola `~20`, `^20`, `<20`, `?20` – standard
ZBP używany m.in. przez PKO BP, Pekao, mBank, ING, Santander, Millennium) i niestrukturalne,
storna (`RC`/`RD`), kodowania CP852, Windows-1250, UTF-8, ISO-8859-2 (wykrywane automatycznie).

## Uruchamianie

```bat
:: przetwórz wszystko z katalogu "wejscie"
uruchom.bat

:: podgląd – tylko wypisz transakcje z pliku, nic nie zapisuj
py et940_do_wapro.py --podglad C:\Bank\wyciag.sta

:: przetwórz wskazany plik (plik zostaje na miejscu, nie jest przenoszony)
py et940_do_wapro.py C:\Bank\wyciag.sta

:: tryb ciągły: sprawdzaj katalog co 60 sekund
py et940_do_wapro.py --obserwuj 60

:: przetwórz ponownie plik, który był już przetworzony
py et940_do_wapro.py --wymus C:\Bank\wyciag.sta
```

### Automatyczne uruchamianie (Harmonogram zadań Windows)

1. *Harmonogram zadań* → *Utwórz zadanie podstawowe*.
2. Wyzwalacz: np. codziennie co 15 minut / o wybranej godzinie.
3. Akcja: *Uruchom program* → wskaż `uruchom.bat`, pole *Rozpocznij w*: katalog skryptu.

Jeśli bankowość elektroniczna (lub program typu home-banking) potrafi zapisywać wyciągi do
katalogu – ustaw jej katalog docelowy na `wejscie` (albo zmień `wejscie` w `config.ini`
na katalog, do którego bank już zapisuje).

## Konfiguracja – `config.ini`

Najważniejsze ustawienia (opisy są też w samym pliku):

| Ustawienie | Domyślnie | Znaczenie |
|---|---|---|
| `[katalogi] wejscie / wyjscie / ...` | `wejscie`, `do_fakira`, ... | katalogi robocze (mogą być ścieżki sieciowe) |
| `[wejscie] kodowanie` | `auto` | kodowanie plików MT940 |
| `[wejscie] blad_przy_niezgodnym_saldzie` | `tak` | odrzuć wyciąg, którego salda się nie zgadzają |
| `[wyjscie] kodowanie` | `windows-1250` | kodowanie pliku dla Fakira |
| `[wyjscie] separator` | `;` | separator kolumn |
| `[wyjscie] separator_dziesietny` | `,` | `1230,50` |
| `[wyjscie] format_daty` | `%Y-%m-%d` | `2026-10-27` |
| `[wyjscie] naglowek` | `tak` | pierwszy wiersz z nazwami kolumn |
| `[wyjscie] kolumny` | patrz niżej | kolejność i zestaw kolumn |

### Kolumny pliku dla Fakira (układ domyślny)

| Nr | Kolumna | Przykład | Opis |
|---|---|---|---|
| 1 | `rachunek` | `PL61109010140000071219812874` | rachunek firmy, którego dotyczy wyciąg |
| 2 | `nr_wyciagu` | `00205` | numer wyciągu z banku (`:28C:`) |
| 3 | `data_operacji` | `2026-10-27` | data księgowania w banku |
| 4 | `data_waluty` | `2026-10-27` | data waluty |
| 5 | `kwota` | `-250,00` | kwota ze znakiem (+ wpływ, − wypływ) |
| 6 | `wplyw` | `1230,50` | kwota wpływu (puste dla wypływu) |
| 7 | `wyplyw` | `250,00` | kwota wypływu (puste dla wpływu) |
| 8 | `waluta` | `PLN` | |
| 9 | `kontrahent` | `Biuro Rachunkowe Śliwa` | nazwa kontrahenta |
| 10 | `adres_kontrahenta` | `UL. KRÓLEWSKA 1, 00-001 WARSZAWA` | |
| 11 | `rachunek_kontrahenta` | `PL27105014451000...` | do dopasowania kontrahenta w Fakirze |
| 12 | `tytul` | `Zapłata za fakturę FV/2026/10/0123` | tytuł przelewu (do rozliczeń) |
| 13 | `kod_operacji` | `020` | kod operacji banku |
| 14 | `opis_operacji` | `PRZELEW PRZYCHODZĄCY` | rodzaj operacji wg banku |
| 15 | `referencja_banku` | `BR26300123456` | unikalny numer operacji w banku |

Dodatkowo dostępne: `data_wyciagu`, `kwota_bez_znaku`, `strona` (`WN` dla wpływu / `MA`
dla wypływu – strona konta bankowego, np. 130), `znak` (`C`/`D`), `kod_typu`, `referencja`.
Jeśli Fakir wymaga innej kolejności kolumn albo np. kwoty bez znaku + oznaczenia strony –
wystarczy zmienić listę `kolumny` w `config.ini`.

## Jednorazowa konfiguracja po stronie WAPRO Fakir

> Nazwy menu mogą się nieznacznie różnić w zależności od wersji Fakira.

1. **Rachunek bankowy firmy** – w kartotece banków/rachunków firmy upewnij się, że jest
   wpisany numer rachunku zgodny z kolumną `rachunek` (pełny IBAN).
2. **Definicja pliku importu** – *Definicje → eBank → Definicje plików importu i eksportu*
   → dodaj nową definicję importu wyciągu:
   * typ pliku: **tekstowy z separatorem** (CSV),
   * separator: `;`, kodowanie: **Windows-1250**, pomiń **1** wiersz (nagłówek),
   * format daty: `RRRR-MM-DD`, separator dziesiętny: przecinek,
   * powiąż pola programu z numerami kolumn wg tabeli powyżej, np.:
     data operacji → 3, kwota → 5 (lub osobno wpływ → 6 i wypływ → 7, zależnie od
     tego, czego oczekuje definicja), kontrahent → 9, rachunek kontrahenta → 11,
     tytuł/treść → 12, numer wyciągu → 2.
3. **Schemat importu** – określ typ dokumentu (np. `WB`), dziennik oraz konto bankowe
   (np. `130-1`), tak jak przy zwykłym imporcie wyciągów; ustaw automatyczne dopasowanie
   rozrachunków po tytule/rachunku kontrahenta, jeśli z niego korzystasz.
4. **Import** – w module eBank wybierz import wyciągu z pliku, wskaż definicję z pkt 2
   i plik z katalogu `do_fakira`. Po udanym imporcie plik CSV można usunąć lub przenieść.

Pierwszy import wykonaj na **kopii bazy** lub firmie testowej i porównaj z wyciągiem PDF.

## Testy

```bat
py -m unittest discover -s tests -v
```

W katalogu `tests\dane` są przykładowe (fikcyjne) wyciągi w różnych wariantach. Warto
dorzucić tam prawdziwy (zanonimizowany) wyciąg z Waszego banku i sprawdzić go
poleceniem `--podglad`.
