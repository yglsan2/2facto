import os
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

from folio import core
from folio.desk import Main

FIELDS = (
    "client",
    "objet",
    "issue",
    "service",
    "service_end",
    "valid_until",
    "po",
    "nature",
    "remise",
    "livraison",
    "liv_cp",
    "liv_ville",
    "notes",
)


class DeskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ["FOLIO_DATA"] = self.tmp.name
        self.app = QApplication.instance() or QApplication([])
        self.window = Main()

    def tearDown(self):
        self.window.close()
        self.tmp.cleanup()

    def test_devis_complet(self):
        self.assertIn("2 facto", self.window.windowTitle())
        page = Path(__file__).resolve().parents[1] / "templates" / "editor.html"
        html = page.read_text(encoding="utf-8")
        for name in ("valid_until", "service_end", "bon_commande", "livraison_adresse", "name=\"notes\""):
            self.assertIn(name, html)

        editor = self.window.pages["editor"]
        editor.open(kind="devis")
        self.assertEqual(editor.title.text(), "Nouveau devis")
        self.assertEqual(editor.nature.count(), 3)
        for name in FIELDS:
            self.assertFalse(getattr(editor, name).isHidden(), name)
        self.assertIn("Valable", editor.valid_label.text())

        core.save_client(self.window.conn, None, {
            "type": "professionnel",
            "nom": "Atelier Nord",
            "contact": "Léa Bernard",
            "adresse": "4 quai Saint-Vincent",
            "complement": "",
            "code_postal": "69001",
            "ville": "Lyon",
            "pays": "FR",
            "email": "",
            "telephone": "",
            "siret": "",
            "masquer_adresse": 0,
            "notes": "",
        })
        editor.open(kind="devis")
        editor.client.setCurrentIndex(1)
        editor.objet.setText("Site vitrine")
        editor.table.item(0, 0).setText("Conception du site")
        editor.table.item(0, 3).setText("1200")
        editor.po.setText("BC-9")
        editor.remise.setText("10")
        editor.livraison.setText("1 rue du Port")
        editor.liv_cp.setText("44000")
        editor.liv_ville.setText("Nantes")
        editor.notes.setPlainText("Acompte à la commande")
        doc_id = editor.save()
        self.assertTrue(doc_id)
        saved = core.one(self.window.conn, "SELECT * FROM documents WHERE id=?", (doc_id,))
        self.assertEqual(saved["kind"], "devis")
        self.assertEqual(saved["objet"], "Site vitrine")
        self.assertEqual(saved["bon_commande"], "BC-9")
        self.assertEqual(saved["nature"], "prestation")
        self.assertEqual(saved["remise_bp"], 1000)
        self.assertEqual(saved["livraison_adresse"], "1 rue du Port")
        self.assertEqual(saved["livraison_cp"], "44000")
        self.assertEqual(saved["livraison_ville"], "Nantes")
        self.assertEqual(saved["notes"], "Acompte à la commande")
        self.assertTrue(saved["valid_until"])
        self.assertEqual(saved["total_ht_cents"], 108000)

        editor.open(kind="facture")
        self.assertEqual(editor.title.text(), "Nouvelle facture")
        self.assertTrue(editor.valid_until.isHidden())
        self.assertFalse(editor.due.isHidden())
        self.assertFalse(editor.nature.isHidden())
        self.assertFalse(editor.notes.isHidden())
        self.assertFalse(editor.livraison.isHidden())
