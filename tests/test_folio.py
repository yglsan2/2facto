import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from facturx import get_facturx_xml_from_pdf

from folio import core
from folio.db import connect
from folio.web import create_app

SELLER = "73282932000009"
CLIENT = "55210055400013"
IBAN = "FR7630006000011234567890189"
ZERO = "0" * 64


def company():
    return {
        "prenom": "Camille",
        "nom": "Martin",
        "adresse": "12 rue des Lilas",
        "code_postal": "69001",
        "ville": "Lyon",
        "email": "camille@example.com",
        "siret": SELLER,
        "iban": IBAN,
        "regime_tva": "franchise",
    }


def client_data():
    return {
        "type": "professionnel",
        "nom": "Atelier Nord",
        "contact": "Léa Bernard",
        "adresse": "4 quai Saint-Vincent",
        "complement": "",
        "code_postal": "69001",
        "ville": "Lyon",
        "pays": "FR",
        "email": "lea@atelier.example",
        "telephone": "",
        "siret": CLIENT,
        "masquer_adresse": 0,
        "notes": "",
    }


def draft(client_id, price="1200,00", day=""):
    year = date.today().year
    return {
        "kind": "facture",
        "client_id": client_id,
        "objet": "Site vitrine",
        "issue_date": day or f"{year}-06-15",
        "service_date": day or f"{year}-06-15",
        "service_end": "",
        "due_date": "",
        "valid_until": "",
        "bon_commande": "",
        "notes": "",
        "nature": "prestation",
        "livraison_adresse": "",
        "livraison_cp": "",
        "livraison_ville": "",
        "remise_bp": 0,
        "lines": [{
            "libelle": "Conception du site",
            "qty_bp": 100,
            "unite": "forfait",
            "prix_cents": core.parse_cents(price, "le prix"),
            "remise_bp": 0,
        }],
    }


class Rules(unittest.TestCase):
    def test_identifiants_et_arrondi(self):
        self.assertTrue(core.siret_ok(SELLER))
        self.assertTrue(core.siret_ok("732 829 320 00009"))
        self.assertFalse(core.siret_ok("73282932000000"))
        self.assertTrue(core.iban_ok(IBAN))
        self.assertFalse(core.iban_ok("FR7630006000011234567890188"))
        self.assertEqual(core.line_cents(100, 10000, 1000), 9000)
        self.assertEqual(core.line_cents(50, 1, 0), 1)
        self.assertEqual(core.parse_cents("1 200,50", "le prix"), 120050)

    def test_piste_et_facturx(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            conn = connect(Path(folder) / "folio.db")
            try:
                self._piste(conn)
            finally:
                conn.close()

    def _piste(self, conn):
            core.save_company(conn, company())
            cid = core.save_client(conn, None, client_data())
            year = date.today().year
            gone = core.enregistrer(conn, None, draft(cid))
            core.supprimer(conn, gone)
            self.assertIsNone(conn.execute("SELECT last FROM sequences").fetchone())
            first = core.enregistrer(conn, None, draft(cid))
            core.emettre(conn, first)
            doc = core.one(conn, "SELECT * FROM documents WHERE id=?", (first,))
            self.assertEqual(doc["number"], f"F{year}-0001")
            self.assertEqual(doc["prev_hash"], ZERO)
            self.assertEqual(doc["locked"], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE documents SET objet='modifié' WHERE id=?", (first,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM documents WHERE id=?", (first,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE audit SET detail='x' WHERE id=1")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE sequences SET last=last-1 WHERE kind='facture'")
            filename, xml = get_facturx_xml_from_pdf(doc["pdf"], check_xsd=True, check_schematron=False)
            self.assertTrue(filename)
            text = xml.decode("utf-8")
            self.assertIn("VATEX-FR-FRANCHISE", text)
            self.assertIn(f"F{year}-0001", text)
            self.assertIn(SELLER[:9], text)
            later = core.enregistrer(conn, None, draft(cid, day=f"{year}-06-20"))
            core.emettre(conn, later)
            second = core.one(conn, "SELECT number, prev_hash FROM documents WHERE id=?", (later,))
            self.assertEqual(second["number"], f"F{year}-0002")
            self.assertEqual(second["prev_hash"], doc["content_hash"])
            early = core.enregistrer(conn, None, draft(cid, day=f"{year}-06-01"))
            with self.assertRaises(core.FolioError):
                core.emettre(conn, early)
            core.encaisser(conn, first, 120000, f"{year}-06-16", "virement")
            with self.assertRaises(core.FolioError):
                core.encaisser(conn, first, 1, f"{year}-06-16", "virement")
            stats = core.recettes(conn, year)
            self.assertEqual(stats["encaisse"], 120000)
            self.assertEqual(len(stats["book"]), 1)
            too_big = core.avoir_brouillon(conn, first)
            conn.execute(
                "UPDATE lines SET prix_cents=200000, ht_cents=200000 WHERE document_id=?",
                (too_big,),
            )
            conn.execute(
                "UPDATE documents SET total_ht_cents=200000, total_ttc_cents=200000 WHERE id=?",
                (too_big,),
            )
            with self.assertRaises(core.FolioError):
                core.emettre(conn, too_big)
            core.supprimer(conn, too_big)
            avoir = core.avoir_brouillon(conn, first)
            core.emettre(conn, avoir)
            credit = core.one(conn, "SELECT number FROM documents WHERE id=?", (avoir,))
            self.assertEqual(credit["number"], f"F{year}-0003")
            self.assertEqual(core.verifier(conn), [])

    def test_conseils(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            app = create_app(folder)
            app.config["TESTING"] = True
            client = app.test_client()
            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            page = home.get_data(as_text=True)
            self.assertIn("plateforme agréée", page)
            self.assertIn('class="tip"', page)
            self.assertIn("2facto-aide", page)
            self.assertIn("SIRET", page)
            with client.session_transaction() as sess:
                token = sess["csrf"]
            started = client.post("/demarrage", data={
                "csrf": token, **company(), "regime_tva": "franchise",
            }, follow_redirects=True)
            self.assertEqual(started.status_code, 200)
            self.assertIn("Encaissé", started.get_data(as_text=True))
            made = client.post("/clients/nouveau", data={"csrf": token, **client_data()}, follow_redirects=True)
            self.assertIn("Atelier Nord", made.get_data(as_text=True))
            editor = client.get("/nouveau/facture")
            self.assertEqual(editor.status_code, 200)
            self.assertIn("Émettre", editor.get_data(as_text=True))
            self.assertIn("Sera numéroté", editor.get_data(as_text=True))
            for path in ("/factures", "/devis", "/recettes", "/reglages", "/journal"):
                page = client.get(path)
                self.assertEqual(page.status_code, 200, path)
            empty = connect(Path(folder) / "other.db")
            try:
                self.assertIn("Conseils", core.situation(empty)["coach"]["text"])
            finally:
                empty.close()


if __name__ == "__main__":
    unittest.main()
