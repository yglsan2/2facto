"""2 facto — la même facturation, dans une fenêtre, sans navigateur."""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from folio import core, invoice
from folio.db import connect
from folio.web import clean_client, clean_company

SUGGESTIONS = (
    ("Site internet", "Conception et réalisation d'un site internet", "forfait", ""),
    ("Logiciel", "Développement d'un logiciel", "forfait", ""),
    ("Journée", "Journée de développement", "jour", ""),
)

STYLE = """
QMainWindow, QWidget { background: #f6f7f9; color: #18211c; }
QListWidget { background: white; border: none; outline: none; }
QListWidget::item { padding: 10px 14px; border-radius: 8px; }
QListWidget::item:selected { background: #e7f3ee; color: #14352c; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDateEdit, QSpinBox {
  background: white; border: 1px solid #e3e7e4; border-radius: 8px; padding: 6px;
}
QTableWidget { background: white; border: 1px solid #e3e7e4; border-radius: 8px; }
QPushButton { padding: 8px 14px; border-radius: 14px; border: 1px solid #e3e7e4; background: white; }
QPushButton#primary { background: #14352c; color: #f4f7f5; border: none; font-weight: 600; }
QGroupBox {
  font-weight: 600; border: 1px solid #e3e7e4; border-radius: 12px;
  margin-top: 14px; padding: 12px; background: white;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
"""


ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "static" / "logo.png"
MARK = ROOT / "static" / "mark.png"


def data_dir() -> Path:
    folder = Path(os.environ.get("FOLIO_DATA") or Path.home() / ".2facto")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def iso(value: QDate) -> str:
    return value.toString("yyyy-MM-dd")


def qdate(value: str) -> QDate:
    if not value:
        return QDate.currentDate()
    year, month, day = (int(part) for part in value.split("-"))
    return QDate(year, month, day)


def warn(parent, text: str):
    QMessageBox.warning(parent, "2 facto", text)


def confirm(parent, text: str) -> bool:
    answer = QMessageBox.question(parent, "2 facto", text, QMessageBox.Yes | QMessageBox.No)
    return answer == QMessageBox.Yes


def run(parent, fn):
    try:
        return fn()
    except core.FolioError as err:
        warn(parent, str(err))
        return None


def wipe(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def open_bytes(payload: bytes, name: str):
    folder = data_dir() / "apercus"
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_bytes(payload)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def show_pdf(conn, doc_id: int):
    doc = core.one(conn, "SELECT id, locked, number, pdf FROM documents WHERE id=?", (doc_id,))
    if not doc:
        raise core.FolioError("Ce document est introuvable.")
    if doc["locked"] and doc["pdf"]:
        open_bytes(bytes(doc["pdf"]), f"{doc['number']}.pdf")
        return
    snap, _ = core.snapshot_live(conn, doc_id)
    company = core.company(conn, with_logo=True)
    payload = invoice.render_pdf(snap, company.get("logo"), watermark=True)
    open_bytes(payload, "brouillon.pdf")


def scroll(inner: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(inner)
    return area


def primary(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("primary")
    return button


def date_edit(value: QDate | None = None) -> QDateEdit:
    widget = QDateEdit(value or QDate.currentDate())
    widget.setCalendarPopup(True)
    widget.setDisplayFormat("dd/MM/yyyy")
    return widget


def unit_combo(current="forfait") -> QComboBox:
    box = QComboBox()
    for key, (label, _code) in core.UNITES.items():
        box.addItem(label, key)
    index = box.findData(current)
    if index >= 0:
        box.setCurrentIndex(index)
    return box


class ClientDialog(QDialog):
    def __init__(self, conn, client_id=None, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.client_id = client_id
        self.setWindowTitle("Client — 2 facto")
        self.resize(480, 560)
        row = core.one(conn, "SELECT * FROM clients WHERE id=?", (client_id,)) if client_id else None
        form = QFormLayout(self)
        self.type = QComboBox()
        self.type.addItem("Professionnel", "professionnel")
        self.type.addItem("Particulier", "particulier")
        self.nom = QLineEdit()
        self.contact = QLineEdit()
        self.adresse = QLineEdit()
        self.complement = QLineEdit()
        self.code_postal = QLineEdit()
        self.ville = QLineEdit()
        self.pays = QLineEdit("FR")
        self.siret = QLineEdit()
        self.email = QLineEdit()
        self.telephone = QLineEdit()
        self.masquer = QCheckBox("Ne pas imprimer l'adresse")
        self.notes = QTextEdit()
        self.notes.setFixedHeight(70)
        form.addRow("Type", self.type)
        form.addRow("Nom", self.nom)
        form.addRow("Contact", self.contact)
        form.addRow("Adresse", self.adresse)
        form.addRow("Complément", self.complement)
        form.addRow("Code postal", self.code_postal)
        form.addRow("Ville", self.ville)
        form.addRow("Pays", self.pays)
        form.addRow("SIRET", self.siret)
        form.addRow("E-mail", self.email)
        form.addRow("Téléphone", self.telephone)
        form.addRow("", self.masquer)
        form.addRow("Note interne", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        if client_id:
            remove = QPushButton("Retirer ce client")
            remove.clicked.connect(self.remove)
            form.addRow(remove)
        if row:
            self.type.setCurrentIndex(max(0, self.type.findData(row["type"])))
            self.nom.setText(row["nom"])
            self.contact.setText(row["contact"])
            self.adresse.setText(row["adresse"])
            self.complement.setText(row["complement"])
            self.code_postal.setText(row["code_postal"])
            self.ville.setText(row["ville"])
            self.pays.setText(row["pays"] or "FR")
            self.siret.setText(row["siret"])
            self.email.setText(row["email"])
            self.telephone.setText(row["telephone"])
            self.masquer.setChecked(bool(row["masquer_adresse"]))
            self.notes.setPlainText(row["notes"])

    def values(self):
        return {
            "type": self.type.currentData(),
            "nom": self.nom.text(),
            "contact": self.contact.text(),
            "adresse": self.adresse.text(),
            "complement": self.complement.text(),
            "code_postal": self.code_postal.text(),
            "ville": self.ville.text(),
            "pays": self.pays.text() or "FR",
            "email": self.email.text(),
            "telephone": self.telephone.text(),
            "siret": self.siret.text(),
            "masquer_adresse": "1" if self.masquer.isChecked() else "",
            "notes": self.notes.toPlainText(),
        }

    def save(self):
        try:
            data = clean_client(self.values())
            self.client_id = core.save_client(self.conn, self.client_id, data)
        except core.FolioError as err:
            warn(self, str(err))
            return
        self.accept()

    def remove(self):
        if not confirm(self, "Retirer ce client ?"):
            return
        try:
            core.delete_client(self.conn, self.client_id)
        except core.FolioError as err:
            warn(self, str(err))
            return
        self.accept()


class SetupPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        form = QFormLayout(self)
        title = QLabel("Votre activité")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        form.addRow(title)
        form.addRow(QLabel("Les mêmes informations que pour une facture. Le devis les reprend toutes."))
        self.prenom = QLineEdit()
        self.nom = QLineEdit()
        self.adresse = QLineEdit()
        self.code_postal = QLineEdit()
        self.ville = QLineEdit()
        self.email = QLineEdit()
        self.siret = QLineEdit()
        self.iban = QLineEdit()
        self.regime = QComboBox()
        self.regime.addItem("Franchise en base, sans TVA", "franchise")
        self.regime.addItem("Je facture la TVA", "assujetti")
        form.addRow("Prénom", self.prenom)
        form.addRow("Nom", self.nom)
        form.addRow("Adresse", self.adresse)
        form.addRow("Code postal", self.code_postal)
        form.addRow("Ville", self.ville)
        form.addRow("E-mail", self.email)
        form.addRow("SIRET", self.siret)
        form.addRow("TVA", self.regime)
        form.addRow("IBAN", self.iban)
        button = primary("Enregistrer")
        button.clicked.connect(self.save)
        form.addRow(button)

    def open(self, **_kw):
        company = core.company(self.host.conn)
        self.prenom.setText(company["prenom"])
        self.nom.setText(company["nom"])
        self.adresse.setText(company["adresse"])
        self.code_postal.setText(company["code_postal"])
        self.ville.setText(company["ville"])
        self.email.setText(company["email"])
        self.siret.setText(company["siret"])
        self.iban.setText(company["iban"])
        self.regime.setCurrentIndex(1 if company["regime_tva"] == "assujetti" else 0)

    def save(self):
        form = {
            "prenom": self.prenom.text(),
            "nom": self.nom.text(),
            "adresse": self.adresse.text(),
            "code_postal": self.code_postal.text(),
            "ville": self.ville.text(),
            "email": self.email.text(),
            "siret": self.siret.text(),
            "iban": self.iban.text(),
            "regime_tva": self.regime.currentData(),
        }
        saved = run(self, lambda: clean_company(form, core.company(self.host.conn), minimal=True))
        if saved is None:
            return
        core.save_company(self.host.conn, saved)
        self.host.sync_nav()
        self.host.go("home")


class HomePage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        layout = QVBoxLayout(self)
        self.hello = QLabel()
        self.hello.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.coach = QLabel()
        self.coach.setWordWrap(True)
        self.encaisse = QLabel()
        self.attente = QLabel()
        self.facture = QLabel()
        row = QHBoxLayout()
        devis = primary("Nouveau devis")
        facture = QPushButton("Nouvelle facture")
        devis.clicked.connect(lambda: host.go("editor", kind="devis"))
        facture.clicked.connect(lambda: host.go("editor", kind="facture"))
        row.addWidget(devis)
        row.addWidget(facture)
        row.addStretch()
        self.recent = QVBoxLayout()
        layout.addWidget(self.hello)
        layout.addWidget(self.coach)
        layout.addWidget(self.encaisse)
        layout.addWidget(self.attente)
        layout.addWidget(self.facture)
        layout.addLayout(row)
        layout.addWidget(QLabel("Dernières factures"))
        layout.addLayout(self.recent)
        layout.addStretch()

    def open(self, **_kw):
        conn = self.host.conn
        company = core.company(conn)
        year = date.today().year
        stats = core.recettes(conn, year)
        opened = core.ouverts(conn)
        state = core.situation(conn)
        self.hello.setText(f"Bonjour {company['prenom']}")
        self.coach.setText(state["coach"]["text"])
        self.encaisse.setText(f"Encaissé en {year} : {core.eur(stats['encaisse'])}")
        self.attente.setText(f"À encaisser : {core.eur(opened['waiting'])}")
        self.facture.setText(f"Facturé : {core.eur(stats['facture_ht'])}")
        wipe(self.recent)
        for item in core.lister(conn, "facture")[:6]:
            button = QPushButton(f"{item['etat']}  {item['client']}  {item['number'] or 'Brouillon'}  {core.eur(item['total_ttc_cents'])}")
            doc_id = item["id"]
            locked = item["locked"]
            button.clicked.connect(lambda _=False, i=doc_id, lock=locked: self.host.go("show" if lock else "editor", doc_id=i))
            self.recent.addWidget(button)


class ListPage(QWidget):
    FILTERS = {
        "facture": [("", "Toutes"), ("brouillon", "Brouillons"), ("attente", "À encaisser"), ("retard", "En retard"), ("payee", "Payées")],
        "devis": [("", "Tous"), ("brouillon", "Brouillons"), ("envoye", "Envoyés"), ("accepte", "Acceptés"), ("refuse", "Refusés")],
        "avoir": [("", "Tous")],
    }
    TITLES = {"facture": "Factures", "devis": "Devis", "avoir": "Avoirs"}

    def __init__(self, host, kind):
        super().__init__()
        self.host = host
        self.kind = kind
        self.rows = []
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel(self.TITLES[kind])
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch()
        if kind != "avoir":
            button = primary("Nouveau")
            button.clicked.connect(lambda: host.go("editor", kind=kind))
            header.addWidget(button)
        if kind == "facture":
            avoirs = QPushButton("Voir les avoirs")
            avoirs.clicked.connect(lambda: host.go("avoirs"))
            header.addWidget(avoirs)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Client, numéro, objet")
        self.search.returnPressed.connect(self.reload)
        self.filter = QComboBox()
        for key, label in self.FILTERS[kind]:
            self.filter.addItem(label, key)
        self.filter.currentIndexChanged.connect(self.reload)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["État", "Client", "Numéro", "Date", "Montant"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self.open_row)
        layout.addLayout(header)
        tools = QHBoxLayout()
        tools.addWidget(self.search)
        tools.addWidget(self.filter)
        layout.addLayout(tools)
        layout.addWidget(self.table)

    def open(self, **_kw):
        self.reload()

    def reload(self):
        self.rows = core.lister(self.host.conn, self.kind, self.filter.currentData() or "", self.search.text())
        self.table.setRowCount(len(self.rows))
        for index, item in enumerate(self.rows):
            values = [
                item["etat"],
                item["client"],
                item["number"] or "Brouillon",
                core.date_fr(item["issue_date"]) or "—",
                core.eur(item["total_ttc_cents"]),
            ]
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))

    def open_row(self, index):
        item = self.rows[index.row()]
        self.host.go("show" if item["locked"] else "editor", doc_id=item["id"])


class EditorPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.kind = "devis"
        self.doc_id = None
        self.facture_id = None
        self.busy = False
        root = QHBoxLayout(self)
        form = QWidget()
        layout = QVBoxLayout(form)
        self.title = QLabel("Nouveau devis")
        self.title.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.number = QLabel()
        self.origin = QLabel()
        self.origin.setWordWrap(True)
        self.client = QComboBox()
        new_client = QPushButton("Nouveau client")
        new_client.clicked.connect(self.create_client)
        client_row = QHBoxLayout()
        client_row.addWidget(self.client, 1)
        client_row.addWidget(new_client)
        self.objet = QLineEdit()
        self.objet.setPlaceholderText("Création du site vitrine")
        self.issue = date_edit()
        self.service = date_edit()
        self.end_on = QCheckBox("Fin de prestation")
        self.service_end = date_edit()
        self.service_end.setEnabled(False)
        self.end_on.toggled.connect(self.service_end.setEnabled)
        self.due = date_edit()
        self.valid_until = date_edit()
        self.issue.dateChanged.connect(lambda _date: self.refresh_number())
        dates = QGridLayout()
        dates.addWidget(QLabel("Émission"), 0, 0)
        dates.addWidget(self.issue, 1, 0)
        dates.addWidget(QLabel("Prestation"), 0, 1)
        dates.addWidget(self.service, 1, 1)
        dates.addWidget(self.end_on, 0, 2)
        dates.addWidget(self.service_end, 1, 2)
        self.due_label = QLabel("Échéance")
        self.valid_label = QLabel("Valable jusqu'au")
        dates.addWidget(self.due_label, 0, 3)
        dates.addWidget(self.due, 1, 3)
        dates.addWidget(self.valid_label, 0, 3)
        dates.addWidget(self.valid_until, 1, 3)
        self.chips = QGridLayout()
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Prestation", "Qté", "Unité", "Prix HT", "Remise %", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.itemChanged.connect(self.refresh_total)
        add = QPushButton("Ajouter une ligne")
        add.clicked.connect(lambda: self.add_line())
        extra = QGroupBox("Compléments")
        extra_form = QFormLayout(extra)
        self.po = QLineEdit()
        self.nature = QComboBox()
        for key, label in core.NATURES.items():
            self.nature.addItem(label, key)
        self.nature.currentIndexChanged.connect(self.refresh_total)
        self.remise = QLineEdit("0")
        self.remise.textChanged.connect(self.refresh_total)
        self.livraison = QLineEdit()
        self.liv_cp = QLineEdit()
        self.liv_ville = QLineEdit()
        self.notes = QTextEdit()
        self.notes.setFixedHeight(70)
        extra_form.addRow("Bon de commande", self.po)
        extra_form.addRow("Nature", self.nature)
        extra_form.addRow("Remise globale %", self.remise)
        extra_form.addRow("Livraison", self.livraison)
        extra_form.addRow("Code postal", self.liv_cp)
        extra_form.addRow("Ville", self.liv_ville)
        extra_form.addRow("Note imprimée", self.notes)
        actions = QHBoxLayout()
        save = QPushButton("Enregistrer")
        emit = primary("Émettre")
        preview = QPushButton("Aperçu PDF")
        remove = QPushButton("Supprimer le brouillon")
        save.clicked.connect(self.save)
        emit.clicked.connect(self.emit)
        preview.clicked.connect(self.preview_pdf)
        remove.clicked.connect(self.remove)
        self.remove_button = remove
        actions.addWidget(save)
        actions.addWidget(emit)
        actions.addWidget(preview)
        actions.addWidget(remove)
        layout.addWidget(self.title)
        layout.addWidget(self.number)
        layout.addWidget(self.origin)
        layout.addLayout(client_row)
        layout.addWidget(QLabel("Objet"))
        layout.addWidget(self.objet)
        layout.addLayout(dates)
        layout.addLayout(self.chips)
        layout.addWidget(self.table)
        layout.addWidget(add)
        layout.addWidget(extra)
        layout.addLayout(actions)
        side = QVBoxLayout()
        self.total = QLabel("0,00 €")
        self.total.setStyleSheet("font-size: 28px; font-weight: 600;")
        self.side_text = QLabel()
        self.side_text.setWordWrap(True)
        side.addWidget(QLabel("Net à payer"))
        side.addWidget(self.total)
        side.addWidget(self.side_text)
        side.addStretch()
        root.addWidget(scroll(form), 3)
        side_wrap = QWidget()
        side_wrap.setLayout(side)
        side_wrap.setMinimumWidth(240)
        root.addWidget(side_wrap, 1)
        self.client.currentIndexChanged.connect(self.refresh_total)

    def open(self, doc_id=None, kind="devis", facture_id=None, **_kw):
        conn = self.host.conn
        doc = core.one(conn, "SELECT * FROM documents WHERE id=?", (doc_id,)) if doc_id else None
        if doc and doc["locked"]:
            self.host.go("show", doc_id=doc_id)
            return
        self.busy = True
        self.doc_id = doc["id"] if doc else None
        self.kind = doc["kind"] if doc else kind
        self.facture_id = doc["facture_id"] if doc else facture_id
        company = core.company(conn)
        today = QDate.currentDate()
        self.reload_clients(doc["client_id"] if doc else None)
        self.objet.setText(doc["objet"] if doc else "")
        self.issue.setDate(qdate(doc["issue_date"]) if doc and doc["issue_date"] else today)
        self.service.setDate(qdate(doc["service_date"]) if doc and doc["service_date"] else today)
        self.end_on.setChecked(bool(doc and doc["service_end"]))
        self.service_end.setDate(qdate(doc["service_end"]) if doc and doc["service_end"] else today)
        self.due.setDate(qdate(doc["due_date"]) if doc and doc["due_date"] else today.addDays(int(company["delai_jours"])))
        self.valid_until.setDate(
            qdate(doc["valid_until"]) if doc and doc["valid_until"] else today.addDays(int(company["validite_devis"]))
        )
        self.po.setText(doc["bon_commande"] if doc else "")
        self.nature.setCurrentIndex(max(0, self.nature.findData(doc["nature"] if doc else "prestation")))
        self.remise.setText(f"{(doc['remise_bp'] / 100):g}" if doc else "0")
        self.livraison.setText(doc["livraison_adresse"] if doc else "")
        self.liv_cp.setText(doc["livraison_cp"] if doc else "")
        self.liv_ville.setText(doc["livraison_ville"] if doc else "")
        self.notes.setPlainText(doc["notes"] if doc else "")
        self.table.setRowCount(0)
        lines = core.lines_of(conn, doc["id"]) if doc else []
        if lines:
            for line in lines:
                self.add_line(
                    line["libelle"],
                    f"{line['qty_bp'] / 100:g}",
                    line["unite"],
                    f"{line['prix_cents'] // 100},{line['prix_cents'] % 100:02d}",
                    f"{line['remise_bp'] / 100:g}",
                )
        else:
            self.add_line()
        self.fill_chips()
        self.apply_kind()
        self.remove_button.setVisible(self.doc_id is not None)
        self.busy = False
        self.refresh_number()
        self.refresh_total()

    def apply_kind(self):
        titles = {
            "devis": ("Nouveau devis", "Modifier le devis"),
            "facture": ("Nouvelle facture", "Modifier la facture"),
            "avoir": ("Nouvel avoir", "Modifier l'avoir"),
        }
        self.title.setText(titles[self.kind][1 if self.doc_id else 0])
        facture = self.kind == "facture"
        devis = self.kind == "devis"
        self.due.setVisible(facture)
        self.due_label.setVisible(facture)
        self.valid_until.setVisible(devis)
        self.valid_label.setVisible(devis)
        if self.kind == "avoir" and self.facture_id:
            origin = core.one(self.host.conn, "SELECT number FROM documents WHERE id=?", (self.facture_id,))
            room = core.credit_restant(self.host.conn, self.facture_id, sauf=self.doc_id)
            number = origin["number"] if origin else ""
            self.origin.setText(f"Avoir sur {number}. Reste annulable : {core.eur(room)}.")
            self.origin.show()
        else:
            self.origin.hide()

    def fill_chips(self):
        while self.chips.count():
            item = self.chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        chips = []
        for row in self.host.conn.execute("SELECT libelle, prix_cents, unite FROM catalog WHERE actif=1 ORDER BY libelle"):
            price = f"{row['prix_cents'] // 100},{row['prix_cents'] % 100:02d}"
            chips.append((row["libelle"], row["libelle"], row["unite"], price))
        chips.extend(SUGGESTIONS)
        for index, (label, libelle, unite, price) in enumerate(chips):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, a=libelle, b=unite, c=price: self.use_chip(a, b, c))
            self.chips.addWidget(button, index // 3, index % 3)

    def reload_clients(self, selected=None):
        self.client.blockSignals(True)
        self.client.clear()
        self.client.addItem("Choisir", None)
        for row in self.host.conn.execute("SELECT id, nom FROM clients ORDER BY nom"):
            self.client.addItem(row["nom"], row["id"])
        if selected is not None:
            index = self.client.findData(selected)
            if index >= 0:
                self.client.setCurrentIndex(index)
        self.client.blockSignals(False)

    def create_client(self):
        dialog = ClientDialog(self.host.conn, None, self)
        if dialog.exec():
            self.reload_clients(dialog.client_id)

    def add_line(self, libelle="", qty="1", unite="forfait", prix="", remise="0"):
        was_busy = self.busy
        self.busy = True
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(libelle))
        self.table.setItem(row, 1, QTableWidgetItem(qty))
        combo = unit_combo(unite)
        combo.currentIndexChanged.connect(self.refresh_total)
        self.table.setCellWidget(row, 2, combo)
        self.table.setItem(row, 3, QTableWidgetItem(prix))
        self.table.setItem(row, 4, QTableWidgetItem(remise))
        button = QPushButton("Retirer")
        button.clicked.connect(self.remove_line)
        self.table.setCellWidget(row, 5, button)
        self.busy = was_busy

    def remove_line(self):
        button = self.sender()
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, 5) is button:
                self.table.removeRow(row)
                break
        if self.table.rowCount() == 0:
            self.add_line()
        self.refresh_total()

    def use_chip(self, libelle, unite, prix):
        if self.table.rowCount() == 1 and not self.cell(0, 0) and not self.cell(0, 3):
            self.busy = True
            self.table.item(0, 0).setText(libelle)
            self.table.cellWidget(0, 2).setCurrentIndex(max(0, self.table.cellWidget(0, 2).findData(unite)))
            self.table.item(0, 3).setText(prix)
            self.busy = False
            self.refresh_total()
            return
        self.add_line(libelle, "1", unite, prix, "0")
        self.refresh_total()

    def cell(self, row, column) -> str:
        item = self.table.item(row, column)
        return item.text().strip() if item else ""

    def collect_lines(self, strict: bool):
        lines = []
        for row in range(self.table.rowCount()):
            libelle = self.cell(row, 0)
            price = self.cell(row, 3)
            if not libelle and not price:
                continue
            if not libelle:
                if strict:
                    raise core.FolioError("Chaque ligne doit avoir un libellé.")
                return None
            try:
                lines.append({
                    "libelle": core.clip(libelle, 180),
                    "qty_bp": core.parse_qty_bp(self.cell(row, 1) or "1"),
                    "unite": self.table.cellWidget(row, 2).currentData(),
                    "prix_cents": core.parse_cents(price, "le prix"),
                    "remise_bp": core.parse_percent_bp(self.cell(row, 4) or "0"),
                })
            except core.FolioError:
                if strict:
                    raise
                return None
        return lines

    def payload(self):
        client_id = self.client.currentData()
        if not client_id:
            raise core.FolioError("Choisissez un client.")
        lines = self.collect_lines(True)
        if not lines:
            raise core.FolioError("Ajoutez au moins une ligne.")
        data = {
            "kind": self.kind,
            "client_id": int(client_id),
            "objet": core.clip(self.objet.text(), 200),
            "issue_date": iso(self.issue.date()),
            "service_date": iso(self.service.date()),
            "service_end": iso(self.service_end.date()) if self.end_on.isChecked() else "",
            "due_date": iso(self.due.date()) if self.kind == "facture" else "",
            "valid_until": iso(self.valid_until.date()) if self.kind == "devis" else "",
            "bon_commande": core.clip(self.po.text(), 40),
            "notes": core.clip(self.notes.toPlainText(), 800),
            "nature": self.nature.currentData() or "prestation",
            "livraison_adresse": core.clip(self.livraison.text(), 180),
            "livraison_cp": core.clip(self.liv_cp.text(), 10),
            "livraison_ville": core.clip(self.liv_ville.text(), 80),
            "remise_bp": core.parse_percent_bp(self.remise.text()),
            "lines": lines,
        }
        if self.kind == "avoir":
            data["facture_id"] = self.facture_id
        return data

    def refresh_number(self):
        if self.busy:
            return
        number = core.prochain_numero(self.host.conn, self.kind, self.issue.date().year())
        self.number.setText(f"Sera numéroté {number}. Le numéro n'est pris qu'à l'émission.")

    def refresh_total(self):
        if self.busy:
            return
        lines = self.collect_lines(False)
        if lines is None:
            self.total.setText("—")
            return
        try:
            remise = core.parse_percent_bp(self.remise.text())
        except core.FolioError:
            self.total.setText("—")
            return
        company = core.company(self.host.conn)
        taxed = company["regime_tva"] == "assujetti"
        rate = company["taux_tva_bp"] if taxed else 0
        ht = sum(core.line_cents(line["qty_bp"], line["prix_cents"], line["remise_bp"]) for line in lines)
        base = ht - core.allowance_cents(ht, remise)
        tva = core.vat_cents(base, rate) if taxed else 0
        self.total.setText(core.eur(base + tva))
        client_id = self.client.currentData()
        client = core.one(self.host.conn, "SELECT nom, type FROM clients WHERE id=?", (client_id,)) if client_id else None
        bits = [core.NATURES.get(self.nature.currentData(), "")]
        if not taxed:
            bits.append(core.MENTION_TVA)
        if client and client["type"] == "professionnel":
            bits.append("Indemnité forfaitaire de 40 € en cas de retard.")
        if client:
            bits.insert(0, client["nom"])
        self.side_text.setText("\n".join(bit for bit in bits if bit))

    def save(self):
        saved = run(self, self.payload)
        if saved is None:
            return None
        doc_id = run(self, lambda: core.enregistrer(self.host.conn, self.doc_id, saved))
        if doc_id is None:
            return None
        self.doc_id = doc_id
        self.remove_button.setVisible(True)
        return doc_id

    def emit(self):
        if not confirm(self, "Émettre fige ce document et lui donne son numéro. Il ne pourra plus être modifié. Continuer ?"):
            return
        doc_id = self.save()
        if not doc_id:
            return
        if run(self, lambda: core.emettre(self.host.conn, doc_id)) is None:
            self.open(doc_id=doc_id)
            return
        self.host.go("show", doc_id=doc_id)

    def preview_pdf(self):
        doc_id = self.save()
        if doc_id:
            run(self, lambda: show_pdf(self.host.conn, doc_id))

    def remove(self):
        if not self.doc_id or not confirm(self, "Supprimer ce brouillon ?"):
            return
        kind = self.kind

        def do():
            core.supprimer(self.host.conn, self.doc_id)
            return True

        if run(self, do):
            self.host.go("devis" if kind == "devis" else "factures")


class ShowPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.doc_id = None
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.actions = QHBoxLayout()
        self.extra = QVBoxLayout()
        layout.addWidget(self.title)
        layout.addWidget(self.info)
        layout.addLayout(self.actions)
        layout.addLayout(self.extra)
        layout.addStretch()

    def open(self, doc_id=None, **_kw):
        self.doc_id = doc_id
        conn = self.host.conn
        raw = core.one(conn, "SELECT * FROM documents WHERE id=?", (doc_id,))
        if not raw:
            warn(self, "Ce document est introuvable.")
            return
        if not raw["locked"]:
            self.host.go("editor", doc_id=doc_id)
            return
        doc = core.annoter(conn, [raw])[0]
        client = core.one(conn, "SELECT * FROM clients WHERE id=?", (doc["client_id"],))
        self.title.setText(f"{doc['number']} — {doc['etat']}")
        self.info.setText(f"{client['nom']}\n{doc['objet']}\n{core.eur(doc['total_ttc_cents'])}")
        wipe(self.actions)
        wipe(self.extra)
        pdf = QPushButton("Ouvrir le PDF")
        pdf.clicked.connect(lambda: run(self, lambda: show_pdf(conn, doc_id)))
        self.actions.addWidget(pdf)
        if doc["kind"] == "facture" and doc["reste"] > 0:
            pay = primary("Marquer comme payée")
            pay.clicked.connect(lambda: self.pay(doc["reste"]))
            other = QPushButton("Autre montant")
            other.clicked.connect(self.pay_other)
            self.actions.addWidget(pay)
            self.actions.addWidget(other)
        if doc["kind"] == "facture" and doc["avoirs_cents"] < doc["total_ttc_cents"]:
            redo = QPushButton("Annuler et refaire")
            redo.clicked.connect(lambda: self.redo(doc["paye"]))
            partial = QPushButton("Avoir partiel")
            partial.clicked.connect(self.partial)
            self.actions.addWidget(redo)
            self.actions.addWidget(partial)
        if doc["kind"] == "devis" and doc["status"] != "transforme":
            accept = QPushButton("Accepté")
            refuse = QPushButton("Refusé")
            transform = primary("Transformer en facture")
            accept.clicked.connect(lambda: self.status("accepte"))
            refuse.clicked.connect(lambda: self.status("refuse"))
            transform.clicked.connect(self.transform)
            self.actions.addWidget(accept)
            self.actions.addWidget(refuse)
            self.actions.addWidget(transform)
        if doc["kind"] != "avoir":
            copy = QPushButton("Dupliquer")
            copy.clicked.connect(self.duplicate)
            self.actions.addWidget(copy)
        self.actions.addStretch()
        payments = list(conn.execute("SELECT * FROM payments WHERE document_id=? ORDER BY paid_on, id", (doc_id,)))
        cancelled = {row["cancels_id"] for row in payments if row["cancels_id"]}
        if payments:
            self.extra.addWidget(QLabel("Encaissements"))
        for row in payments:
            line = QHBoxLayout()
            line.addWidget(QLabel(f"{core.date_fr(row['paid_on'])} · {core.MODES.get(row['method'], row['method'])} · {core.eur(row['amount_cents'])}"))
            if not row["cancels_id"] and row["id"] not in cancelled:
                cancel = QPushButton("Annuler la ligne")
                payment_id = row["id"]
                cancel.clicked.connect(lambda _=False, i=payment_id: self.cancel_payment(i))
                line.addWidget(cancel)
            wrap = QWidget()
            wrap.setLayout(line)
            self.extra.addWidget(wrap)
        if doc["kind"] == "facture" and doc["reste"] > 0 and doc["snapshot"]:
            letters = core.messages(json.loads(doc["snapshot"]))
            self.extra.addWidget(QLabel("Relance"))
            text = QPlainTextEdit(letters["relance"])
            text.setReadOnly(True)
            copy = QPushButton("Copier")
            copy.clicked.connect(lambda: QApplication.clipboard().setText(text.toPlainText()))
            self.extra.addWidget(text)
            self.extra.addWidget(copy)

    def pay(self, amount):
        def do():
            core.encaisser(self.host.conn, self.doc_id, amount, core.today(), "virement")
            return True

        if run(self, do):
            self.open(doc_id=self.doc_id)

    def pay_other(self):
        amount, ok = QInputDialog.getText(self, "2 facto", "Montant")
        if not ok:
            return
        parsed = run(self, lambda: core.parse_cents(amount, "le montant"))
        if parsed is None:
            return
        self.pay(parsed)

    def redo(self, paid):
        if not confirm(self, "Un avoir va annuler cette facture, puis un nouveau brouillon sera préparé. Continuer ?"):
            return
        refund = False
        if paid > 0:
            refund = confirm(self, f"Noter aussi le remboursement de {core.eur(paid)} ?")
        new_id = run(self, lambda: core.annuler_et_refaire(self.host.conn, self.doc_id, refund))
        if new_id:
            self.host.go("editor", doc_id=new_id)

    def partial(self):
        new_id = run(self, lambda: core.avoir_brouillon(self.host.conn, self.doc_id))
        if new_id:
            self.host.go("editor", doc_id=new_id)

    def status(self, value):
        def do():
            core.statut_devis(self.host.conn, self.doc_id, value)
            return True

        if run(self, do):
            self.open(doc_id=self.doc_id)

    def transform(self):
        new_id = run(self, lambda: core.facture_depuis_devis(self.host.conn, self.doc_id))
        if new_id:
            self.host.go("editor", doc_id=new_id)

    def duplicate(self):
        new_id = run(self, lambda: core.dupliquer(self.host.conn, self.doc_id))
        if new_id:
            self.host.go("editor", doc_id=new_id)

    def cancel_payment(self, payment_id):
        motif, ok = QInputDialog.getText(self, "2 facto", "Motif de l'annulation")
        if not ok:
            return
        def do():
            core.annuler_encaissement(self.host.conn, payment_id, motif)
            return True

        if run(self, do):
            self.open(doc_id=self.doc_id)


class ClientsPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.rows = []
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Clients")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        button = primary("Nouveau client")
        button.clicked.connect(lambda: self.edit(None))
        header.addWidget(title)
        header.addStretch()
        header.addWidget(button)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Nom ou e-mail")
        self.search.returnPressed.connect(self.reload)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Nom", "Type", "Ville", "E-mail"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda index: self.edit(self.rows[index.row()]["id"]))
        layout.addLayout(header)
        layout.addWidget(self.search)
        layout.addWidget(self.table)

    def open(self, **_kw):
        self.reload()

    def reload(self):
        query = self.search.text().strip()
        sql = "SELECT * FROM clients"
        args = ()
        if query:
            sql += " WHERE nom LIKE ? OR email LIKE ?"
            args = (f"%{query}%", f"%{query}%")
        sql += " ORDER BY nom"
        self.rows = [dict(row) for row in self.host.conn.execute(sql, args)]
        self.table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            values = [row["nom"], "Pro" if row["type"] == "professionnel" else "Particulier", row["ville"], row["email"]]
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))

    def edit(self, client_id):
        if ClientDialog(self.host.conn, client_id, self).exec():
            self.reload()


class RecettesPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Recettes")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(date.today().year)
        self.year.valueChanged.connect(self.reload)
        pdf = QPushButton("PDF")
        csv_button = QPushButton("CSV")
        pdf.clicked.connect(self.export_pdf)
        csv_button.clicked.connect(self.export_csv)
        header.addWidget(title)
        header.addWidget(self.year)
        header.addStretch()
        header.addWidget(pdf)
        header.addWidget(csv_button)
        self.summary = QLabel()
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Date", "Client", "Nature", "Pièce", "Mode", "Montant"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addLayout(header)
        layout.addWidget(self.summary)
        layout.addWidget(self.table)
        self.stats = None

    def open(self, **_kw):
        self.reload()

    def reload(self):
        self.stats = core.recettes(self.host.conn, self.year.value())
        self.summary.setText(
            f"Encaissé {core.eur(self.stats['encaisse'])} · cotisations environ {core.eur(self.stats['cotisations'])}"
        )
        book = self.stats["book"]
        self.table.setRowCount(len(book))
        for index, row in enumerate(book):
            values = [
                core.date_fr(row["paid_on"]),
                row["client"],
                row["objet"] or "Prestation",
                row["number"] or "",
                row["mode"],
                core.eur(row["amount_cents"]),
            ]
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))

    def export_pdf(self):
        if self.stats is None:
            self.reload()
        company = core.company(self.host.conn)
        payload = invoice.livre_pdf(company, self.year.value(), self.stats["book"], self.stats["encaisse"])
        open_bytes(payload, f"recettes-{self.year.value()}.pdf")

    def export_csv(self):
        if self.stats is None:
            self.reload()
        path, _selected = QFileDialog.getSaveFileName(self, "Exporter", f"recettes-{self.year.value()}.csv", "CSV (*.csv)")
        if not path:
            return
        buffer = io.StringIO()
        buffer.write("\ufeff")
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(["date", "client", "nature", "piece", "mode", "montant"])
        for row in self.stats["book"]:
            writer.writerow([row["paid_on"], row["client"], row["objet"], row["number"] or "", row["mode"], f"{row['amount_cents'] / 100:.2f}"])
        Path(path).write_text(buffer.getvalue(), encoding="utf-8")


class SettingsPage(QWidget):
    TEXT = (
        ("prenom", "Prénom"), ("nom", "Nom"), ("nom_commercial", "Nom commercial"),
        ("adresse", "Adresse"), ("complement", "Complément"), ("code_postal", "Code postal"),
        ("ville", "Ville"), ("email", "E-mail"), ("telephone", "Téléphone"), ("site", "Site"),
        ("siret", "SIRET"), ("code_ape", "Code APE"), ("iban", "IBAN"), ("bic", "BIC"),
        ("titulaire", "Titulaire"), ("delai_jours", "Échéance (jours)"),
        ("validite_devis", "Validité d'un devis (jours)"), ("penalites", "Pénalités"),
        ("escompte", "Escompte"), ("assurance", "Assurance"), ("mediateur", "Médiateur"),
        ("mention_libre", "Phrase en plus"),
    )

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.fields = {}
        inner = QWidget()
        form = QFormLayout(inner)
        title = QLabel("Réglages")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        form.addRow(title)
        for key, label in self.TEXT:
            widget = QTextEdit() if key in ("penalites", "escompte", "assurance", "mediateur", "mention_libre") else QLineEdit()
            if isinstance(widget, QTextEdit):
                widget.setFixedHeight(60)
            self.fields[key] = widget
            form.addRow(label, widget)
        self.regime = QComboBox()
        self.regime.addItem("Franchise en base", "franchise")
        self.regime.addItem("TVA facturée", "assujetti")
        self.taux = QComboBox()
        for bp, label in core.TAUX_TVA.items():
            self.taux.addItem(label, bp)
        self.debits = QCheckBox("Option des débits")
        self.activite = QComboBox()
        for key, rate in core.RATES.items():
            self.activite.addItem(rate["label"], key)
        self.acre = QComboBox()
        self.acre.addItem("Pas d'ACRE", "non")
        self.acre.addItem("ACRE à partir du 1er juillet 2026", "apres")
        self.acre.addItem("ACRE avant le 1er juillet 2026", "avant")
        self.vl = QCheckBox("Versement libératoire de l'impôt")
        self.period = QComboBox()
        self.period.addItem("Trimestrielle", "trimestre")
        self.period.addItem("Mensuelle", "mois")
        form.addRow("TVA", self.regime)
        form.addRow("Taux", self.taux)
        form.addRow("", self.debits)
        form.addRow("Activité", self.activite)
        form.addRow("ACRE", self.acre)
        form.addRow("", self.vl)
        form.addRow("Déclaration", self.period)
        buttons = QHBoxLayout()
        save = primary("Enregistrer")
        logo = QPushButton("Logo")
        clear = QPushButton("Retirer le logo")
        backup = QPushButton("Sauvegarde")
        journal = QPushButton("Journal")
        save.clicked.connect(self.save)
        logo.clicked.connect(self.pick_logo)
        clear.clicked.connect(self.clear_logo)
        backup.clicked.connect(self.backup)
        journal.clicked.connect(self.journal)
        for button in (save, logo, clear, backup, journal):
            buttons.addWidget(button)
        form.addRow(buttons)
        outer = QVBoxLayout(self)
        outer.addWidget(scroll(inner))

    def text_of(self, key) -> str:
        widget = self.fields[key]
        return widget.toPlainText() if isinstance(widget, QTextEdit) else widget.text()

    def set_text(self, key, value):
        widget = self.fields[key]
        if isinstance(widget, QTextEdit):
            widget.setPlainText(value or "")
        else:
            widget.setText(value or "")

    def open(self, **_kw):
        company = core.company(self.host.conn)
        for key, _label in self.TEXT:
            value = company[key]
            self.set_text(key, str(value) if not isinstance(value, str) else value)
        self.regime.setCurrentIndex(1 if company["regime_tva"] == "assujetti" else 0)
        self.taux.setCurrentIndex(max(0, self.taux.findData(company["taux_tva_bp"])))
        self.debits.setChecked(bool(company["option_debits"]))
        self.activite.setCurrentIndex(max(0, self.activite.findData(company["activite"])))
        self.acre.setCurrentIndex(max(0, self.acre.findData(company["acre"])))
        self.vl.setChecked(bool(company["versement_liberatoire"]))
        self.period.setCurrentIndex(1 if company["periodicite"] == "mois" else 0)

    def form(self):
        data = {key: self.text_of(key) for key, _label in self.TEXT}
        data.update({
            "regime_tva": self.regime.currentData(),
            "taux_tva_bp": str(self.taux.currentData()),
            "option_debits": "1" if self.debits.isChecked() else "",
            "activite": self.activite.currentData(),
            "acre": self.acre.currentData(),
            "versement_liberatoire": "1" if self.vl.isChecked() else "",
            "periodicite": self.period.currentData(),
        })
        return data

    def save(self):
        saved = run(self, lambda: clean_company(self.form(), core.company(self.host.conn), minimal=False))
        if saved is None:
            return
        core.save_company(self.host.conn, saved)
        self.host.sync_nav()
        QMessageBox.information(self, "2 facto", "Réglages enregistrés.")

    def pick_logo(self):
        path, _selected = QFileDialog.getOpenFileName(self, "Logo", "", "Images (*.png *.jpg *.jpeg)")
        if not path:
            return
        blob = Path(path).read_bytes()
        if len(blob) > 800_000:
            warn(self, "Le logo doit rester sous 800 Ko.")
            return
        mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
        self.host.conn.execute("UPDATE company SET logo=?, logo_mime=? WHERE id=1", (blob, mime))

    def clear_logo(self):
        self.host.conn.execute("UPDATE company SET logo=NULL, logo_mime='' WHERE id=1")

    def backup(self):
        path, _selected = QFileDialog.getSaveFileName(self, "Sauvegarde", "2facto-sauvegarde.db", "Base (*.db)")
        if not path:
            return
        target = sqlite3.connect(path)
        self.host.conn.backup(target)
        target.close()
        QMessageBox.information(self, "2 facto", "Sauvegarde enregistrée.")

    def journal(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Journal — 2 facto")
        dialog.resize(640, 420)
        layout = QVBoxLayout(dialog)
        rows = core.journal(self.host.conn)
        text = QPlainTextEdit("\n".join(f"{row['at']}  {row['message']}" for row in rows) or "Le journal est vide.")
        text.setReadOnly(True)
        button = QPushButton("Vérifier la chaîne")

        def verify():
            problems = core.verifier(self.host.conn)
            if problems:
                warn(dialog, "\n".join(problems))
            else:
                QMessageBox.information(dialog, "2 facto", "Journal et factures émises sont intacts.")

        button.clicked.connect(verify)
        layout.addWidget(text)
        layout.addWidget(button)
        dialog.exec()


class Main(QMainWindow):
    NAV = (
        ("home", "Accueil"),
        ("factures", "Factures"),
        ("devis", "Devis"),
        ("clients", "Clients"),
        ("recettes", "Recettes"),
        ("reglages", "Réglages"),
    )

    def __init__(self):
        super().__init__()
        self.setWindowTitle("2 facto")
        self.setWindowIcon(QIcon(str(MARK)))
        self.resize(1180, 760)
        self.conn = connect(data_dir() / "2facto.db")
        self._nav_lock = False
        self.nav = QListWidget()
        self.nav.setFixedWidth(200)
        self.nav_index = {}
        for key, label in self.NAV:
            self.nav.addItem(label)
            item = self.nav.item(self.nav.count() - 1)
            item.setData(Qt.UserRole, key)
            self.nav_index[key] = self.nav.count() - 1
        self.nav.currentRowChanged.connect(self.on_nav)
        self.stack = QStackedWidget()
        self.pages = {
            "setup": SetupPage(self),
            "home": HomePage(self),
            "factures": ListPage(self, "facture"),
            "devis": ListPage(self, "devis"),
            "avoirs": ListPage(self, "avoir"),
            "editor": EditorPage(self),
            "show": ShowPage(self),
            "clients": ClientsPage(self),
            "recettes": RecettesPage(self),
            "reglages": SettingsPage(self),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        shell = QWidget()
        layout = QHBoxLayout(shell)
        brand = QLabel()
        brand.setPixmap(QPixmap(str(LOGO)).scaledToWidth(176, Qt.SmoothTransformation))
        brand.setAlignment(Qt.AlignHCenter)
        side = QVBoxLayout()
        side.addWidget(brand)
        side.addWidget(self.nav)
        note = QLabel("Même dossier que 2facto, le programme dans le navigateur.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #5c6661; padding: 8px;")
        side.addWidget(note)
        layout.addLayout(side)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(shell)
        self.sync_nav()
        if core.ready(core.company(self.conn)):
            self.go("home")
        else:
            self.go("setup")

    def sync_nav(self):
        ready = core.ready(core.company(self.conn))
        for index in range(self.nav.count()):
            item = self.nav.item(index)
            key = item.data(Qt.UserRole)
            if ready or key == "reglages":
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            else:
                item.setFlags(Qt.NoItemFlags)

    def on_nav(self, row):
        if self._nav_lock or row < 0:
            return
        self.go(self.nav.item(row).data(Qt.UserRole))

    def go(self, name, **kwargs):
        ready = core.ready(core.company(self.conn))
        if name not in ("setup", "reglages") and not ready:
            name = "setup"
            kwargs = {}
        index = self.nav_index.get(name)
        if index is not None:
            self._nav_lock = True
            self.nav.setCurrentRow(index)
            self._nav_lock = False
        self.stack.setCurrentWidget(self.pages[name])
        self.pages[name].open(**kwargs)

    def closeEvent(self, event):
        self.conn.close()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("2 facto")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(QIcon(str(MARK)))
    window = Main()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
