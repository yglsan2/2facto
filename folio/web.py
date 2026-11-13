"""Interface locale. Rien ne quitte l'ordinateur."""

from __future__ import annotations

import csv
import io
import os
import secrets
import sqlite3
from datetime import date
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from markupsafe import Markup, escape

from folio import core, invoice
from folio.db import connect

ROOT = Path(__file__).resolve().parent.parent


def tip(text: str) -> Markup:
    return Markup(
        '<span class="tip"><button type="button" class="tip-btn" aria-label="Conseil" aria-expanded="false">?</button>'
        f'<span class="tip-pop" role="note">{escape(text)}</span></span>'
    )


def create_app(data_dir=None):
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    folder = Path(data_dir or os.environ.get("FOLIO_DATA") or Path.home() / ".2facto")
    folder.mkdir(parents=True, exist_ok=True)
    secret = folder / "secret"
    if not secret.exists():
        secret.write_text(secrets.token_hex(32), encoding="utf-8")
    app.secret_key = secret.read_text(encoding="utf-8").strip()
    app.config["DATA"] = folder
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

    def db():
        if "db" not in g:
            g.db = connect(folder / "2facto.db")
        return g.db

    @app.teardown_appcontext
    def close(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.before_request
    def csrf():
        session.setdefault("csrf", secrets.token_hex(16))
        if request.method == "POST" and request.form.get("csrf") != session.get("csrf"):
            abort(400, "La page a expiré. Rechargez-la, puis recommencez.")

    @app.context_processor
    def inject():
        if request.endpoint == "static":
            return {}
        state = core.situation(db())
        return {
            "csrf": session.get("csrf", ""),
            "ready": state["ready"],
            "coach": state["coach"],
            "retard": state["retard"],
            "tip": tip,
            "eur": core.eur,
            "date_fr": core.date_fr,
            "year": date.today().year,
        }

    def go(endpoint, **args):
        return redirect(url_for(endpoint, **args), 303)

    def need_ready():
        if not core.ready(core.company(db())):
            flash("Indiquez d'abord votre nom et votre SIRET.", "err")
            return go("home")
        return None

    @app.get("/")
    def home():
        conn = db()
        c = core.company(conn)
        if not core.ready(c):
            return render_template("home.html", company=c, setup=True)
        year = date.today().year
        stats = core.recettes(conn, year)
        open_ = core.ouverts(conn)
        rate = stats["rate"]
        recent = core.lister(conn, "facture")[:6]
        return render_template(
            "home.html",
            company=c,
            setup=False,
            stats=stats,
            open_=open_,
            rate=rate,
            recent=recent,
            rate_social=core.rate_label(rate["social"]),
            rate_vl=core.rate_label(rate["vl"]),
        )

    @app.post("/demarrage")
    def demarrage():
        try:
            data = clean_company(request.form, core.company(db()), minimal=True)
            core.save_company(db(), data)
        except core.FolioError as err:
            flash(str(err), "err")
            return render_template("home.html", company=core.company(db()), setup=True, form=request.form)
        flash("C'est enregistré. Vous pouvez facturer.", "ok")
        return go("home")

    @app.get("/factures")
    def factures():
        blocked = need_ready()
        if blocked:
            return blocked
        return _list("facture")

    @app.get("/devis")
    def devis():
        blocked = need_ready()
        if blocked:
            return blocked
        return _list("devis")

    @app.get("/avoirs")
    def avoirs():
        blocked = need_ready()
        if blocked:
            return blocked
        return _list("avoir")

    def _list(kind):
        items = core.lister(db(), kind, request.args.get("filtre", ""), request.args.get("q", ""))
        return render_template("list.html", kind=kind, items=items, filtre=request.args.get("filtre", ""), q=request.args.get("q", ""))

    @app.get("/nouveau/<kind>")
    def nouveau(kind):
        blocked = need_ready()
        if blocked:
            return blocked
        if kind not in ("facture", "devis"):
            abort(404)
        return _editor(None, kind)

    @app.get("/d/<int:doc_id>")
    def voir(doc_id):
        blocked = need_ready()
        if blocked:
            return blocked
        conn = db()
        doc = core.one(conn, "SELECT * FROM documents WHERE id=?", (doc_id,))
        if not doc:
            abort(404)
        if not doc["locked"]:
            return go("modifier", doc_id=doc_id)
        rows = core.annoter(conn, [doc])
        doc = rows[0]
        client = core.one(conn, "SELECT * FROM clients WHERE id=?", (doc["client_id"],))
        payments = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM payments WHERE document_id=? ORDER BY paid_on, id", (doc_id,)
            )
        ]
        cancelled = {p["cancels_id"] for p in payments if p["cancels_id"]}
        avoirs = [
            dict(r)
            for r in conn.execute(
                "SELECT id, number, total_ttc_cents, locked FROM documents WHERE kind='avoir' AND facture_id=? ORDER BY id",
                (doc_id,),
            )
        ]
        snap = None
        lettres = None
        if doc["snapshot"]:
            import json
            snap = json.loads(doc["snapshot"])
            lettres = core.messages(snap)
        return render_template(
            "show.html",
            doc=doc,
            client=client,
            payments=payments,
            cancelled=cancelled,
            avoirs=avoirs,
            snap=snap,
            lettres=lettres,
            modes=core.MODES,
            today=core.today(),
        )

    @app.get("/d/<int:doc_id>/modifier")
    def modifier(doc_id):
        blocked = need_ready()
        if blocked:
            return blocked
        doc = core.one(db(), "SELECT * FROM documents WHERE id=?", (doc_id,))
        if not doc:
            abort(404)
        if doc["locked"]:
            flash("Ce document est émis. Il ne se modifie plus.", "err")
            return go("voir", doc_id=doc_id)
        return _editor(doc, doc["kind"])

    def _editor(doc, kind, form=None):
        conn = db()
        clients = [dict(r) for r in conn.execute("SELECT * FROM clients ORDER BY nom")]
        catalog = [
            dict(r)
            for r in conn.execute("SELECT * FROM catalog WHERE actif=1 ORDER BY libelle")
        ]
        lines = core.lines_of(conn, doc["id"]) if doc else []
        year = date.today().year
        if doc and doc["issue_date"]:
            year = int(doc["issue_date"][:4])
        c = core.company(conn)
        numbers = {str(y): core.prochain_numero(conn, kind if kind != "avoir" else "facture", y) for y in (year, date.today().year)}
        origin = None
        room = None
        if doc and doc["kind"] == "avoir" and doc["facture_id"]:
            origin = core.one(conn, "SELECT id, number, total_ttc_cents FROM documents WHERE id=?", (doc["facture_id"],))
            room = core.credit_restant(conn, doc["facture_id"], sauf=doc["id"])
        preview = {
            "seller": f"{c['prenom']} {c['nom']}".strip(),
            "commercial": c["nom_commercial"],
            "siret": core.group_siret(c["siret"]),
            "siren": core.group_siret(core.siren_of(c["siret"])),
            "adresse": [c["adresse"], f"{c['code_postal']} {c['ville']}".strip()],
            "mention": core.MENTION_TVA,
            "tva": c["regime_tva"] == "assujetti",
            "taux": core.TAUX_TVA.get(c["taux_tva_bp"], "20 %"),
            "taux_bp": c["taux_tva_bp"],
            "escompte": c["escompte"],
            "penalites": c["penalites"],
            "mediateur": c["mediateur"],
            "iban": core.pretty_iban(c["iban"]) if c["iban"] else "",
            "delai": c["delai_jours"],
            "validite": c["validite_devis"],
            "numbers": numbers,
            "natures": core.NATURES,
            "unites": {k: v[0] for k, v in core.UNITES.items()},
            "clients": {
                str(item["id"]): {
                    "nom": item["nom"],
                    "type": item["type"],
                    "contact": item["contact"],
                    "adresse": item["adresse"],
                    "cp": item["code_postal"],
                    "ville": item["ville"],
                    "siren": core.group_siret(core.siren_of(item["siret"])) if item["siret"] else "",
                    "masquer": bool(item["masquer_adresse"]),
                }
                for item in clients
            },
        }
        return render_template(
            "editor.html",
            doc=doc,
            kind=kind,
            form=form,
            lines=lines,
            clients=clients,
            catalog=catalog,
            company=c,
            numbers=numbers,
            origin=origin,
            room=room,
            unites=core.UNITES,
            natures=core.NATURES,
            preview=preview,
        )

    @app.post("/d/sauver")
    def sauver():
        blocked = need_ready()
        if blocked:
            return blocked
        kind = request.form.get("kind", "facture")
        doc_id = request.form.get("id", type=int)
        try:
            data = clean_document(request.form, kind)
            if kind == "avoir":
                data["facture_id"] = request.form.get("facture_id", type=int)
            doc_id = core.enregistrer(db(), doc_id, data)
            if request.form.get("action") == "emettre":
                core.emettre(db(), doc_id)
                flash("C'est émis. Le numéro est définitif.", "ok")
                return go("voir", doc_id=doc_id)
        except core.FolioError as err:
            flash(str(err), "err")
            doc = core.one(db(), "SELECT * FROM documents WHERE id=?", (doc_id,)) if doc_id else None
            return _editor(doc, kind, request.form)
        flash("Brouillon enregistré.", "ok")
        return go("modifier", doc_id=doc_id)

    @app.get("/d/<int:doc_id>/pdf")
    def pdf(doc_id):
        conn = db()
        doc = core.one(conn, "SELECT id, locked, number, kind, pdf FROM documents WHERE id=?", (doc_id,))
        if not doc:
            abort(404)
        download = request.args.get("dl") == "1"
        if doc["locked"]:
            payload = conn.execute("SELECT pdf FROM documents WHERE id=?", (doc_id,)).fetchone()["pdf"]
            name = f"{doc['number']}.pdf"
        else:
            snap, _ = core.snapshot_live(conn, doc_id)
            c = core.company(conn, with_logo=True)
            payload = invoice.render_pdf(snap, c.get("logo"), watermark=True)
            name = "brouillon.pdf"
        return send_file(io.BytesIO(payload), mimetype="application/pdf", as_attachment=download, download_name=name)

    @app.get("/d/<int:doc_id>/xml")
    def xml(doc_id):
        row = db().execute("SELECT number, xml, locked, kind FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row or not row["locked"] or not row["xml"]:
            abort(404)
        return send_file(
            io.BytesIO(row["xml"]),
            mimetype="application/xml",
            as_attachment=True,
            download_name=f"{row['number']}.xml",
        )

    @app.post("/d/<int:doc_id>/supprimer")
    def supprimer(doc_id):
        try:
            kind = core.one(db(), "SELECT kind FROM documents WHERE id=?", (doc_id,))
            core.supprimer(db(), doc_id)
            flash("Brouillon supprimé. Aucun numéro n'a été consommé.", "ok")
            return go("devis" if kind and kind["kind"] == "devis" else "factures")
        except core.FolioError as err:
            flash(str(err), "err")
            return go("modifier", doc_id=doc_id)

    @app.post("/d/<int:doc_id>/dupliquer")
    def dupliquer(doc_id):
        try:
            new_id = core.dupliquer(db(), doc_id)
        except core.FolioError as err:
            flash(str(err), "err")
            return go("voir", doc_id=doc_id)
        flash("Copie créée en brouillon.", "ok")
        return go("modifier", doc_id=new_id)

    @app.post("/d/<int:doc_id>/payer")
    def payer(doc_id):
        try:
            if request.form.get("montant_cents"):
                amount = int(request.form["montant_cents"])
            else:
                amount = core.parse_cents(request.form.get("montant"), "le montant")
            core.encaisser(
                db(),
                doc_id,
                amount,
                request.form.get("date") or core.today(),
                request.form.get("mode") or "virement",
                core.clip(request.form.get("note"), 180),
            )
        except (core.FolioError, ValueError) as err:
            flash(str(err), "err")
            return go("voir", doc_id=doc_id)
        flash("Paiement noté dans le livre des recettes.", "ok")
        return go("voir", doc_id=doc_id)

    @app.post("/d/<int:doc_id>/annuler-paiement")
    def annuler_paiement(doc_id):
        try:
            core.annuler_encaissement(db(), request.form.get("payment_id", type=int), request.form.get("motif", ""))
        except core.FolioError as err:
            flash(str(err), "err")
        return go("voir", doc_id=doc_id)

    @app.post("/d/<int:doc_id>/corriger")
    def corriger(doc_id):
        try:
            new_id = core.annuler_et_refaire(db(), doc_id, request.form.get("rembourser") == "1")
        except core.FolioError as err:
            flash(str(err), "err")
            return go("voir", doc_id=doc_id)
        flash("L'avoir est émis. Un nouveau brouillon est prêt à corriger.", "ok")
        return go("modifier", doc_id=new_id)

    @app.post("/d/<int:doc_id>/avoir")
    def avoir(doc_id):
        try:
            new_id = core.avoir_brouillon(db(), doc_id)
        except core.FolioError as err:
            flash(str(err), "err")
            return go("voir", doc_id=doc_id)
        return go("modifier", doc_id=new_id)

    @app.post("/d/<int:doc_id>/transformer")
    def transformer(doc_id):
        try:
            new_id = core.facture_depuis_devis(db(), doc_id)
        except core.FolioError as err:
            flash(str(err), "err")
            return go("voir", doc_id=doc_id)
        flash("La facture est en brouillon. Émettez-la quand elle est juste.", "ok")
        return go("modifier", doc_id=new_id)

    @app.post("/d/<int:doc_id>/statut")
    def statut(doc_id):
        try:
            core.statut_devis(db(), doc_id, request.form.get("statut", ""))
        except core.FolioError as err:
            flash(str(err), "err")
        return go("voir", doc_id=doc_id)

    @app.get("/clients")
    def clients():
        blocked = need_ready()
        if blocked:
            return blocked
        q = request.args.get("q", "").strip()
        sql = "SELECT * FROM clients"
        args = ()
        if q:
            sql += " WHERE nom LIKE ? OR email LIKE ?"
            args = (f"%{q}%", f"%{q}%")
        sql += " ORDER BY nom"
        rows = [dict(r) for r in db().execute(sql, args)]
        return render_template("clients.html", clients=rows, q=q)

    @app.route("/clients/nouveau", methods=["GET", "POST"])
    @app.route("/clients/<int:client_id>", methods=["GET", "POST"])
    def client_edit(client_id=None):
        blocked = need_ready()
        if blocked:
            return blocked
        row = core.one(db(), "SELECT * FROM clients WHERE id=?", (client_id,)) if client_id else None
        if client_id and not row:
            abort(404)
        if request.method == "POST":
            try:
                data = clean_client(request.form)
                core.save_client(db(), client_id, data)
            except core.FolioError as err:
                flash(str(err), "err")
                return render_template("client.html", client=row, form=request.form)
            flash("Client enregistré.", "ok")
            return go("clients")
        return render_template("client.html", client=row, form=None)

    @app.post("/clients/<int:client_id>/supprimer")
    def client_delete(client_id):
        try:
            core.delete_client(db(), client_id)
            flash("Client retiré.", "ok")
        except core.FolioError as err:
            flash(str(err), "err")
        return go("clients")

    @app.get("/recettes")
    def recettes():
        blocked = need_ready()
        if blocked:
            return blocked
        year = request.args.get("annee", type=int) or date.today().year
        stats = core.recettes(db(), year)
        c = core.company(db())
        return render_template("recettes.html", stats=stats, annee=year, company=c, rate_label=core.rate_label)

    @app.get("/recettes.pdf")
    def recettes_pdf():
        blocked = need_ready()
        if blocked:
            return blocked
        year = request.args.get("annee", type=int) or date.today().year
        stats = core.recettes(db(), year)
        c = core.company(db())
        payload = invoice.livre_pdf(c, year, stats["book"], stats["encaisse"])
        return send_file(io.BytesIO(payload), mimetype="application/pdf", as_attachment=True, download_name=f"recettes-{year}.pdf")

    @app.get("/reglages")
    def reglages():
        c = core.company(db())
        catalog = [dict(r) for r in db().execute("SELECT * FROM catalog WHERE actif=1 ORDER BY libelle")]
        return render_template("settings.html", company=c, catalog=catalog, rates=core.RATES, unites=core.UNITES)

    @app.post("/reglages")
    def sauver_reglages():
        try:
            data = clean_company(request.form, core.company(db()), minimal=False)
            core.save_company(db(), data)
        except core.FolioError as err:
            flash(str(err), "err")
            return go("reglages")
        flash("Réglages enregistrés.", "ok")
        return go("reglages")

    @app.post("/reglages/logo")
    def logo():
        if request.form.get("retirer") == "1":
            db().execute("UPDATE company SET logo=NULL, logo_mime='' WHERE id=1")
            flash("Logo retiré.", "ok")
            return go("reglages")
        upload = request.files.get("logo")
        if not upload or not upload.filename:
            flash("Choisissez une image.", "err")
            return go("reglages")
        blob = upload.read()
        if len(blob) > 800_000:
            flash("Le logo doit rester sous 800 Ko.", "err")
            return go("reglages")
        mime = upload.mimetype or ""
        if mime not in ("image/png", "image/jpeg"):
            flash("Utilisez un PNG ou un JPEG.", "err")
            return go("reglages")
        db().execute("UPDATE company SET logo=?, logo_mime=? WHERE id=1", (blob, mime))
        flash("Logo enregistré.", "ok")
        return go("reglages")

    @app.post("/prestations")
    def prestation_add():
        try:
            libelle = core.clip(request.form.get("libelle"), 120)
            if not libelle:
                raise core.FolioError("Donnez un nom à la prestation.")
            prix = core.parse_cents(request.form.get("prix"), "le prix")
            unite = request.form.get("unite") or "forfait"
            if unite not in core.UNITES:
                unite = "forfait"
            db().execute(
                "INSERT INTO catalog(libelle, prix_cents, unite, actif) VALUES (?,?,?,1)",
                (libelle, prix, unite),
            )
        except core.FolioError as err:
            flash(str(err), "err")
        return go("reglages")

    @app.post("/prestations/<int:item_id>/retirer")
    def prestation_off(item_id):
        db().execute("UPDATE catalog SET actif=0 WHERE id=?", (item_id,))
        return go("reglages")

    @app.get("/journal")
    def journal():
        rows = core.journal(db())
        return render_template("journal.html", rows=rows, problemes=None)

    @app.post("/journal/verifier")
    def verifier():
        problemes = core.verifier(db())
        if problemes:
            flash("La vérification a trouvé un écart.", "err")
        else:
            flash("Journal et factures émises sont intacts.", "ok")
        return render_template("journal.html", rows=core.journal(db()), problemes=problemes)

    @app.get("/export/factures.csv")
    def export_factures():
        year = request.args.get("annee", type=int) or date.today().year
        items = [i for i in core.lister(db(), "facture") if (i["issue_date"] or "").startswith(str(year))]
        return _csv(
            f"factures-{year}.csv",
            ["numero", "date", "client", "objet", "ttc", "reste", "etat"],
            [
                [i["number"] or "", i["issue_date"], i["client"], i["objet"], f"{i['total_ttc_cents']/100:.2f}", f"{i['reste']/100:.2f}", i["etat"]]
                for i in items
            ],
        )

    @app.get("/export/recettes.csv")
    def export_recettes():
        year = request.args.get("annee", type=int) or date.today().year
        stats = core.recettes(db(), year)
        return _csv(
            f"recettes-{year}.csv",
            ["date", "client", "nature", "piece", "mode", "montant"],
            [
                [r["paid_on"], r["client"], r["objet"], r["number"] or "", r["mode"], f"{r['amount_cents']/100:.2f}"]
                for r in stats["book"]
            ],
        )

    @app.get("/sauvegarde")
    def sauvegarde():
        conn = db()
        target = app.config["DATA"] / "sauvegarde.db"
        if target.exists():
            target.unlink()
        out = sqlite3.connect(target)
        conn.backup(out)
        out.close()
        return send_file(target, as_attachment=True, download_name="2facto-sauvegarde.db")

    return app


def _csv(name, headers, rows):
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(headers)
    writer.writerows(rows)
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=name,
    )


def clean_company(form, current, minimal=False):
    prenom = core.clip(form.get("prenom"), 80)
    nom = core.clip(form.get("nom"), 80)
    if not prenom or not nom:
        raise core.FolioError("Indiquez votre prénom et votre nom.")
    siret = core.digits(form.get("siret"))
    if not core.siret_ok(siret):
        raise core.FolioError("Le SIRET doit comporter 14 chiffres valides.")
    email = core.clip(form.get("email"), 120)
    if not core.email_ok(email):
        raise core.FolioError("L'adresse e-mail n'est pas valide.")
    adresse = core.clip(form.get("adresse"), 180)
    cp = core.clip(form.get("code_postal"), 10)
    ville = core.clip(form.get("ville"), 80)
    if not adresse or not cp or not ville:
        raise core.FolioError("Indiquez l'adresse, le code postal et la ville.")
    iban = core.clip(form.get("iban") if "iban" in form else current["iban"], 42).upper()
    iban = "".join(iban.split())
    if iban and not core.iban_ok(iban):
        raise core.FolioError("Cet IBAN ne semble pas valide.")
    bic = core.clip(form.get("bic") if "bic" in form else current["bic"], 11).upper().replace(" ", "")
    if bic and not core.bic_ok(bic):
        raise core.FolioError("Ce BIC ne semble pas valide.")
    data = {
        "prenom": prenom,
        "nom": nom,
        "adresse": adresse,
        "code_postal": cp,
        "ville": ville,
        "email": email,
        "siret": siret,
        "iban": iban,
    }
    if minimal:
        regime = form.get("regime_tva", "franchise")
        data["regime_tva"] = regime if regime in ("franchise", "assujetti") else "franchise"
        return data
    regime = form.get("regime_tva", current["regime_tva"])
    if regime not in ("franchise", "assujetti"):
        regime = "franchise"
    activite = form.get("activite", "bic_services")
    if activite not in core.RATES:
        activite = "bic_services"
    acre = form.get("acre", "non")
    if acre not in ("non", "apres", "avant"):
        acre = "non"
    periodicite = form.get("periodicite", "trimestre")
    if periodicite not in ("trimestre", "mois"):
        periodicite = "trimestre"
    try:
        taux = int(form.get("taux_tva_bp") or current["taux_tva_bp"])
    except ValueError:
        taux = 2000
    if taux not in core.TAUX_TVA:
        taux = 2000
    try:
        delai = int(form.get("delai_jours") or 30)
        validite = int(form.get("validite_devis") or 30)
    except ValueError as err:
        raise core.FolioError("Le délai doit être un nombre de jours.") from err
    if not 0 <= delai <= 60 or not 1 <= validite <= 365:
        raise core.FolioError("Le délai de paiement va de 0 à 60 jours.")
    data.update({
        "nom_commercial": core.clip(form.get("nom_commercial"), 120),
        "complement": core.clip(form.get("complement"), 180),
        "telephone": core.clip(form.get("telephone"), 30),
        "site": core.clip(form.get("site"), 180),
        "code_ape": core.clip(form.get("code_ape"), 6).upper(),
        "regime_tva": regime,
        "taux_tva_bp": taux,
        "option_debits": 1 if form.get("option_debits") == "1" and regime == "assujetti" else 0,
        "bic": bic,
        "titulaire": core.clip(form.get("titulaire"), 120),
        "delai_jours": delai,
        "validite_devis": validite,
        "penalites": core.clip(form.get("penalites"), 400) or current["penalites"],
        "escompte": core.clip(form.get("escompte"), 240) or current["escompte"],
        "assurance": core.clip(form.get("assurance"), 240),
        "mediateur": core.clip(form.get("mediateur"), 240),
        "activite": activite,
        "acre": acre,
        "versement_liberatoire": 1 if form.get("versement_liberatoire") == "1" else 0,
        "periodicite": periodicite,
        "mention_libre": core.clip(form.get("mention_libre"), 400),
    })
    return data


def clean_client(form):
    kind = form.get("type", "professionnel")
    if kind not in ("particulier", "professionnel"):
        kind = "professionnel"
    nom = core.clip(form.get("nom"), 160)
    if not nom:
        raise core.FolioError("Indiquez le nom du client.")
    pays = core.clip(form.get("pays") or "FR", 2).upper()
    if not pays.isalpha() or len(pays) != 2:
        raise core.FolioError("Le pays tient sur deux lettres, par exemple FR.")
    siret = core.digits(form.get("siret"))
    if kind == "particulier":
        siret = ""
    elif siret and not core.siret_ok(siret):
        raise core.FolioError("Le SIRET du client n'est pas valide.")
    email = core.clip(form.get("email"), 120)
    if email and not core.email_ok(email):
        raise core.FolioError("L'e-mail du client n'est pas valide.")
    return {
        "type": kind,
        "nom": nom,
        "contact": core.clip(form.get("contact"), 120),
        "adresse": core.clip(form.get("adresse"), 180),
        "complement": core.clip(form.get("complement"), 180),
        "code_postal": core.clip(form.get("code_postal"), 10),
        "ville": core.clip(form.get("ville"), 80),
        "pays": pays,
        "email": email,
        "telephone": core.clip(form.get("telephone"), 30),
        "siret": siret,
        "masquer_adresse": 1 if kind == "particulier" and form.get("masquer_adresse") == "1" else 0,
        "notes": core.clip(form.get("notes"), 800),
    }


def clean_document(form, kind):
    libs = form.getlist("libelle")
    qtys = form.getlist("quantite")
    unites = form.getlist("unite")
    prices = form.getlist("prix")
    remises = form.getlist("remise")
    lines = []
    for i, lib in enumerate(libs):
        libelle = core.clip(lib, 180)
        price = prices[i] if i < len(prices) else ""
        if not libelle and not (price or "").strip():
            continue
        if not libelle:
            raise core.FolioError("Chaque ligne doit avoir un libellé.")
        lines.append({
            "libelle": libelle,
            "qty_bp": core.parse_qty_bp(qtys[i] if i < len(qtys) else "1"),
            "unite": unites[i] if i < len(unites) and unites[i] in core.UNITES else "forfait",
            "prix_cents": core.parse_cents(price, "le prix"),
            "remise_bp": core.parse_percent_bp(remises[i] if i < len(remises) else "0"),
        })
    nature = form.get("nature") or "prestation"
    if nature not in core.NATURES:
        nature = "prestation"
    return {
        "kind": kind,
        "client_id": form.get("client_id", type=int),
        "objet": core.clip(form.get("objet"), 200),
        "issue_date": core.parse_date(form.get("issue_date"), "la date d'émission"),
        "service_date": core.parse_date(form.get("service_date"), "la date de prestation"),
        "service_end": core.parse_date(form.get("service_end"), "la fin de prestation"),
        "due_date": core.parse_date(form.get("due_date"), "l'échéance"),
        "valid_until": core.parse_date(form.get("valid_until"), "la validité"),
        "bon_commande": core.clip(form.get("bon_commande"), 40),
        "notes": core.clip(form.get("notes"), 800),
        "nature": nature,
        "livraison_adresse": core.clip(form.get("livraison_adresse"), 180),
        "livraison_cp": core.clip(form.get("livraison_cp"), 10),
        "livraison_ville": core.clip(form.get("livraison_ville"), 80),
        "remise_bp": core.parse_percent_bp(form.get("remise_globale")),
        "lines": lines,
    }
