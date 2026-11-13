"""Règles de facturation : montants, numéros, verrou, journal."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from folio.db import connect

ZERO = "0" * 64
MENTION_TVA = "TVA non applicable, art. 293 B du CGI"
UNITES = {
    "forfait": ("Forfait", "C62"),
    "heure": ("Heure", "HUR"),
    "jour": ("Jour", "DAY"),
    "mois": ("Mois", "MON"),
    "unite": ("Unité", "C62"),
}
NATURES = {
    "prestation": "Prestation de services",
    "biens": "Livraison de biens",
    "mixte": "Livraison de biens et prestation de services",
}
MODES = {
    "virement": "Virement",
    "cheque": "Chèque",
    "carte": "Carte",
    "especes": "Espèces",
    "autre": "Autre",
}
TAUX_TVA = {2000: "20 %", 1000: "10 %", 550: "5,5 %", 210: "2,1 %"}
RATES = {
    "bic_services": {
        "label": "Services commerciaux — sites et logiciels",
        "social": 2120,
        "acre_avant": 1060,
        "acre_apres": 1590,
        "vl": 170,
        "micro": 8_360_000,
        "tva": 3_750_000,
        "tva_maj": 4_125_000,
    },
    "bnc": {
        "label": "Profession libérale",
        "social": 2560,
        "acre_avant": 1280,
        "acre_apres": 1920,
        "vl": 220,
        "micro": 8_360_000,
        "tva": 3_750_000,
        "tva_maj": 4_125_000,
    },
}
MOIS = (
    "janvier février mars avril mai juin juillet août "
    "septembre octobre novembre décembre"
).split()


class FolioError(Exception):
    pass


class Tx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, et, ev, tb):
        if et:
            self.conn.execute("ROLLBACK")
        else:
            self.conn.execute("COMMIT")
        return False


def guard(err: sqlite3.Error):
    msg = str(err).lower()
    if "verrouille" in msg or "fige" in msg:
        raise FolioError("Ce document est émis. Il ne se modifie plus et ne se supprime pas.") from err
    if "journal" in msg:
        raise FolioError("Cette ligne fait partie du journal. Elle ne s'efface pas.") from err
    if "numerotation" in msg:
        raise FolioError("La numérotation ne revient pas en arrière.") from err
    if "paiement" in msg:
        raise FolioError("Un paiement se note seulement sur une facture émise.") from err
    raise FolioError("L'enregistrement a été refusé.") from err


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return date.today().isoformat()


def clip(value, size: int) -> str:
    return (value or "").strip()[:size]


def digits(value) -> str:
    return re.sub(r"\D", "", value or "")


def siret_ok(value) -> bool:
    raw = digits(value)
    if len(raw) != 14 or raw == "0" * 14:
        return False
    total = 0
    for i, ch in enumerate(raw):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def siren_of(siret) -> str:
    return digits(siret)[:9]


def group_siret(value) -> str:
    raw = digits(value)
    if len(raw) == 14:
        return f"{raw[:3]} {raw[3:6]} {raw[6:9]} {raw[9:]}"
    if len(raw) == 9:
        return f"{raw[:3]} {raw[3:6]} {raw[6:]}"
    return value or ""


def iban_ok(value) -> bool:
    raw = re.sub(r"\s+", "", value or "").upper()
    if not 15 <= len(raw) <= 34 or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", raw):
        return False
    rearr = raw[4:] + raw[:4]
    num = "".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in rearr)
    return int(num) % 97 == 1


def pretty_iban(value) -> str:
    raw = re.sub(r"\s+", "", value or "").upper()
    return " ".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def bic_ok(value) -> bool:
    return bool(re.fullmatch(r"[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?", value or ""))


def email_ok(value) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value or ""))


def tva_intracom(siren: str) -> str:
    key = (12 + 3 * (int(siren) % 97)) % 97
    return f"FR{key:02d}{siren}"


def date_fr(iso: str) -> str:
    if not iso:
        return ""
    y, m, d = iso.split("-")
    return f"{int(d)} {MOIS[int(m) - 1]} {y}"


def add_days(iso: str, days: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def eur(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    whole, frac = divmod(cents, 100)
    grouped = f"{whole:,}".replace(",", " ")
    return f"{sign}{grouped},{frac:02d} €"


def input_money(cents: int) -> str:
    return f"{int(cents) // 100},{int(cents) % 100:02d}"


def qty_fr(qty_bp: int) -> str:
    whole, frac = divmod(int(qty_bp), 100)
    if frac == 0:
        return str(whole)
    text = f"{whole},{frac:02d}".rstrip("0")
    return text


def parse_date(value, label: str, required=False) -> str:
    value = (value or "").strip()
    if not value:
        if required:
            raise FolioError(f"Indiquez {label}.")
        return ""
    try:
        date.fromisoformat(value)
    except ValueError as err:
        raise FolioError(f"{label} n'est pas une date valide.") from err
    return value


def _decimal(value, label: str) -> Decimal:
    raw = (value or "").strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not raw:
        raise FolioError(f"Indiquez {label}.")
    try:
        return Decimal(raw)
    except Exception as err:
        raise FolioError(f"{label} n'est pas un nombre.") from err


def parse_cents(value, label="le prix") -> int:
    amount = _decimal(value, label)
    if amount < 0 or amount > Decimal("100000"):
        raise FolioError(f"{label.capitalize()} doit rester entre 0 et 100 000 €.")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_qty_bp(value) -> int:
    raw = (value or "").strip()
    amount = Decimal("1") if not raw else _decimal(raw, "la quantité")
    if amount <= 0 or amount > Decimal("500"):
        raise FolioError("La quantité doit être comprise entre 0 et 500.")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_percent_bp(value) -> int:
    raw = (value or "").strip()
    if not raw:
        return 0
    amount = _decimal(raw, "la remise")
    if amount < 0 or amount > 100:
        raise FolioError("La remise est comprise entre 0 et 100 %.")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def line_cents(qty_bp: int, price_cents: int, remise_bp: int) -> int:
    num = qty_bp * price_cents * (10000 - remise_bp)
    return (num + 500_000) // 1_000_000


def allowance_cents(ht_cents: int, remise_bp: int) -> int:
    return (ht_cents * remise_bp + 5_000) // 10_000


def vat_cents(base_cents: int, taux_bp: int) -> int:
    return (base_cents * taux_bp + 5_000) // 10_000


def prorata(amount: int, part: int, total: int) -> int:
    if not total:
        return 0
    num = amount * part
    sign = -1 if num < 0 else 1
    return sign * ((abs(num) + total // 2) // total)


def one(conn, sql, args=()):
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def company(conn, with_logo=False):
    row = dict(conn.execute("SELECT * FROM company WHERE id=1").fetchone())
    row["has_logo"] = bool(row.get("logo"))
    if not with_logo:
        row.pop("logo", None)
    return row


def ready(c) -> bool:
    needed = ("prenom", "nom", "adresse", "code_postal", "ville", "email")
    return all((c.get(k) or "").strip() for k in needed) and siret_ok(c.get("siret", ""))


def rates_for(c) -> dict:
    base = RATES.get(c["activite"], RATES["bic_services"])
    if c["acre"] == "avant":
        social = base["acre_avant"]
    elif c["acre"] == "apres":
        social = base["acre_apres"]
    else:
        social = base["social"]
    vl = base["vl"] if c["versement_liberatoire"] else 0
    return {**base, "social": social, "vl": vl, "total": social + vl}


def rate_label(bp: int) -> str:
    whole, frac = divmod(bp, 100)
    if frac == 0:
        return f"{whole} %"
    return f"{whole},{frac:02d}".rstrip("0") + " %"


def audit(conn, action: str, message: str, document_id=None):
    detail = json.dumps({"message": message}, ensure_ascii=False, separators=(",", ":"))
    prev = conn.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = prev["hash"] if prev else ZERO
    at = now()
    raw = f"{prev_hash}|{at}|{action}|{document_id or ''}|{detail}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    conn.execute(
        "INSERT INTO audit(at, action, document_id, detail, prev_hash, hash) VALUES (?,?,?,?,?,?)",
        (at, action, document_id, detail, prev_hash, digest),
    )


def _client(conn, client_id):
    row = one(conn, "SELECT * FROM clients WHERE id=?", (client_id,))
    if not row:
        raise FolioError("Choisissez un client.")
    return row


def _doc(conn, doc_id):
    row = one(conn, "SELECT * FROM documents WHERE id=?", (doc_id,))
    if not row:
        raise FolioError("Ce document est introuvable.")
    return row


def lines_of(conn, doc_id):
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM lines WHERE document_id=? ORDER BY position, id", (doc_id,)
        )
    ]


def _totals(lines, remise_bp, tva_on, taux_bp):
    ht = sum(line_cents(line["qty_bp"], line["prix_cents"], line["remise_bp"]) for line in lines)
    allowance = allowance_cents(ht, remise_bp)
    base = ht - allowance
    tva = vat_cents(base, taux_bp) if tva_on else 0
    return ht, allowance, base, tva, base + tva


def _replace_lines(conn, doc_id, lines):
    conn.execute("DELETE FROM lines WHERE document_id=?", (doc_id,))
    for i, line in enumerate(lines, start=1):
        ht = line_cents(line["qty_bp"], line["prix_cents"], line["remise_bp"])
        line["ht_cents"] = ht
        conn.execute(
            """
            INSERT INTO lines(document_id, position, libelle, qty_bp, unite, prix_cents, remise_bp, ht_cents)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                doc_id,
                i,
                line["libelle"],
                line["qty_bp"],
                line["unite"],
                line["prix_cents"],
                line["remise_bp"],
                ht,
            ),
        )


def enregistrer(conn, doc_id, data):
    kind = data["kind"]
    if kind not in ("devis", "facture", "avoir"):
        raise FolioError("Type de document inconnu.")
    client = _client(conn, data["client_id"])
    if not data["lines"]:
        raise FolioError("Ajoutez au moins une ligne.")
    if len(data["lines"]) > 40:
        raise FolioError("Quarante lignes maximum.")
    liv = (data["livraison_adresse"], data["livraison_cp"], data["livraison_ville"])
    if any(liv) and not all(liv):
        raise FolioError("Complétez l'adresse de livraison, ou laissez les trois champs vides.")
    c = company(conn)
    tva_on = c["regime_tva"] == "assujetti"
    taux = c["taux_tva_bp"] if tva_on else 0
    ht, allowance, base, tva, ttc = _totals(data["lines"], data["remise_bp"], tva_on, taux)
    fields = (
        "client_id", "objet", "issue_date", "service_date", "service_end", "due_date",
        "valid_until", "bon_commande", "notes", "nature", "livraison_adresse",
        "livraison_cp", "livraison_ville", "remise_bp", "total_ht_cents",
        "total_tva_cents", "total_ttc_cents", "tva_applicable", "taux_tva_bp",
    )
    values = (
        client["id"], data["objet"], data["issue_date"], data["service_date"],
        data["service_end"], data["due_date"], data["valid_until"], data["bon_commande"],
        data["notes"], data["nature"], data["livraison_adresse"], data["livraison_cp"],
        data["livraison_ville"], data["remise_bp"], base, tva, ttc, int(tva_on), taux,
    )
    try:
        with Tx(conn):
            if doc_id is None:
                cur = conn.execute(
                    f"""
                    INSERT INTO documents(kind, status, created_at, {", ".join(fields)})
                    VALUES ('{kind}', 'brouillon', ?, {", ".join("?" * len(fields))})
                    """,
                    (now(), *values),
                )
                doc_id = cur.lastrowid
                if kind == "avoir":
                    conn.execute(
                        "UPDATE documents SET facture_id=? WHERE id=?",
                        (data["facture_id"], doc_id),
                    )
                audit(conn, "creation", "Brouillon créé, aucun numéro consommé.", doc_id)
            else:
                doc = _doc(conn, doc_id)
                if doc["locked"]:
                    raise FolioError("Ce document est émis. Pour le corriger, créez un avoir.")
                conn.execute(
                    f"UPDATE documents SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                    (*values, doc_id),
                )
            _replace_lines(conn, doc_id, data["lines"])
    except sqlite3.Error as err:
        guard(err)
    return doc_id


def _seq_kind(kind):
    return "devis" if kind == "devis" else "facture"


def prochain_numero(conn, kind, year: int) -> str:
    prefix = "D" if kind == "devis" else "F"
    row = conn.execute(
        "SELECT last FROM sequences WHERE kind=? AND year=?", (_seq_kind(kind), year)
    ).fetchone()
    n = (row["last"] if row else 0) + 1
    return f"{prefix}{year}-{n:04d}"


def _prendre_numero(conn, kind, year: int) -> str:
    seq = _seq_kind(kind)
    prefix = "D" if kind == "devis" else "F"
    row = conn.execute(
        "SELECT last FROM sequences WHERE kind=? AND year=?", (seq, year)
    ).fetchone()
    n = (row["last"] if row else 0) + 1
    if row:
        conn.execute(
            "UPDATE sequences SET last=? WHERE kind=? AND year=?", (n, seq, year)
        )
    else:
        conn.execute(
            "INSERT INTO sequences(kind, year, last) VALUES (?,?,?)", (seq, year, n)
        )
    return f"{prefix}{year}-{n:04d}"


def _derniere_date(conn, kind, year: int) -> str:
    kinds = ("devis",) if kind == "devis" else ("facture", "avoir")
    marks = ",".join("?" * len(kinds))
    row = conn.execute(
        f"""
        SELECT issue_date FROM documents
        WHERE locked=1 AND kind IN ({marks}) AND substr(issue_date, 1, 4)=?
        ORDER BY issue_date DESC, id DESC LIMIT 1
        """,
        (*kinds, str(year)),
    ).fetchone()
    return row["issue_date"] if row else ""


def _prev_hash(conn, kind) -> str:
    kinds = ("devis",) if kind == "devis" else ("facture", "avoir")
    marks = ",".join("?" * len(kinds))
    row = conn.execute(
        f"""
        SELECT content_hash FROM documents
        WHERE locked=1 AND kind IN ({marks})
        ORDER BY issued_at DESC, id DESC LIMIT 1
        """,
        kinds,
    ).fetchone()
    return row["content_hash"] if row else ZERO


def credit_restant(conn, facture_id, sauf=None) -> int:
    facture = _doc(conn, facture_id)
    sql = """
      SELECT COALESCE(SUM(total_ttc_cents), 0) AS s
      FROM documents WHERE kind='avoir' AND locked=1 AND facture_id=?
    """
    args = [facture_id]
    if sauf:
        sql += " AND id!=?"
        args.append(sauf)
    used = conn.execute(sql, args).fetchone()["s"]
    return facture["total_ttc_cents"] - used


def _seller_client_ok(c, client, kind):
    if not ready(c):
        raise FolioError("Complétez votre identité et votre SIRET avant d'émettre.")
    if kind == "devis":
        return
    if client["type"] == "professionnel" and client["pays"] == "FR" and not siret_ok(client["siret"]):
        raise FolioError(
            "Ce client professionnel français a besoin d'un SIRET. "
            "Ajoutez-le sur sa fiche, puis émettez."
        )


def make_snapshot(c, client, doc, lines, number, prev_hash):
    ht = sum(line["ht_cents"] for line in lines)
    allowance = allowance_cents(ht, doc["remise_bp"])
    base = ht - allowance
    tva = vat_cents(base, doc["taux_tva_bp"]) if doc["tva_applicable"] else 0
    return {
        "kind": doc["kind"],
        "number": number,
        "issue_date": doc["issue_date"],
        "service_date": doc["service_date"],
        "service_end": doc["service_end"],
        "due_date": doc["due_date"],
        "valid_until": doc["valid_until"],
        "bon_commande": doc["bon_commande"],
        "objet": doc["objet"],
        "notes": doc["notes"],
        "nature": doc["nature"],
        "livraison": {
            "adresse": doc["livraison_adresse"],
            "cp": doc["livraison_cp"],
            "ville": doc["livraison_ville"],
        },
        "remise_bp": doc["remise_bp"],
        "totals": {
            "lines": ht,
            "allowance": allowance,
            "base": base,
            "tva": tva,
            "ttc": base + tva,
        },
        "tva_applicable": bool(doc["tva_applicable"]),
        "taux_tva_bp": doc["taux_tva_bp"],
        "mention_tva": MENTION_TVA,
        "option_debits": bool(c["option_debits"]) and bool(doc["tva_applicable"]),
        "seller": {
            "prenom": c["prenom"],
            "nom": c["nom"],
            "nom_complet": f"{c['prenom']} {c['nom']}".strip(),
            "commercial": c["nom_commercial"],
            "adresse": c["adresse"],
            "complement": c["complement"],
            "cp": c["code_postal"],
            "ville": c["ville"],
            "email": c["email"],
            "telephone": c["telephone"],
            "site": c["site"],
            "siret": digits(c["siret"]),
            "siren": siren_of(c["siret"]),
            "ape": c["code_ape"],
            "iban": re.sub(r"\s+", "", c["iban"]).upper(),
            "bic": c["bic"],
            "titulaire": c["titulaire"] or f"{c['prenom']} {c['nom']}".strip(),
            "escompte": c["escompte"],
            "penalites": c["penalites"],
            "assurance": c["assurance"],
            "mediateur": c["mediateur"],
            "mention_libre": c["mention_libre"],
            "delai": c["delai_jours"],
        },
        "client": {
            "type": client["type"],
            "nom": client["nom"],
            "contact": client["contact"],
            "adresse": "" if client["masquer_adresse"] else client["adresse"],
            "complement": "" if client["masquer_adresse"] else client["complement"],
            "cp": "" if client["masquer_adresse"] else client["code_postal"],
            "ville": "" if client["masquer_adresse"] else client["ville"],
            "pays": client["pays"],
            "email": client["email"],
            "siret": digits(client["siret"]),
            "siren": siren_of(client["siret"]) if client["siret"] else "",
            "masquer_adresse": bool(client["masquer_adresse"]),
        },
        "lines": [
            {
                "libelle": line["libelle"],
                "qty_bp": line["qty_bp"],
                "unite": line["unite"],
                "prix_cents": line["prix_cents"],
                "remise_bp": line["remise_bp"],
                "ht_cents": line["ht_cents"],
            }
            for line in lines
        ],
        "facture_number": "",
        "facture_date": "",
        "prev_hash": prev_hash,
    }


def _prepare_dates(conn, doc, c):
    issue = doc["issue_date"] or today()
    service = doc["service_date"]
    end = doc["service_end"]
    due = doc["due_date"]
    valid = doc["valid_until"]
    if doc["kind"] in ("facture", "avoir") and not service:
        raise FolioError("Indiquez la date de la prestation.")
    if end and service and end < service:
        raise FolioError("La fin de prestation ne peut pas précéder le début.")
    if doc["kind"] == "facture" and not due:
        due = add_days(issue, int(c["delai_jours"]))
    if doc["kind"] == "devis" and not valid:
        valid = add_days(issue, int(c["validite_devis"]))
    if due and due < issue:
        raise FolioError("L'échéance ne peut pas précéder la date d'émission.")
    year = int(issue[:4])
    last = _derniere_date(conn, doc["kind"], year)
    if last and issue < last:
        raise FolioError(
            f"La dernière pièce de {year} est datée du {date_fr(last)}. "
            "Choisissez cette date, ou une date plus récente."
        )
    return issue, service, end, due, valid


def emettre(conn, doc_id):
    from folio import invoice

    try:
        with Tx(conn):
            doc = _doc(conn, doc_id)
            if doc["locked"]:
                raise FolioError("Ce document est déjà émis.")
            lines = lines_of(conn, doc_id)
            if not lines or doc["total_ttc_cents"] <= 0:
                raise FolioError("Le montant à émettre doit être supérieur à zéro.")
            c = company(conn, with_logo=True)
            client = _client(conn, doc["client_id"])
            _seller_client_ok(c, client, doc["kind"])
            issue, service, end, due, valid = _prepare_dates(conn, doc, c)
            if doc["kind"] == "avoir":
                room = credit_restant(conn, doc["facture_id"], sauf=doc_id)
                if doc["total_ttc_cents"] > room:
                    raise FolioError(
                        f"Cet avoir dépasse le solde annulable ({eur(room)})."
                    )
            doc.update(
                issue_date=issue,
                service_date=service,
                service_end=end,
                due_date=due,
                valid_until=valid,
            )
            number = _prendre_numero(conn, doc["kind"], int(issue[:4]))
            prev = _prev_hash(conn, doc["kind"])
            snap = make_snapshot(c, client, doc, lines, number, prev)
            if doc["kind"] == "avoir":
                origin = _doc(conn, doc["facture_id"])
                snap["facture_number"] = origin["number"]
                snap["facture_date"] = origin["issue_date"]
            logo = c.get("logo")
            pdf = invoice.render_pdf(snap, logo, watermark=False)
            xml = None
            if doc["kind"] != "devis":
                xml = invoice.render_xml(snap)
                pdf = invoice.embed(pdf, xml, snap)
            digest = hashlib.sha256(pdf).hexdigest()
            status = "envoye" if doc["kind"] == "devis" else "emise"
            conn.execute(
                """
                UPDATE documents SET
                  number=?, status=?, issue_date=?, service_date=?, service_end=?,
                  due_date=?, valid_until=?, snapshot=?, pdf=?, xml=?,
                  content_hash=?, prev_hash=?, locked=1, issued_at=?
                WHERE id=? AND locked=0
                """,
                (
                    number, status, issue, service, end, due, valid,
                    json.dumps(snap, ensure_ascii=False), pdf, xml,
                    digest, prev, now(), doc_id,
                ),
            )
            if doc["devis_id"]:
                conn.execute(
                    "UPDATE documents SET status='transforme' WHERE id=? AND kind='devis' AND locked=1",
                    (doc["devis_id"],),
                )
            label = {"devis": "Devis", "facture": "Facture", "avoir": "Avoir"}[doc["kind"]]
            audit(
                conn,
                "emission",
                f"{label} {number} émis, {eur(doc['total_ttc_cents'])}.",
                doc_id,
            )
    except sqlite3.Error as err:
        guard(err)
    return _doc(conn, doc_id)


def supprimer(conn, doc_id):
    doc = _doc(conn, doc_id)
    if doc["locked"]:
        raise FolioError("Un document émis ne se supprime pas. Créez un avoir.")
    linked = conn.execute(
        "SELECT 1 FROM documents WHERE devis_id=? AND id!=?", (doc_id, doc_id)
    ).fetchone()
    if linked:
        raise FolioError("Ce devis a déjà une facture.")
    try:
        with Tx(conn):
            conn.execute("DELETE FROM documents WHERE id=? AND locked=0", (doc_id,))
            audit(conn, "suppression", "Brouillon supprimé, aucun numéro consommé.", doc_id)
    except sqlite3.Error as err:
        guard(err)


def dupliquer(conn, doc_id):
    doc = _doc(conn, doc_id)
    if doc["kind"] == "avoir":
        raise FolioError("Dupliquez la facture d'origine plutôt que l'avoir.")
    lines = lines_of(conn, doc_id)
    data = _data_from(doc, lines, kind=doc["kind"])
    new_id = enregistrer(conn, None, data)
    return new_id


def _data_from(doc, lines, kind=None, facture_id=None):
    return {
        "kind": kind or doc["kind"],
        "client_id": doc["client_id"],
        "facture_id": facture_id,
        "objet": doc["objet"],
        "issue_date": today(),
        "service_date": doc["service_date"],
        "service_end": doc["service_end"],
        "due_date": "",
        "valid_until": "",
        "bon_commande": doc["bon_commande"],
        "notes": doc["notes"],
        "nature": doc["nature"],
        "livraison_adresse": doc["livraison_adresse"],
        "livraison_cp": doc["livraison_cp"],
        "livraison_ville": doc["livraison_ville"],
        "remise_bp": doc["remise_bp"],
        "lines": [
            {
                "libelle": line["libelle"],
                "qty_bp": line["qty_bp"],
                "unite": line["unite"],
                "prix_cents": line["prix_cents"],
                "remise_bp": line["remise_bp"],
            }
            for line in lines
        ],
    }


def facture_depuis_devis(conn, devis_id):
    devis = _doc(conn, devis_id)
    if devis["kind"] != "devis" or not devis["locked"]:
        raise FolioError("Émettez le devis avant de le transformer, ou créez la facture directement.")
    if devis["status"] == "refuse":
        raise FolioError("Ce devis est refusé. Dupliquez-le pour repartir.")
    existing = conn.execute(
        "SELECT id, locked FROM documents WHERE kind='facture' AND devis_id=? ORDER BY id DESC",
        (devis_id,),
    ).fetchall()
    for row in existing:
        if row["locked"]:
            raise FolioError("Ce devis a déjà été transformé. Dupliquez la facture s'il en faut une autre.")
        return row["id"]
    lines = lines_of(conn, devis_id)
    data = _data_from(devis, lines, kind="facture")
    new_id = enregistrer(conn, None, data)
    conn.execute("UPDATE documents SET devis_id=? WHERE id=?", (devis_id, new_id))
    return new_id


def avoir_brouillon(conn, facture_id):
    facture = _doc(conn, facture_id)
    if facture["kind"] != "facture" or not facture["locked"]:
        raise FolioError("L'avoir se crée à partir d'une facture émise.")
    draft = conn.execute(
        "SELECT id FROM documents WHERE kind='avoir' AND locked=0 AND facture_id=?",
        (facture_id,),
    ).fetchone()
    if draft:
        return draft["id"]
    if credit_restant(conn, facture_id) <= 0:
        raise FolioError("Cette facture est déjà annulée.")
    lines = lines_of(conn, facture_id)
    data = _data_from(facture, lines, kind="avoir", facture_id=facture_id)
    data["objet"] = f"Avoir sur {facture['number']}"
    return enregistrer(conn, None, data)


def annuler_et_refaire(conn, facture_id, rembourser: bool):
    facture = _doc(conn, facture_id)
    if facture["kind"] != "facture" or not facture["locked"]:
        raise FolioError("Seule une facture émise peut être annulée ainsi.")
    if conn.execute(
        "SELECT 1 FROM documents WHERE kind='avoir' AND locked=1 AND facture_id=?",
        (facture_id,),
    ).fetchone():
        raise FolioError("Un avoir existe déjà. Ajustez le solde avec un avoir partiel.")
    avoir_id = avoir_brouillon(conn, facture_id)
    emettre(conn, avoir_id)
    if rembourser:
        rembourser_tout(conn, facture_id)
    lines = lines_of(conn, facture_id)
    data = _data_from(facture, lines, kind="facture")
    new_id = enregistrer(conn, None, data)
    if facture["devis_id"]:
        conn.execute("UPDATE documents SET devis_id=? WHERE id=?", (facture["devis_id"], new_id))
    return new_id


def statut_devis(conn, doc_id, status):
    if status not in ("accepte", "refuse", "envoye"):
        raise FolioError("Statut impossible.")
    doc = _doc(conn, doc_id)
    if doc["kind"] != "devis" or not doc["locked"]:
        raise FolioError("Émettez le devis avant de changer son statut.")
    if doc["status"] == "transforme":
        raise FolioError("Ce devis est déjà transformé en facture.")
    try:
        conn.execute(
            "UPDATE documents SET status=? WHERE id=? AND kind='devis' AND locked=1",
            (status, doc_id),
        )
    except sqlite3.Error as err:
        guard(err)
    label = {"accepte": "accepté", "refuse": "refusé", "envoye": "envoyé"}[status]
    audit(conn, "statut", f"Devis {doc['number']} marqué {label}.", doc_id)


def _net_paid(conn, doc_id) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM payments WHERE document_id=?",
        (doc_id,),
    ).fetchone()
    return row["s"]


def encaisser(conn, doc_id, amount, paid_on, method, note=""):
    doc = _doc(conn, doc_id)
    if doc["kind"] != "facture" or not doc["locked"]:
        raise FolioError("On encaisse une facture émise.")
    if method not in MODES:
        raise FolioError("Mode de paiement inconnu.")
    paid_on = parse_date(paid_on, "la date d'encaissement", required=True)
    credited = credit_restant(conn, doc_id)
    # credit_restant = ttc - avoirs, so remaining due = that - payments
    reste = credited - _net_paid(conn, doc_id)
    if amount > 0 and amount > reste:
        raise FolioError(f"Il reste {eur(reste)} sur cette facture.")
    if amount < 0 and -amount > _net_paid(conn, doc_id):
        raise FolioError("Le remboursement dépasse les sommes encaissées.")
    if amount == 0:
        raise FolioError("Le montant est vide.")
    try:
        with Tx(conn):
            conn.execute(
                """
                INSERT INTO payments(document_id, paid_on, amount_cents, method, note, created_at)
                VALUES (?,?,?,?,?,?)
                """,
                (doc_id, paid_on, amount, method, note, now()),
            )
            verb = "Encaissement" if amount > 0 else "Remboursement"
            audit(
                conn,
                "paiement",
                f"{verb} de {eur(amount)} sur {doc['number']} ({MODES[method]}).",
                doc_id,
            )
    except sqlite3.Error as err:
        guard(err)


def annuler_encaissement(conn, payment_id, motif):
    motif = clip(motif, 180)
    if len(motif) < 3:
        raise FolioError("Indiquez le motif de l'annulation.")
    payment = one(conn, "SELECT * FROM payments WHERE id=?", (payment_id,))
    if not payment:
        raise FolioError("Paiement introuvable.")
    if payment["cancels_id"]:
        raise FolioError("Cette ligne est déjà une annulation.")
    if conn.execute("SELECT 1 FROM payments WHERE cancels_id=?", (payment_id,)).fetchone():
        raise FolioError("Cette ligne est déjà annulée.")
    try:
        with Tx(conn):
            conn.execute(
                """
                INSERT INTO payments(document_id, paid_on, amount_cents, method, note, cancels_id, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    payment["document_id"],
                    today(),
                    -payment["amount_cents"],
                    payment["method"],
                    motif,
                    payment_id,
                    now(),
                ),
            )
            doc = _doc(conn, payment["document_id"])
            audit(
                conn,
                "annulation_paiement",
                f"Annulation d'un encaissement de {eur(payment['amount_cents'])} sur {doc['number']} : {motif}",
                doc["id"],
            )
    except sqlite3.Error as err:
        guard(err)


def rembourser_tout(conn, facture_id):
    net = _net_paid(conn, facture_id)
    if net <= 0:
        return
    encaisser(conn, facture_id, -net, today(), "virement", "Remboursement")


def balances(conn):
    paid = {
        row["document_id"]: row["s"]
        for row in conn.execute(
            "SELECT document_id, COALESCE(SUM(amount_cents),0) AS s FROM payments GROUP BY document_id"
        )
    }
    credited = {
        row["facture_id"]: row["s"]
        for row in conn.execute(
            """
            SELECT facture_id, COALESCE(SUM(total_ttc_cents),0) AS s
            FROM documents WHERE kind='avoir' AND locked=1 AND facture_id IS NOT NULL
            GROUP BY facture_id
            """
        )
    }
    return paid, credited


def etat_de(doc, paid, credited, day):
    if not doc["locked"]:
        return "Brouillon", "gray"
    if doc["kind"] == "devis":
        status = doc["status"]
        if status == "envoye" and doc["valid_until"] and doc["valid_until"] < day:
            return "Expiré", "amber"
        return {
            "envoye": ("Envoyé", "blue"),
            "accepte": ("Accepté", "green"),
            "refuse": ("Refusé", "gray"),
            "transforme": ("Transformé", "green"),
        }.get(status, ("Envoyé", "blue"))
    if doc["kind"] == "avoir":
        return "Émis", "blue"
    reste = doc["total_ttc_cents"] - paid - credited
    if doc["total_ttc_cents"] > 0 and credited >= doc["total_ttc_cents"]:
        return "Annulée", "gray"
    if reste <= 0:
        return "Payée", "green"
    if doc["due_date"] and doc["due_date"] < day:
        return "En retard", "red"
    if paid or credited:
        return "Partielle", "amber"
    return "Émise", "blue"


def annoter(conn, docs, day=None):
    day = day or today()
    paid, credited = balances(conn)
    out = []
    for raw in docs:
        doc = dict(raw)
        p = paid.get(doc["id"], 0)
        c = credited.get(doc["id"], 0) if doc["kind"] == "facture" else 0
        doc["paye"] = p
        doc["avoirs_cents"] = c
        doc["reste"] = doc["total_ttc_cents"] - p - c if doc["kind"] == "facture" and doc["locked"] else 0
        doc["etat"], doc["badge"] = etat_de(doc, p, c, day)
        out.append(doc)
    return out


def lister(conn, kind, filtre="", q=""):
    rows = conn.execute(
        """
        SELECT d.id, d.kind, d.status, d.number, d.issue_date, d.due_date, d.valid_until,
               d.objet, d.total_ttc_cents, d.total_ht_cents, d.locked, d.created_at,
               c.nom AS client
        FROM documents d JOIN clients c ON c.id = d.client_id
        WHERE d.kind=?
        ORDER BY COALESCE(NULLIF(d.issue_date, ''), d.created_at) DESC, d.id DESC
        """,
        (kind,),
    ).fetchall()
    items = annoter(conn, rows)
    q = (q or "").strip().lower()
    if q:
        items = [
            item for item in items
            if q in f"{item.get('number') or ''} {item['client']} {item['objet']}".lower()
        ]
    if filtre == "brouillon":
        items = [i for i in items if not i["locked"]]
    elif filtre == "attente":
        items = [i for i in items if i["reste"] > 0]
    elif filtre == "retard":
        items = [i for i in items if i["badge"] == "red"]
    elif filtre == "payee":
        items = [i for i in items if i["etat"] == "Payée"]
    elif filtre in ("envoye", "accepte", "refuse"):
        items = [i for i in items if i["locked"] and i["status"] == filtre]
    return items


def recettes(conn, year: int):
    c = company(conn)
    rate = rates_for(c)
    rows = conn.execute(
        """
        SELECT p.paid_on, p.amount_cents, p.method, p.note, d.number, d.objet,
               d.total_ht_cents AS ht, d.total_ttc_cents AS ttc, c.nom AS client
        FROM payments p
        JOIN documents d ON d.id = p.document_id
        JOIN clients c ON c.id = d.client_id
        WHERE d.kind='facture' AND substr(p.paid_on,1,4)=?
        ORDER BY p.paid_on, p.id
        """,
        (str(year),),
    ).fetchall()
    book = []
    encaisse = 0
    buckets = {}
    for row in rows:
        part = prorata(row["amount_cents"], row["ht"], row["ttc"])
        encaisse += part
        key = int(row["paid_on"][5:7])
        if c["periodicite"] != "mois":
            key = (key - 1) // 3 + 1
        buckets[key] = buckets.get(key, 0) + part
        book.append({**dict(row), "ht_cents": part, "mode": MODES.get(row["method"], row["method"])})
    periods = []
    if c["periodicite"] == "mois":
        labels = [m[:3] for m in ("janvier février mars avril mai juin juillet août septembre octobre novembre décembre").split()]
        current = date.today().month if date.today().year == year else 0
        keys = range(1, 13)
    else:
        labels = ["1er trimestre", "2e trimestre", "3e trimestre", "4e trimestre"]
        current = (date.today().month - 1) // 3 + 1 if date.today().year == year else 0
        keys = range(1, 5)
    for key in keys:
        amount = buckets.get(key, 0)
        periods.append({
            "label": labels[key - 1],
            "encaisse": amount,
            "cotisations": (amount * rate["total"] + 5_000) // 10_000,
            "current": key == current,
        })
    facture = conn.execute(
        """
        SELECT kind, total_ht_cents FROM documents
        WHERE locked=1 AND kind IN ('facture','avoir') AND substr(issue_date,1,4)=?
        """,
        (str(year),),
    )
    facture_ht = 0
    for row in facture:
        facture_ht += row["total_ht_cents"] if row["kind"] == "facture" else -row["total_ht_cents"]
    return {
        "book": book,
        "encaisse": encaisse,
        "facture_ht": facture_ht,
        "periods": periods,
        "rate": rate,
        "cotisations": (encaisse * rate["total"] + 5_000) // 10_000,
        "social": (encaisse * rate["social"] + 5_000) // 10_000,
        "impot": (encaisse * rate["vl"] + 5_000) // 10_000,
    }


def ouverts(conn, day=None):
    day = day or today()
    rows = conn.execute(
        """
        SELECT d.id, d.number, d.due_date, d.total_ttc_cents, d.objet, c.nom AS client
        FROM documents d JOIN clients c ON c.id=d.client_id
        WHERE d.kind='facture' AND d.locked=1
        """
    ).fetchall()
    paid, credited = balances(conn)
    late = []
    waiting = 0
    late_cents = 0
    for row in rows:
        reste = row["total_ttc_cents"] - paid.get(row["id"], 0) - credited.get(row["id"], 0)
        if reste <= 0:
            continue
        waiting += reste
        if row["due_date"] and row["due_date"] < day:
            late_cents += reste
            late.append({**dict(row), "reste": reste})
    late.sort(key=lambda item: item["due_date"])
    return {"waiting": waiting, "late_cents": late_cents, "late": late, "late_n": len(late)}


def situation(conn, day=None):
    day = day or today()
    c = company(conn)
    if not ready(c):
        return {
            "ready": False,
            "retard": 0,
            "coach": {
                "text": "Commencez par votre nom et votre SIRET. Sans eux, une facture n'est pas valable. Le bouton Conseils masque ces aides quand vous n'en avez plus besoin.",
                "cta": "",
                "href": "",
                "quiet": False,
            },
        }
    n_clients = conn.execute("SELECT COUNT(*) AS n FROM clients").fetchone()["n"]
    draft = conn.execute(
        "SELECT id, kind FROM documents WHERE locked=0 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    issued = conn.execute("SELECT COUNT(*) AS n FROM documents WHERE locked=1").fetchone()["n"]
    open_ = ouverts(conn, day)
    year = int(day[:4])
    if n_clients == 0:
        coach = {
            "text": "Ajoutez votre premier client. Pour une entreprise française, le SIRET est nécessaire : le SIREN en est déduit.",
            "cta": "Nouveau client",
            "href": "/clients/nouveau",
        }
    elif open_["late"]:
        first = open_["late"][0]
        coach = {
            "text": f"{first['client']} a {eur(first['reste'])} en retard sur {first['number'] or 'une facture'}. Ouvrez-la pour relancer, ou notez le paiement s'il est arrivé.",
            "cta": "Ouvrir la facture",
            "href": f"/d/{first['id']}",
        }
    elif draft:
        kind = {"facture": "facture", "devis": "devis", "avoir": "avoir"}[draft["kind"]]
        coach = {
            "text": f"Un {kind} est encore en brouillon. Vous pouvez le modifier ou le supprimer. Le numéro n'est pris qu'au moment où vous l'émettez.",
            "cta": "Reprendre le brouillon",
            "href": f"/d/{draft['id']}/modifier",
        }
    elif issued == 0:
        coach = {
            "text": "Créez un devis si le client doit d'abord accepter, ou une facture si la prestation est déjà faite.",
            "cta": "Nouvelle facture",
            "href": "/nouveau/facture",
        }
    elif not c["iban"]:
        coach = {
            "text": "Ajoutez votre IBAN. Il sera imprimé sur les factures, et le client saura où virer.",
            "cta": "Ajouter l'IBAN",
            "href": "/reglages",
        }
    else:
        stats = recettes(conn, year)
        current = next((p for p in stats["periods"] if p["current"]), None)
        if current and current["encaisse"] > 0:
            coach = {
                "text": f"{current['label'].capitalize()} : {eur(current['encaisse'])} encaissés, environ {eur(current['cotisations'])} de cotisations. C'est le montant encaissé que vous reportez sur l'Urssaf, pas le montant facturé.",
                "cta": "Voir les recettes",
                "href": "/recettes",
                "quiet": True,
            }
        else:
            coach = {
                "text": "Tout est à jour. Les factures émises restent verrouillées sur cet ordinateur.",
                "cta": "",
                "href": "",
                "quiet": True,
            }
    coach.setdefault("quiet", False)
    return {"ready": True, "retard": open_["late_n"], "coach": coach, "open": open_}


def verifier(conn):
    problemes = []
    prev = ZERO
    for row in conn.execute("SELECT * FROM audit ORDER BY id"):
        raw = f"{row['prev_hash']}|{row['at']}|{row['action']}|{row['document_id'] or ''}|{row['detail']}"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        if row["prev_hash"] != prev or digest != row["hash"]:
            problemes.append(f"Le journal s'interrompt à la ligne {row['id']}.")
            break
        prev = row["hash"]
    for kinds, label in ((("facture", "avoir"), "factures"), (("devis",), "devis")):
        marks = ",".join("?" * len(kinds))
        prev = ZERO
        rows = conn.execute(
            f"""
            SELECT number, content_hash, prev_hash, pdf FROM documents
            WHERE locked=1 AND kind IN ({marks})
            ORDER BY issued_at, id
            """,
            kinds,
        )
        for doc in rows:
            shown = doc["number"] or label
            if doc["prev_hash"] != prev:
                problemes.append(f"{shown} n'est plus chaîné au document précédent.")
            if hashlib.sha256(doc["pdf"]).hexdigest() != doc["content_hash"]:
                problemes.append(f"Le fichier {shown} ne correspond plus à son empreinte.")
            prev = doc["content_hash"]
    return problemes


def journal(conn, limit=200):
    rows = []
    for row in conn.execute(
        "SELECT id, at, action, document_id, detail FROM audit ORDER BY id DESC LIMIT ?",
        (limit,),
    ):
        try:
            message = json.loads(row["detail"]).get("message", "")
        except json.JSONDecodeError:
            message = row["detail"]
        rows.append({"id": row["id"], "at": row["at"], "message": message, "document_id": row["document_id"]})
    return rows


def save_company(conn, data):
    allowed = {
        "prenom", "nom", "nom_commercial", "adresse", "complement", "code_postal", "ville",
        "email", "telephone", "site", "siret", "code_ape", "regime_tva", "taux_tva_bp",
        "option_debits", "iban", "bic", "titulaire", "delai_jours", "validite_devis",
        "penalites", "escompte", "assurance", "mediateur", "activite", "acre",
        "versement_liberatoire", "periodicite", "mention_libre",
    }
    data = {k: v for k, v in data.items() if k in allowed}
    sets = ", ".join(f"{key}=?" for key in data)
    conn.execute(f"UPDATE company SET {sets} WHERE id=1", tuple(data.values()))


def save_client(conn, client_id, data):
    if client_id:
        if not one(conn, "SELECT id FROM clients WHERE id=?", (client_id,)):
            raise FolioError("Client introuvable.")
        sets = ", ".join(f"{key}=?" for key in data)
        try:
            conn.execute(f"UPDATE clients SET {sets} WHERE id=?", (*data.values(), client_id))
        except sqlite3.Error as err:
            guard(err)
        return client_id
    keys = ", ".join(data)
    marks = ", ".join("?" * len(data))
    cur = conn.execute(
        f"INSERT INTO clients({keys}, created_at) VALUES ({marks}, ?)",
        (*data.values(), now()),
    )
    return cur.lastrowid


def delete_client(conn, client_id):
    try:
        cur = conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
    except sqlite3.IntegrityError as err:
        raise FolioError("Ce client a des documents. Il reste dans la liste.") from err
    if cur.rowcount == 0:
        raise FolioError("Client introuvable.")


def messages(snap):
    seller = snap["seller"]
    client = snap["client"]
    kind = {"facture": "facture", "devis": "devis", "avoir": "avoir"}[snap["kind"]]
    number = snap["number"]
    total = eur(snap["totals"]["ttc"])
    hello = "Bonjour,"
    sign = (
        f"\nBien cordialement,\n{seller['nom_complet']}\nEntrepreneur individuel\n{seller['email']}"
    )
    due = date_fr(snap["due_date"])
    iban = pretty_iban(seller["iban"])
    pay = ""
    if seller["iban"] and snap["kind"] == "facture":
        pay = f"\nLe règlement peut se faire par virement :\nIBAN {iban}\n"
        if seller["bic"]:
            pay += f"BIC {seller['bic']}\n"
    envoi = (
        f"{hello}\n\nVeuillez trouver votre {kind} {number} d'un montant de {total}."
        + (f"\nÉchéance : {due}." if due else "")
        + pay
        + sign
    )
    relance = (
        f"{hello}\n\nSauf erreur de ma part, la facture {number} "
        f"du {date_fr(snap['issue_date'])} reste due"
        + (f" depuis le {due}" if due else "")
        + f".\nLe solde est de {total}.\n"
        "Je vous remercie de procéder au règlement, ou de me dire si le paiement est déjà parti."
        + pay
        + sign
    )
    ferme = (
        f"{hello}\n\nLa facture {number} reste impayée. Le solde dû est de {total}"
        + (f", échéance du {due}" if due else "")
        + ".\nLes pénalités prévues sur la facture sont exigibles"
    )
    if client["type"] == "professionnel":
        ferme += ", ainsi que l'indemnité forfaitaire de 40 € pour frais de recouvrement"
    ferme += ".\nJe vous demande de régulariser cette somme sous huit jours." + pay + sign
    return {
        "envoi_sujet": f"{kind.capitalize()} {number}",
        "envoi": envoi,
        "relance_sujet": f"Facture {number} — rappel",
        "relance": relance,
        "ferme_sujet": f"Facture {number} — solde impayé",
        "ferme": ferme,
    }


def snapshot_live(conn, doc_id):
    doc = _doc(conn, doc_id)
    if doc["locked"] and doc["snapshot"]:
        return json.loads(doc["snapshot"]), doc
    c = company(conn, with_logo=True)
    client = _client(conn, doc["client_id"])
    lines = lines_of(conn, doc_id)
    snap = make_snapshot(c, client, doc, lines, doc["number"] or "", "")
    if doc["kind"] == "avoir" and doc["facture_id"]:
        origin = one(conn, "SELECT number, issue_date FROM documents WHERE id=?", (doc["facture_id"],))
        if origin:
            snap["facture_number"] = origin["number"] or ""
            snap["facture_date"] = origin["issue_date"]
    return snap, doc
