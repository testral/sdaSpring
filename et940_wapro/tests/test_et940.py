import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal

KATALOG = os.path.dirname(os.path.abspath(__file__))
DANE = os.path.join(KATALOG, "dane")
sys.path.insert(0, os.path.dirname(KATALOG))

import et940_do_wapro  # noqa: E402
import fakir_eksport  # noqa: E402
from mt940_parser import BladMT940, parsuj_plik, parsuj_tekst  # noqa: E402


class TestParser(unittest.TestCase):
    def test_pko_cp852_podpola_tylda(self):
        (w,) = parsuj_plik(os.path.join(DANE, "pko_cp852.sta"))
        self.assertEqual(w.rachunek, "PL61109010140000071219812874")
        self.assertEqual(w.numer, "00205")
        self.assertIsNone(w.sprawdz_salda())
        t1, t2 = w.transakcje
        self.assertEqual(t1.kwota, Decimal("1230.50"))
        self.assertEqual(t1.kontrahent, "PRZEDSIĘBIORSTWO HANDLOWE ŻURAWSKI SP. Z O.O.")
        self.assertEqual(t1.adres_kontrahenta, "UL. KRÓLEWSKA 1, 00-001 WARSZAWA")
        self.assertEqual(t1.tytul, "Zapłata za fakturę FV/2026/10/0123")
        self.assertEqual(t1.rachunek_kontrahenta, "PL271050144510001234567890")
        self.assertEqual(t1.referencja_banku, "BR26300123456")
        self.assertEqual(t2.kwota, Decimal("-250.00"))
        self.assertEqual(t2.rachunek_kontrahenta, "PL10109010140000071219811111")

    def test_mbank_koperta_swift_wiele_wyciagow_storno(self):
        w1, w2 = parsuj_plik(os.path.join(DANE, "mbank_cp1250.940"))
        self.assertEqual((w1.numer, w2.numer), ("101/1", "102/1"))
        self.assertEqual(w1.transakcje[0].tytul, "Zwrot zaliczki - koszty delegacji")
        self.assertEqual(w2.transakcje[0].tytul, "ORLEN STACJA NR 123 ŁÓDŹ")
        # RD = storno obciążenia, czyli wpływ
        self.assertEqual(w2.transakcje[1].kwota, Decimal("10.00"))
        self.assertIsNone(w2.sprawdz_salda())

    def test_niestrukturalne_86_i_przelom_roku(self):
        (w,) = parsuj_plik(os.path.join(DANE, "niestrukturalny_utf8.txt"))
        (t,) = w.transakcje
        self.assertEqual(w.waluta, "EUR")
        self.assertEqual(t.data_waluty, date(2025, 12, 31))
        self.assertEqual(t.data_ksiegowania, date(2026, 1, 2))
        self.assertEqual(t.referencja, "INV/2025/77")
        self.assertEqual(t.referencja_banku, "XYZ")
        self.assertEqual(t.tytul, "Payment for invoice INV/2025/77 from ACME GmbH Berlin")
        self.assertIsNone(w.sprawdz_salda())

    def test_niezgodne_salda(self):
        (w,) = parsuj_tekst(":20:X\n:25:PL1\n:60F:C260101PLN0,\n:61:260101C5,NTRFX\n:62F:C260101PLN6,\n-\n")
        self.assertIn("6", w.sprawdz_salda())

    def test_plik_niebedacy_mt940(self):
        with self.assertRaises(BladMT940):
            parsuj_tekst("to nie jest wyciąg")


class TestEksport(unittest.TestCase):
    def test_czyszczenie_separatora_i_kolumny(self):
        (w,) = parsuj_tekst(
            ":20:X\n:25:PL1\n:60F:C260101PLN0,\n:61:260101D5,NTRFX\n:86:Tytuł; z \"średnikiem\"\n:62F:D260101PLN5,\n-\n"
        )
        u = fakir_eksport.UstawieniaEksportu(kolumny=["data_operacji", "kwota_bez_znaku", "strona", "tytul"],
                                             format_daty="%d.%m.%Y", naglowek=False)
        self.assertEqual(fakir_eksport.wiersze(w, u), ["01.01.2026;5,00;MA;Tytuł z 'średnikiem'"])

    def test_nieznana_kolumna(self):
        (w,) = parsuj_plik(os.path.join(DANE, "pko_cp852.sta"))
        with self.assertRaises(ValueError):
            fakir_eksport.wiersze(w, fakir_eksport.UstawieniaEksportu(kolumny=["xyz"]))


class TestPrzeplyw(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "wejscie"))
        self.config = os.path.join(self.tmp, "config.ini")
        with open(self.config, "w", encoding="utf-8") as f:
            f.write("[katalogi]\nwejscie = wejscie\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _wrzuc(self, nazwa, tresc=None):
        cel = os.path.join(self.tmp, "wejscie", nazwa)
        if tresc is None:
            shutil.copy(os.path.join(DANE, nazwa), cel)
        else:
            with open(cel, "w", encoding="utf-8") as f:
                f.write(tresc)
        os.utime(cel, (0, 0))  # plik "stary", więc nie jest uznawany za zapisywany

    def test_katalog_archiwum_bledne_i_duplikaty(self):
        self._wrzuc("mbank_cp1250.940")
        self._wrzuc("zly.sta", "śmieci")
        self.assertEqual(et940_do_wapro.main(["-c", self.config]), 1)
        self.assertEqual(len(os.listdir(os.path.join(self.tmp, "do_fakira"))), 2)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "archiwum")), ["mbank_cp1250.940"])
        self.assertEqual(os.listdir(os.path.join(self.tmp, "bledne")), ["zly.sta"])
        self.assertEqual(os.listdir(os.path.join(self.tmp, "wejscie")), [])

        # Ten sam plik drugi raz: pomijany, nie powstają nowe pliki dla Fakira.
        shutil.rmtree(os.path.join(self.tmp, "do_fakira"))
        self._wrzuc("mbank_cp1250.940")
        self.assertEqual(et940_do_wapro.main(["-c", self.config]), 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "do_fakira")))
        self.assertEqual(len(os.listdir(os.path.join(self.tmp, "archiwum"))), 2)


if __name__ == "__main__":
    unittest.main()
