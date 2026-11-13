"""PDF lisible et Factur-X EN 16931 embarqué dans le même fichier."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path

from fpdf import FPDF

from folio.core import (
    MENTION_TVA,
    NATURES,
    UNITES,
    FolioError,
    date_fr,
    eur,
    group_siret,
    pretty_iban,
    qty_fr,
    tva_intracom,
)

FONT = Path(r"C:\Windows\Fonts")
INK = (24, 33, 29)
MUTED = (92, 102, 97)
GREEN = (15, 107, 74)
LINE = (226, 230, 227)
PAPER = (255, 255, 255)


def m2(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def rate_str(bp: int) -> str:
    return f"{int(bp) // 100}.{int(bp) % 100:02d}"


def dec_str(value: Decimal) -> str:
    text = format(value, "f")
    if "." not in text:
        return text + ".00"
    left, right = text.split(".")
    right = right.rstrip("0")
    if len(right) < 2:
        right = right.ljust(2, "0")
    return f"{left}.{right}"


def fit_price(qty_bp: int, ht_cents: int):
    qty = Decimal(qty_bp) / Decimal(100)
    ht = Decimal(ht_cents) / Decimal(100)
    exact = ht / qty
    for prec in (2, 3, 4):
        net = exact.quantize(Decimal(10) ** -prec, rounding=ROUND_HALF_UP)
        if (qty * net).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == ht:
            return net, None
    return ht, qty


class Sheet(FPDF):
    draft = False
    doc_label = ""
    doc_number = ""

    def header(self):
        if self.draft:
            self.set_font("Segoe", "B", 46)
            self.set_text_color(232, 235, 233)
            with self.rotation(32, x=78, y=168):
                self.text(x=28, y=168, text="BROUILLON")
        self.set_text_color(*INK)
        if self.page_no() > 1:
            self.set_xy(self.l_margin, 10)
            self.set_font("Segoe", "", 9)
            self.set_text_color(*MUTED)
            self.cell(0, 6, f"{self.doc_label} {self.doc_number}", new_x="LMARGIN", new_y="NEXT")

    def footer(self):
        self.set_y(-12)
        self.set_font("Segoe", "", 8)
        self.set_text_color(150, 156, 152)
        label = self.doc_number or "Brouillon"
        self.cell(0, 6, f"{label}   ·   {self.page_no()}", align="C")


def _fonts(pdf: Sheet):
    regular = FONT / "segoeui.ttf"
    if not regular.exists():
        raise FolioError("La police Segoe UI est introuvable sur cet ordinateur.")
    pdf.add_font("Segoe", "", str(regular))
    pdf.add_font("Segoe", "B", str(FONT / "segoeuib.ttf"))
    pdf.add_font("Segoe", "I", str(FONT / "segoeuii.ttf"))


def _logo(blob):
    if not blob:
        return None
    try:
        from PIL import Image

        image = Image.open(BytesIO(blob))
        image.load()
        return image
    except Exception:
        return None


def _need(pdf, height):
    if pdf.get_y() + height > pdf.h - pdf.b_margin:
        pdf.add_page()


def _rule(pdf):
    y = pdf.get_y()
    pdf.set_draw_color(*LINE)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)


def _paragraph(pdf, text, size=9, style="", color=INK, h=4.4):
    pdf.set_font("Segoe", style, size)
    pdf.set_text_color(*color)
    pdf.multi_cell(0, h, text, new_x="LMARGIN", new_y="NEXT")


def render_pdf(snap: dict, logo=None, watermark=False) -> bytes:
    labels = {"facture": "FACTURE", "devis": "DEVIS", "avoir": "AVOIR"}
    pdf = Sheet(format="A4", unit="mm")
    _fonts(pdf)
    pdf.draft = watermark or not snap.get("number")
    pdf.doc_label = labels[snap["kind"]]
    pdf.doc_number = snap.get("number") or ""
    pdf.set_auto_page_break(True, margin=16)
    pdf.set_margins(16, 14, 16)
    pdf.set_title(f"{pdf.doc_label} {pdf.doc_number}".strip())
    pdf.set_author(snap["seller"]["nom_complet"])
    pdf.set_keywords(f"prev:{snap.get('prev_hash') or ''}")
    pdf.set_creator("2facto")
    pdf.add_page()
    seller = snap["seller"]
    y0 = pdf.get_y()
    image = _logo(logo)
    x_text = 16
    if image is not None:
        pdf.image(image, x=16, y=y0, h=14)
        x_text = 34
    pdf.set_xy(x_text, y0)
    pdf.set_font("Segoe", "B", 13)
    pdf.set_text_color(*INK)
    pdf.cell(110, 6, seller["nom_complet"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(x_text)
    pdf.set_font("Segoe", "", 9)
    pdf.set_text_color(*GREEN)
    pdf.cell(110, 4.5, "Entrepreneur individuel", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*MUTED)
    pdf.set_x(x_text)
    lines = []
    if seller["commercial"]:
        lines.append(seller["commercial"])
    lines.append(seller["adresse"])
    if seller["complement"]:
        lines.append(seller["complement"])
    lines.append(f"{seller['cp']} {seller['ville']}".strip())
    lines.append(f"SIREN {group_siret(seller['siren'])}")
    lines.append(f"SIRET {group_siret(seller['siret'])}")
    if seller["ape"]:
        lines.append(f"APE {seller['ape']}")
    contact = "  ·  ".join(bit for bit in (seller["email"], seller["telephone"], seller["site"]) if bit)
    if contact:
        lines.append(contact)
    pdf.set_font("Segoe", "", 8.5)
    for line in lines:
        pdf.set_x(x_text)
        pdf.cell(110, 4, line, new_x="LMARGIN", new_y="NEXT")
    left_bottom = pdf.get_y()

    pdf.set_xy(120, y0)
    pdf.set_font("Segoe", "B", 16)
    pdf.set_text_color(*INK)
    pdf.cell(74, 8, pdf.doc_label, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(120)
    pdf.set_font("Segoe", "B", 11)
    pdf.set_text_color(*GREEN)
    pdf.cell(74, 6, snap["number"] or "Brouillon", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Segoe", "", 8.5)
    pdf.set_text_color(*MUTED)
    meta = [f"Émis le {date_fr(snap['issue_date'])}" if snap["issue_date"] else "Pas encore émis"]
    if snap["service_date"] and snap["service_end"]:
        meta.append(f"Prestation du {date_fr(snap['service_date'])} au {date_fr(snap['service_end'])}")
    elif snap["service_date"]:
        meta.append(f"Prestation du {date_fr(snap['service_date'])}")
    if snap["kind"] == "facture" and snap["due_date"]:
        meta.append(f"Échéance le {date_fr(snap['due_date'])}")
    if snap["kind"] == "devis" and snap["valid_until"]:
        meta.append(f"Valable jusqu'au {date_fr(snap['valid_until'])}")
    if snap["bon_commande"]:
        meta.append(f"Bon de commande {snap['bon_commande']}")
    if snap["kind"] == "avoir" and snap.get("facture_number"):
        meta.append(f"Sur la facture {snap['facture_number']} du {date_fr(snap.get('facture_date') or '')}")
    for line in meta:
        pdf.set_x(120)
        pdf.cell(74, 4, line, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(max(left_bottom, pdf.get_y()) + 6)

    client = snap["client"]
    pdf.set_fill_color(247, 248, 247)
    pdf.set_font("Segoe", "", 8)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 5, "Facturé à", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Segoe", "B", 11)
    pdf.set_text_color(*INK)
    pdf.cell(0, 5.5, client["nom"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Segoe", "", 9)
    pdf.set_text_color(*MUTED)
    if client["contact"]:
        pdf.cell(0, 4, client["contact"], new_x="LMARGIN", new_y="NEXT")
    if not client["masquer_adresse"]:
        if client["adresse"]:
            pdf.cell(0, 4, client["adresse"], new_x="LMARGIN", new_y="NEXT")
        if client["complement"]:
            pdf.cell(0, 4, client["complement"], new_x="LMARGIN", new_y="NEXT")
        city = f"{client['cp']} {client['ville']}".strip()
        if city:
            pdf.cell(0, 4, city, new_x="LMARGIN", new_y="NEXT")
    if client["siren"]:
        pdf.cell(0, 4, f"SIREN {group_siret(client['siren'])}", new_x="LMARGIN", new_y="NEXT")
        if client["siret"]:
            pdf.cell(0, 4, f"SIRET {group_siret(client['siret'])}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    if snap["objet"]:
        pdf.set_font("Segoe", "B", 11)
        pdf.set_text_color(*INK)
        pdf.multi_cell(0, 5.5, snap["objet"], new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    widths = (78, 16, 22, 26, 18, 22)
    headers = ("Désignation", "Qté", "Unité", "PU HT", "Remise", "Montant")
    aligns = ("L", "R", "L", "R", "R", "R")
    pdf.set_font("Segoe", "B", 8)
    pdf.set_fill_color(*GREEN)
    pdf.set_text_color(255, 255, 255)
    for width, label, align in zip(widths, headers, aligns):
        pdf.cell(width, 7, label, align=align, fill=True)
    pdf.ln(7)

    pdf.set_text_color(*INK)
    for line in snap["lines"]:
        remise = f"{line['remise_bp'] / 100:.2f} %".replace(".", ",").replace(",00 %", " %")
        values = (
            line["libelle"],
            qty_fr(line["qty_bp"]),
            UNITES[line["unite"]][0],
            eur(line["prix_cents"]),
            remise if line["remise_bp"] else "—",
            eur(line["ht_cents"]),
        )
        pdf.set_font("Segoe", "", 8.5)
        parts = []
        for width, value in zip(widths, values):
            parts.append(pdf.multi_cell(width, 4.4, value, dry_run=True, output="LINES"))
        height = max(len(part) for part in parts) * 4.4 + 1.6
        _need(pdf, height + 2)
        x = pdf.l_margin
        y = pdf.get_y()
        for width, part, align in zip(widths, parts, aligns):
            pdf.set_xy(x, y + 0.8)
            if align == "R":
                pdf.set_xy(x, y + 0.8)
            text = "\n".join(part)
            pdf.multi_cell(width, 4.4, text, align=align)
            x += width
        pdf.set_xy(pdf.l_margin, y + height)
        _rule(pdf)

    totals = snap["totals"]
    pdf.ln(3)
    _need(pdf, 36)

    def total_row(label, value, bold=False):
        pdf.set_font("Segoe", "B" if bold else "", 10 if bold else 9)
        pdf.set_text_color(*INK)
        pdf.cell(132, 6, label, align="R")
        pdf.cell(50, 6, value, align="R", new_x="LMARGIN", new_y="NEXT")

    total_row("Total HT", eur(totals["lines"]))
    if totals["allowance"]:
        total_row("Remise", "− " + eur(totals["allowance"]))
        total_row("Base HT", eur(totals["base"]))
    if snap["tva_applicable"]:
        total_row(f"TVA {rate_str(snap['taux_tva_bp']).replace('.', ',')} %", eur(totals["tva"]))
    else:
        pdf.set_font("Segoe", "I", 8.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(182, 5, MENTION_TVA, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_fill_color(247, 248, 247)
    pdf.set_font("Segoe", "B", 11)
    pdf.set_text_color(*INK)
    label = "Net à déduire" if snap["kind"] == "avoir" else "Net à payer"
    pdf.cell(132, 8, label, align="R", fill=True)
    pdf.cell(50, 8, eur(totals["ttc"]), align="R", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    notes = []
    notes.append("Nature de l'opération : " + NATURES.get(snap["nature"], NATURES["prestation"]))
    liv = snap["livraison"]
    if liv["adresse"] and liv["cp"] and liv["ville"]:
        notes.append(f"Adresse de livraison : {liv['adresse']}, {liv['cp']} {liv['ville']}")
    if snap["kind"] == "facture" and seller["iban"]:
        notes.append(f"Règlement par virement à {seller['titulaire']}.")
        notes.append(f"IBAN {pretty_iban(seller['iban'])}" + (f"   BIC {seller['bic']}" if seller["bic"] else ""))
    if seller["escompte"]:
        notes.append(seller["escompte"])
    if seller["penalites"]:
        notes.append(seller["penalites"])
    if client["type"] == "professionnel":
        notes.append("Indemnité forfaitaire pour frais de recouvrement en cas de retard : 40 €.")
    if client["type"] == "particulier" and seller["mediateur"]:
        notes.append(f"Médiateur de la consommation : {seller['mediateur']}")
    if seller["assurance"]:
        notes.append(f"Assurance : {seller['assurance']}")
    if snap["option_debits"]:
        notes.append("Option pour le paiement de la taxe d'après les débits.")
    if snap["tva_applicable"]:
        notes.append(f"N° TVA {tva_intracom(seller['siren'])}")
    if seller["mention_libre"]:
        notes.append(seller["mention_libre"])
    if snap["notes"]:
        notes.append(snap["notes"])
    if pdf.draft:
        notes.append("Brouillon : ce document n'est pas une facture et n'a pas de numéro définitif.")
    _need(pdf, 8 + 4 * min(len(notes), 6))
    pdf.set_font("Segoe", "", 8)
    pdf.set_text_color(*MUTED)
    for note in notes:
        pdf.multi_cell(0, 3.8, note, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def render_xml(snap: dict) -> bytes:
    from facturx import generate_cii_xml

    seller = snap["seller"]
    client = snap["client"]
    totals = snap["totals"]
    cat = "S" if snap["tva_applicable"] else "E"
    rate = rate_str(snap["taux_tva_bp"] if snap["tva_applicable"] else 0)
    seller_party = {
        "name": seller["nom_complet"],
        "legal_info": "Entrepreneur individuel",
        "legal_identifier": seller["siret"],
        "legal_identifier_schemeid": "0002",
        "addr_l1": seller["adresse"],
        "postcode": seller["cp"],
        "city": seller["ville"],
        "country_code": "FR",
        "einvoicing_addr": seller["siren"],
        "einvoicing_addr_schemeid": "0225",
    }
    if seller["complement"]:
        seller_party["addr_l2"] = seller["complement"]
    if seller["commercial"]:
        seller_party["biz_name"] = seller["commercial"]
    if snap["tva_applicable"]:
        seller_party["vat_identifier"] = tva_intracom(seller["siren"])
    else:
        seller_party["tax_identifier"] = seller["siren"]
    contact = {"name": seller["nom_complet"]}
    if seller["email"]:
        contact["email"] = seller["email"]
    if seller["telephone"]:
        contact["phone"] = seller["telephone"]
    if seller["email"] or seller["telephone"]:
        seller_party["contacts"] = [contact]

    buyer = {"name": client["nom"], "country_code": client["pays"] or "FR"}
    if client["siret"]:
        buyer["legal_identifier"] = client["siret"]
        buyer["legal_identifier_schemeid"] = "0002"
        if client["siren"] and client["pays"] == "FR":
            buyer["einvoicing_addr"] = client["siren"]
            buyer["einvoicing_addr_schemeid"] = "0225"
    if client["adresse"]:
        buyer["addr_l1"] = client["adresse"]
    if client["complement"]:
        buyer["addr_l2"] = client["complement"]
    if client["cp"]:
        buyer["postcode"] = client["cp"]
    if client["ville"]:
        buyer["city"] = client["ville"]

    notes = []
    if not snap["tva_applicable"]:
        notes.append({"BT-21": "REG", "BT-22": MENTION_TVA})
    notes.append({"BT-21": "REG", "BT-22": "Nature de l'opération : " + NATURES[snap["nature"]]})
    if snap["option_debits"]:
        notes.append({"BT-21": "REG", "BT-22": "Option pour le paiement de la taxe d'après les débits."})
    if seller["penalites"]:
        notes.append({"BT-21": "PMD", "BT-22": seller["penalites"]})
    if client["type"] == "professionnel":
        notes.append({
            "BT-21": "PMD",
            "BT-22": "Indemnité forfaitaire pour frais de recouvrement en cas de retard : 40 EUR.",
        })
    if snap["objet"]:
        notes.append({"BT-21": "AAI", "BT-22": "Objet : " + snap["objet"]})

    terms = []
    if snap["due_date"]:
        terms.append(f"Échéance le {date_fr(snap['due_date'])}.")
    if seller["escompte"]:
        terms.append(seller["escompte"])

    data = {
        "BT-1": snap["number"],
        "BT-2": snap["issue_date"],
        "BT-3": "381" if snap["kind"] == "avoir" else "380",
        "BT-5": "EUR",
        "BT-20": " ".join(terms),
        "BT-81": "30" if seller["iban"] else "1",
        "BT-106": m2(totals["lines"]),
        "BT-109": m2(totals["base"]),
        "BT-110": m2(totals["tva"]),
        "BT-112": m2(totals["ttc"]),
        "BT-115": m2(totals["ttc"]),
        "BG-1": notes,
        "BG-4": seller_party,
        "BG-7": buyer,
        "BG-23": [{
            "BT-116": m2(totals["base"]),
            "BT-117": m2(totals["tva"]),
            "BT-118": cat,
            "BT-119": rate,
            **({"BT-120": MENTION_TVA, "BT-121": "VATEX-FR-FRANCHISE"} if cat == "E" else {}),
        }],
        "BG-25": [],
    }
    if snap["due_date"] and snap["kind"] == "facture":
        data["BT-9"] = snap["due_date"]
    if snap["bon_commande"]:
        data["BT-13"] = snap["bon_commande"]
    if snap["service_end"]:
        data["BT-72"] = snap["service_end"]
        if snap["service_date"]:
            data["BT-73"] = snap["service_date"]
            data["BT-74"] = snap["service_end"]
    elif snap["service_date"]:
        data["BT-72"] = snap["service_date"]
    if seller["iban"]:
        data["BT-84"] = seller["iban"]
        if seller["bic"]:
            data["BT-86"] = seller["bic"]
        if seller["titulaire"]:
            data["BT-85"] = seller["titulaire"]
    if totals["allowance"]:
        data["BT-107"] = m2(totals["allowance"])
        data["BG-20"] = [{
            "amount": m2(totals["allowance"]),
            "reason": "Remise",
            "vat_category_code": cat,
            "vat_rate": rate,
        }]
    liv = snap["livraison"]
    if liv["adresse"] and liv["cp"] and liv["ville"]:
        data["BG-13"] = {
            "name": client["nom"],
            "addr_l1": liv["adresse"],
            "postcode": liv["cp"],
            "city": liv["ville"],
            "country_code": client["pays"] or "FR",
        }
    if snap["kind"] == "avoir" and snap.get("facture_number"):
        ref = {"BT-25": snap["facture_number"]}
        if snap.get("facture_date"):
            ref["BT-26"] = snap["facture_date"]
        data["BG-3"] = [ref]
    for index, line in enumerate(snap["lines"], start=1):
        net, base = fit_price(line["qty_bp"], line["ht_cents"])
        item = {
            "BT-126": str(index),
            "BT-129": f"{line['qty_bp'] / 100:.2f}",
            "BT-130": UNITES[line["unite"]][1],
            "BT-131": m2(line["ht_cents"]),
            "BT-146": dec_str(net),
            "BT-151": cat,
            "BT-152": rate,
            "BT-153": line["libelle"],
        }
        if base is not None:
            item["BT-149"] = dec_str(base)
        data["BG-25"].append(item)
    try:
        return generate_cii_xml(data, level="en16931", check_xsd=True, check_schematron=False)
    except Exception as err:
        raise FolioError(
            "Le fichier structuré a été refusé. Vérifiez les adresses, le SIRET et les montants. "
            + str(err).splitlines()[0]
        ) from err


def embed(pdf: bytes, xml: bytes, snap: dict) -> bytes:
    from facturx import generate_from_binary

    meta = {
        "author": snap["seller"]["nom_complet"],
        "title": snap["number"],
        "subject": f"Factur-X {snap['number']}",
        "keywords": f"Factur-X, prev:{snap.get('prev_hash') or ''}",
    }
    try:
        out = generate_from_binary(
            pdf,
            xml,
            flavor="factur-x",
            level="en16931",
            check_xsd=False,
            check_schematron=False,
            lang="fr",
            afrelationship="alternative",
            pdf_metadata=meta,
        )
    except Exception as err:
        raise FolioError("Le PDF Factur-X n'a pas pu être composé. " + str(err).splitlines()[0]) from err
    return bytes(out)


def livre_pdf(c, year, book, total) -> bytes:
    pdf = Sheet(format="A4", unit="mm")
    _fonts(pdf)
    pdf.doc_label = "RECETTES"
    pdf.doc_number = str(year)
    pdf.set_auto_page_break(True, margin=16)
    pdf.set_margins(14, 14, 14)
    pdf.add_page()
    pdf.set_font("Segoe", "B", 16)
    pdf.set_text_color(*INK)
    pdf.cell(0, 8, f"Livre des recettes {year}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Segoe", "", 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 5, f"{c['prenom']} {c['nom']}  ·  Entrepreneur individuel  ·  SIRET {group_siret(c['siret'])}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    headers = (24, 48, 42, 28, 20, 24)
    titles = ("Date", "Client", "Nature", "Pièce", "Mode", "Montant")
    pdf.set_font("Segoe", "B", 8)
    pdf.set_fill_color(*GREEN)
    pdf.set_text_color(255, 255, 255)
    for width, title in zip(headers, titles):
        pdf.cell(width, 7, title, fill=True)
    pdf.ln(7)
    pdf.set_font("Segoe", "", 8)
    pdf.set_text_color(*INK)
    for row in book:
        _need(pdf, 7)
        values = (
            date_fr(row["paid_on"]),
            row["client"],
            row["objet"] or "Prestation",
            row["number"] or "",
            row["mode"],
            eur(row["amount_cents"]),
        )
        for width, value in zip(headers, values):
            pdf.cell(width, 6, value[:28])
        pdf.ln(6)
        _rule(pdf)
    pdf.ln(3)
    pdf.set_font("Segoe", "B", 10)
    pdf.cell(0, 7, f"Total encaissé {eur(total)}", align="R")
    return bytes(pdf.output())
