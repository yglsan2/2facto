import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS company (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  prenom TEXT NOT NULL DEFAULT '',
  nom TEXT NOT NULL DEFAULT '',
  nom_commercial TEXT NOT NULL DEFAULT '',
  adresse TEXT NOT NULL DEFAULT '',
  complement TEXT NOT NULL DEFAULT '',
  code_postal TEXT NOT NULL DEFAULT '',
  ville TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  telephone TEXT NOT NULL DEFAULT '',
  site TEXT NOT NULL DEFAULT '',
  siret TEXT NOT NULL DEFAULT '',
  code_ape TEXT NOT NULL DEFAULT '',
  regime_tva TEXT NOT NULL DEFAULT 'franchise',
  taux_tva_bp INTEGER NOT NULL DEFAULT 2000,
  option_debits INTEGER NOT NULL DEFAULT 0,
  iban TEXT NOT NULL DEFAULT '',
  bic TEXT NOT NULL DEFAULT '',
  titulaire TEXT NOT NULL DEFAULT '',
  delai_jours INTEGER NOT NULL DEFAULT 30,
  validite_devis INTEGER NOT NULL DEFAULT 30,
  penalites TEXT NOT NULL DEFAULT '',
  escompte TEXT NOT NULL DEFAULT '',
  assurance TEXT NOT NULL DEFAULT '',
  mediateur TEXT NOT NULL DEFAULT '',
  activite TEXT NOT NULL DEFAULT 'bic_services',
  acre TEXT NOT NULL DEFAULT 'non',
  versement_liberatoire INTEGER NOT NULL DEFAULT 0,
  periodicite TEXT NOT NULL DEFAULT 'trimestre',
  mention_libre TEXT NOT NULL DEFAULT '',
  logo BLOB,
  logo_mime TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS clients (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN ('particulier', 'professionnel')),
  nom TEXT NOT NULL,
  contact TEXT NOT NULL DEFAULT '',
  adresse TEXT NOT NULL DEFAULT '',
  complement TEXT NOT NULL DEFAULT '',
  code_postal TEXT NOT NULL DEFAULT '',
  ville TEXT NOT NULL DEFAULT '',
  pays TEXT NOT NULL DEFAULT 'FR',
  email TEXT NOT NULL DEFAULT '',
  telephone TEXT NOT NULL DEFAULT '',
  siret TEXT NOT NULL DEFAULT '',
  masquer_adresse INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog (
  id INTEGER PRIMARY KEY,
  libelle TEXT NOT NULL,
  prix_cents INTEGER NOT NULL,
  unite TEXT NOT NULL,
  actif INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('devis', 'facture', 'avoir')),
  status TEXT NOT NULL CHECK (status IN ('brouillon','envoye','accepte','refuse','transforme','emise')),
  number TEXT,
  client_id INTEGER NOT NULL REFERENCES clients(id),
  devis_id INTEGER,
  facture_id INTEGER,
  issue_date TEXT NOT NULL DEFAULT '',
  service_date TEXT NOT NULL DEFAULT '',
  service_end TEXT NOT NULL DEFAULT '',
  due_date TEXT NOT NULL DEFAULT '',
  valid_until TEXT NOT NULL DEFAULT '',
  bon_commande TEXT NOT NULL DEFAULT '',
  objet TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  nature TEXT NOT NULL DEFAULT 'prestation',
  livraison_adresse TEXT NOT NULL DEFAULT '',
  livraison_cp TEXT NOT NULL DEFAULT '',
  livraison_ville TEXT NOT NULL DEFAULT '',
  remise_bp INTEGER NOT NULL DEFAULT 0,
  total_ht_cents INTEGER NOT NULL DEFAULT 0,
  total_tva_cents INTEGER NOT NULL DEFAULT 0,
  total_ttc_cents INTEGER NOT NULL DEFAULT 0,
  tva_applicable INTEGER NOT NULL DEFAULT 0,
  taux_tva_bp INTEGER NOT NULL DEFAULT 0,
  snapshot TEXT,
  pdf BLOB,
  xml BLOB,
  content_hash TEXT,
  prev_hash TEXT,
  locked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  issued_at TEXT
);

CREATE TABLE IF NOT EXISTS lines (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  libelle TEXT NOT NULL,
  qty_bp INTEGER NOT NULL CHECK (qty_bp > 0),
  unite TEXT NOT NULL,
  prix_cents INTEGER NOT NULL CHECK (prix_cents >= 0),
  remise_bp INTEGER NOT NULL DEFAULT 0 CHECK (remise_bp >= 0 AND remise_bp <= 10000),
  ht_cents INTEGER NOT NULL CHECK (ht_cents >= 0)
);

CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  paid_on TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK (amount_cents != 0),
  method TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  cancels_id INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sequences (
  kind TEXT NOT NULL,
  year INTEGER NOT NULL,
  last INTEGER NOT NULL,
  PRIMARY KEY (kind, year)
);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  action TEXT NOT NULL,
  document_id INTEGER,
  detail TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS documents_no_delete
BEFORE DELETE ON documents
WHEN OLD.locked = 1
BEGIN
  SELECT RAISE(ABORT, 'verrouille');
END;

CREATE TRIGGER IF NOT EXISTS documents_no_update
BEFORE UPDATE ON documents
WHEN OLD.locked = 1 AND (
  OLD.kind != 'devis'
  OR NEW.status NOT IN ('envoye', 'accepte', 'refuse', 'transforme')
  OR NEW.number IS NOT OLD.number
  OR NEW.client_id IS NOT OLD.client_id
  OR NEW.devis_id IS NOT OLD.devis_id
  OR NEW.facture_id IS NOT OLD.facture_id
  OR NEW.issue_date IS NOT OLD.issue_date
  OR NEW.service_date IS NOT OLD.service_date
  OR NEW.service_end IS NOT OLD.service_end
  OR NEW.due_date IS NOT OLD.due_date
  OR NEW.valid_until IS NOT OLD.valid_until
  OR NEW.bon_commande IS NOT OLD.bon_commande
  OR NEW.objet IS NOT OLD.objet
  OR NEW.notes IS NOT OLD.notes
  OR NEW.nature IS NOT OLD.nature
  OR NEW.livraison_adresse IS NOT OLD.livraison_adresse
  OR NEW.livraison_cp IS NOT OLD.livraison_cp
  OR NEW.livraison_ville IS NOT OLD.livraison_ville
  OR NEW.remise_bp IS NOT OLD.remise_bp
  OR NEW.total_ht_cents IS NOT OLD.total_ht_cents
  OR NEW.total_tva_cents IS NOT OLD.total_tva_cents
  OR NEW.total_ttc_cents IS NOT OLD.total_ttc_cents
  OR NEW.tva_applicable IS NOT OLD.tva_applicable
  OR NEW.taux_tva_bp IS NOT OLD.taux_tva_bp
  OR NEW.snapshot IS NOT OLD.snapshot
  OR NEW.pdf IS NOT OLD.pdf
  OR NEW.xml IS NOT OLD.xml
  OR NEW.content_hash IS NOT OLD.content_hash
  OR NEW.prev_hash IS NOT OLD.prev_hash
  OR NEW.locked IS NOT OLD.locked
  OR NEW.kind IS NOT OLD.kind
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.issued_at IS NOT OLD.issued_at
)
BEGIN
  SELECT RAISE(ABORT, 'verrouille');
END;

CREATE TRIGGER IF NOT EXISTS lines_no_write
BEFORE UPDATE ON lines
WHEN (SELECT locked FROM documents WHERE id = OLD.document_id) = 1
BEGIN
  SELECT RAISE(ABORT, 'fige');
END;

CREATE TRIGGER IF NOT EXISTS lines_no_delete
BEFORE DELETE ON lines
WHEN (SELECT locked FROM documents WHERE id = OLD.document_id) = 1
BEGIN
  SELECT RAISE(ABORT, 'fige');
END;

CREATE TRIGGER IF NOT EXISTS lines_no_insert
BEFORE INSERT ON lines
WHEN (SELECT locked FROM documents WHERE id = NEW.document_id) = 1
BEGIN
  SELECT RAISE(ABORT, 'fige');
END;

CREATE TRIGGER IF NOT EXISTS payments_no_update
BEFORE UPDATE ON payments
BEGIN
  SELECT RAISE(ABORT, 'journal');
END;

CREATE TRIGGER IF NOT EXISTS payments_no_delete
BEFORE DELETE ON payments
BEGIN
  SELECT RAISE(ABORT, 'journal');
END;

CREATE TRIGGER IF NOT EXISTS payments_only_facture
BEFORE INSERT ON payments
WHEN (SELECT kind FROM documents WHERE id = NEW.document_id) != 'facture'
  OR (SELECT locked FROM documents WHERE id = NEW.document_id) != 1
BEGIN
  SELECT RAISE(ABORT, 'paiement');
END;

CREATE TRIGGER IF NOT EXISTS sequences_no_delete
BEFORE DELETE ON sequences
BEGIN
  SELECT RAISE(ABORT, 'numerotation');
END;

CREATE TRIGGER IF NOT EXISTS sequences_no_back
BEFORE UPDATE ON sequences
WHEN NEW.last < OLD.last OR NEW.kind != OLD.kind OR NEW.year != OLD.year
BEGIN
  SELECT RAISE(ABORT, 'numerotation');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit
BEGIN
  SELECT RAISE(ABORT, 'journal');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit
BEGIN
  SELECT RAISE(ABORT, 'journal');
END;
"""

DEFAULTS = (
    "Escompte pour paiement anticipé : néant.",
    "En cas de retard de paiement, les pénalités sont exigibles sans rappel, "
    "au taux de trois fois le taux d'intérêt légal en vigueur.",
)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=4000")
    conn.executescript(SCHEMA)
    conn.execute(
        """
        INSERT OR IGNORE INTO company (id, escompte, penalites)
        VALUES (1, ?, ?)
        """,
        DEFAULTS,
    )
    return conn
