"""
SessionsToMistral.py — Envoie les sessions complètes/valides vers Mistral.

Parallélisme :
  - Phase d'analyse (validation d'intégrité de chaque candidat) :
    ProcessPoolExecutor (ANALYZE_PROCESSES, défaut nb CPUs) découpé en chunks,
    chaque processus utilisant un ThreadPoolExecutor (ANALYZE_THREADS_PER_PROCESS,
    défaut 8) pour paralléliser l'I/O (lecture des fichiers de session sur NAS).
  - Phase d'envoi (zip + upload réseau + DB) :
    ThreadPoolExecutor (UPLOAD_WORKERS, défaut 3) — l'upload HTTP et la
    compression libèrent le GIL, donc des threads suffisent.
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timezone
from multiprocessing import cpu_count
from pathlib import Path

import psycopg2
import requests

logging.basicConfig(level=logging.INFO)

BASE_URL = "http://13.62.206.125:5001"
USERNAME = "pd_umi"
PASSWORD = "sqiu763hQP1"

DODGE_FILE = "uploaded_sessions.json"

# Client Mistral dans la BDD — configurable via env
MISTRAL_CLIENT_ID   = os.environ.get("DELIVERY_CLIENT_ID",   "mistral")
MISTRAL_CLIENT_NAME = os.environ.get("DELIVERY_CLIENT_NAME", "Mistral AI")

# Limite de volume par exécution (défaut 5 Go, surchargeable via MAX_RUN_GB)
MAX_RUN_BYTES = int(os.environ.get("MAX_RUN_GB", "5")) * 1024 ** 3

# Files that alone do not constitute a meaningful session
METADATA_ONLY_FILES = {"metadata.json"}

# Parallélisme — analyse (validation/intégrité) : multiprocessing + threads par
# processus pour l'I/O (lecture des fichiers de session sur NAS)
ANALYZE_PROCESSES           = int(os.environ.get("ANALYZE_PROCESSES", str(max(1, cpu_count() or 4))))
ANALYZE_THREADS_PER_PROCESS = int(os.environ.get("ANALYZE_THREADS_PER_PROCESS", "8"))

# Parallélisme — envoi (zip + upload réseau) : threads, l'I/O réseau libère le GIL
UPLOAD_WORKERS = int(os.environ.get("UPLOAD_WORKERS", "3"))


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------

def _pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "192.168.1.18"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "robotics"),
        user=os.environ.get("POSTGRES_USER", "robotics"),
        password=os.environ.get("POSTGRES_PASSWORD", "YsLuB46NKoF6WlS3NwUm97vhEtLkjLRQ"),
        connect_timeout=10,
    )


def _now():
    return datetime.now(timezone.utc)


def db_ensure_client(cur) -> None:
    """Crée le client Mistral s'il n'existe pas encore."""
    cur.execute("""
        INSERT INTO clients (client_id, name)
        VALUES (%s, %s)
        ON CONFLICT (client_id) DO NOTHING
    """, (MISTRAL_CLIENT_ID, MISTRAL_CLIENT_NAME))


def db_resolve_session_id(folder_name: str) -> str | None:
    """
    Retourne le session_id DB correspondant au dossier NAS.

    La colonne session_folder (ex: 'session_20260604_002349') permet de relier
    le nom de dossier NAS à l'ID DB (ex: 'sess_20260604_002351_ab8e4d39').
    Fallback : si la session est directement enregistrée avec le nom de dossier.
    """
    try:
        conn = _pg_connect()
        with conn:
            with conn.cursor() as cur:
                # 1. Lookup par session_folder (cas normal — IDs sess_*)
                cur.execute(
                    "SELECT session_id FROM sessions WHERE session_folder = %s LIMIT 1",
                    (folder_name,),
                )
                row = cur.fetchone()
                if row:
                    return row[0]

                # 2. Lookup direct — session enregistrée avec le nom de dossier comme ID
                cur.execute(
                    "SELECT session_id FROM sessions WHERE session_id = %s LIMIT 1",
                    (folder_name,),
                )
                row = cur.fetchone()
                if row:
                    return row[0]

        conn.close()
    except Exception as exc:
        logging.warning("  DB: impossible de résoudre session_id pour '%s': %s", folder_name, exc)
    return None


def db_start_delivery(session_id: str, size_bytes: int) -> bool:
    """
    Passe la session en 'delivering' et crée/met à jour l'entrée client_deliveries.
    Appelé juste avant l'upload.
    """
    now = _now()
    try:
        conn = _pg_connect()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT project_id, duration_seconds FROM sessions WHERE session_id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
                if not row:
                    logging.warning("  DB: session '%s' introuvable", session_id)
                    return False
                project_id, duration_seconds = row

                db_ensure_client(cur)

                delivery_id = f"del_{session_id}_{MISTRAL_CLIENT_ID}"
                cur.execute("""
                    INSERT INTO client_deliveries
                        (delivery_id, client_id, session_id, project_id,
                         status, started_at, size_bytes, duration_seconds)
                    VALUES (%s, %s, %s, %s, 'delivering', %s, %s, %s)
                    ON CONFLICT (client_id, session_id) DO UPDATE
                        SET status = 'delivering', started_at = %s
                """, (delivery_id, MISTRAL_CLIENT_ID, session_id, project_id,
                      now, size_bytes, duration_seconds, now))

                cur.execute("""
                    UPDATE sessions
                    SET pipeline_status     = 'delivering',
                        delivering_at       = COALESCE(delivering_at, %s),
                        delivery_pending_at = COALESCE(delivery_pending_at, %s),
                        client_id           = %s,
                        size_bytes          = COALESCE(size_bytes, %s)
                    WHERE session_id = %s
                """, (now, now, MISTRAL_CLIENT_ID, size_bytes, session_id))

        conn.close()
        logging.info("  DB: %s → delivering (client: %s)", session_id, MISTRAL_CLIENT_ID)
        return True
    except Exception as exc:
        logging.error("  DB erreur (start_delivery) : %s", exc)
        return False


def db_confirm_delivered(session_id: str, size_bytes: int, duration_seconds: float) -> bool:
    """
    Marque la session et la livraison client comme 'delivered'.
    Appelé après upload réussi.
    """
    now = _now()
    delivery_id = f"del_{session_id}_{MISTRAL_CLIENT_ID}"
    try:
        conn = _pg_connect()
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE client_deliveries
                    SET status = 'delivered', delivered_at = %s
                    WHERE delivery_id = %s
                """, (now, delivery_id))

                cur.execute("""
                    UPDATE sessions
                    SET pipeline_status  = 'delivered',
                        delivered_at     = %s,
                        size_bytes       = COALESCE(size_bytes, %s),
                        duration_seconds = COALESCE(duration_seconds, %s)
                    WHERE session_id = %s
                    RETURNING session_id
                """, (now, size_bytes, duration_seconds or None, session_id))
                updated = cur.fetchone() is not None

        conn.close()
        if updated:
            logging.info("  DB: %s → delivered (client: %s)", session_id, MISTRAL_CLIENT_ID)
        else:
            logging.warning("  DB: session '%s' introuvable lors de la confirmation", session_id)
        return updated
    except Exception as exc:
        logging.error("  DB erreur (confirm_delivered) : %s", exc)
        return False


def db_mark_delivery_failed(session_id: str) -> None:
    """Marque la session et la livraison client comme 'delivery_failed'."""
    now = _now()
    delivery_id = f"del_{session_id}_{MISTRAL_CLIENT_ID}"
    try:
        conn = _pg_connect()
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE client_deliveries
                    SET status = 'failed', error_msg = 'Upload Mistral échoué'
                    WHERE delivery_id = %s
                """, (delivery_id,))
                cur.execute("""
                    UPDATE sessions
                    SET pipeline_status    = 'delivery_failed',
                        delivery_failed_at = %s
                    WHERE session_id = %s
                """, (now, session_id))
        conn.close()
        logging.info("  DB: %s → delivery_failed", session_id)
    except Exception as exc:
        logging.error("  DB erreur (mark_delivery_failed) : %s", exc)


# ---------------------------------------------------------------------------
# Validation d'intégrité — structure complète de la session
# ---------------------------------------------------------------------------

# Taille minimale d'un MP4 valide (encodage non-corrompu)
MP4_MIN_BYTES = 100_000  # 100 KB


def validate_session(session_dir: Path) -> list[str]:
    """
    Vérifie que la session est structurellement complète et sans problème connu.
    Retourne une liste d'issues (vide = session valide).

    Checks :
      1. result.json existe et indique SUCCESS
      2. config.json lisible, caméras sans erreur
      3. mission.json présent
      4. analysis.json : sync_check.ok doit être True
      5. Pour chaque caméra (config) : <name>.mp4 ≥ 100 KB et <name>.jsonl non-vide
      6. cameras/resampled_30hz.jsonl non-vide
      7. Pour chaque capteur (config) : sensors/<name>.jsonl non-vide
    """
    issues: list[str] = []

    # 1. result.json
    result_path = session_dir / "result.json"
    if not result_path.exists():
        issues.append("result.json manquant")
    else:
        try:
            res = json.loads(result_path.read_text(encoding="utf-8"))
            result_val = str(res.get("result", "")).upper()
            if result_val != "SUCCESS":
                issues.append(f"result.json non-SUCCESS (valeur : '{res.get('result')}')")
        except Exception as exc:
            issues.append(f"result.json illisible : {exc}")

    # 2. config.json — lit les caméras et capteurs attendus
    config_path = session_dir / "config.json"
    expected_cameras: list[str] = []
    expected_sensors: list[str] = []
    if not config_path.exists():
        issues.append("config.json manquant")
    else:
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            for cam in cfg.get("cameras", []):
                name = cam.get("name")
                if not name:
                    continue
                expected_cameras.append(name)
                if cam.get("error"):
                    issues.append(f"caméra '{name}' : erreur hardware ({cam['error']})")
            for sen in cfg.get("sensors", []):
                name = sen.get("name")
                if name:
                    expected_sensors.append(name)
        except Exception as exc:
            issues.append(f"config.json illisible : {exc}")

    # 3. mission.json
    if not (session_dir / "mission.json").exists():
        issues.append("mission.json manquant")

    # 4. analysis.json — sync_check
    analysis_path = session_dir / "analysis.json"
    if analysis_path.exists():
        try:
            data = json.loads(analysis_path.read_text(encoding="utf-8"))
            sync = data.get("sync_check", {})
            if isinstance(sync.get("ok"), bool) and not sync["ok"]:
                delta = sync.get("delta_sec", "?")
                issues.append(f"sync_check échoué — delta={delta}s (caméras/capteurs désynchronisés)")
        except Exception:
            pass  # read_analysis_errors lèvera l'erreur si le fichier est corrompu

    # 5. Fichiers caméra
    cam_dir = session_dir / "cameras"
    for name in expected_cameras:
        mp4  = cam_dir / f"{name}.mp4"
        jsonl = cam_dir / f"{name}.jsonl"

        if not mp4.exists():
            issues.append(f"cameras/{name}.mp4 manquant")
        else:
            size = mp4.stat().st_size
            if size < MP4_MIN_BYTES:
                issues.append(
                    f"cameras/{name}.mp4 trop petit ({size} octets < {MP4_MIN_BYTES}) — "
                    "encodage probablement corrompu"
                )

        if not jsonl.exists():
            issues.append(f"cameras/{name}.jsonl manquant")
        elif jsonl.stat().st_size == 0:
            issues.append(f"cameras/{name}.jsonl vide — aucune frame enregistrée")

    # resampled_30hz.jsonl — produit par le post-processing
    resampled = cam_dir / "resampled_30hz.jsonl"
    if expected_cameras:  # seulement si des caméras sont attendues
        if not resampled.exists():
            issues.append("cameras/resampled_30hz.jsonl manquant — post-processing non terminé")
        elif resampled.stat().st_size == 0:
            issues.append("cameras/resampled_30hz.jsonl vide — resample échoué")

    # 6. Fichiers capteurs
    sen_dir = session_dir / "sensors"
    for name in expected_sensors:
        jsonl = sen_dir / f"{name}.jsonl"
        if not jsonl.exists():
            issues.append(f"sensors/{name}.jsonl manquant")
        elif jsonl.stat().st_size == 0:
            issues.append(f"sensors/{name}.jsonl vide — aucune donnée capteur")

    return issues


# ---------------------------------------------------------------------------
# Analysis.json — vérification des erreurs de capture
# ---------------------------------------------------------------------------

def read_analysis_errors(session_dir: Path) -> list[str]:
    """
    Lit analysis.json et retourne la liste des erreurs.
    Retourne [] si le fichier est absent, illisible, ou sans erreurs.
    """
    analysis_path = session_dir / "analysis.json"
    if not analysis_path.exists():
        return []
    try:
        data = json.loads(analysis_path.read_text(encoding="utf-8"))
        errors = data.get("errors", [])
        return [str(e) for e in errors] if errors else []
    except Exception as exc:
        logging.warning("  Impossible de lire analysis.json pour '%s': %s", session_dir.name, exc)
        return []


# ---------------------------------------------------------------------------
# Session analysis — single rglob pass (size + empty check)
# ---------------------------------------------------------------------------

def analyze_session(session_dir: Path) -> tuple[bool, str, int]:
    """
    Single directory traversal.
    Returns (is_empty, reason, total_bytes).
    """
    try:
        all_files = [f for f in session_dir.rglob("*") if f.is_file()]
    except Exception as exc:
        return True, f"impossible de lister les fichiers : {exc}", 0

    if not all_files:
        return True, "dossier vide", 0

    total_bytes = 0
    data_bytes = 0
    data_count = 0
    for f in all_files:
        try:
            size = f.stat().st_size
        except OSError:
            size = 0
        total_bytes += size
        if f.name.lower() not in METADATA_ONLY_FILES:
            data_bytes += size
            data_count += 1

    if data_count == 0:
        return True, "uniquement metadata.json, pas de données", 0
    if data_bytes == 0:
        return True, f"{data_count} fichier(s) de données mais tous vides (0 octet)", 0

    return False, "", total_bytes


# ---------------------------------------------------------------------------
# Analyse parallèle des candidats (multiprocessing + threads)
# ---------------------------------------------------------------------------

def _analyze_one(session_dir_str: str) -> tuple:
    """
    Classifie une session candidate (lecture seule) :
      ("invalid",  name, issues)   — structure incomplète/corrompue
      ("rejected", name, errors)   — erreurs dans analysis.json
      ("empty",    name, reason)   — pas de données
      ("valid",    name, size)     — prête à être envoyée
    """
    s = Path(session_dir_str)

    issues = validate_session(s)
    if issues:
        return (s.name, "invalid", issues)

    errors = read_analysis_errors(s)
    if errors:
        return (s.name, "rejected", errors)

    is_empty, reason, size = analyze_session(s)
    if is_empty:
        return (s.name, "empty", reason)

    return (s.name, "valid", size)


def _analyze_chunk(session_dir_strs: list) -> list:
    """
    Worker process : analyse un lot de sessions via un pool de threads
    (l'I/O — rglob, lecture JSON — domine et libère le GIL).
    """
    if len(session_dir_strs) == 1:
        return [_analyze_one(session_dir_strs[0])]

    workers = min(ANALYZE_THREADS_PER_PROCESS, len(session_dir_strs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_analyze_one, session_dir_strs))


# ---------------------------------------------------------------------------
# Dodge file helpers
# ---------------------------------------------------------------------------

def load_dodge(root: Path) -> dict:
    dodge_path = root / DODGE_FILE
    if dodge_path.exists():
        try:
            return json.loads(dodge_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"sessions": []}


def save_dodge(root: Path, dodge: dict) -> None:
    dodge_path = root / DODGE_FILE
    dodge_path.write_text(json.dumps(dodge, indent=2, ensure_ascii=False), encoding="utf-8")


def mark_uploaded(root: Path, dodge: dict, session_name: str, size_bytes: int, duration_seconds: float) -> None:
    dodge["sessions"].append({
        "name": session_name,
        "size_bytes": size_bytes,
        "duration_seconds": duration_seconds,
    })
    save_dodge(root, dodge)


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------

def read_duration(session_dir: Path) -> float:
    """
    Durée totale d'une session, calculée depuis analysis.json : on prend le
    max des duration_sec de toutes les caméras (fps_check) et de tous les
    capteurs (sensor_check), c'est-à-dire le flux le plus long.
    """
    analysis_path = session_dir / "analysis.json"
    if not analysis_path.exists():
        return 0.0
    try:
        data = json.loads(analysis_path.read_text(encoding="utf-8"))
    except Exception:
        return 0.0

    durations = []

    cams = data.get("fps_check", {}).get("cameras", {})
    for c in (cams or {}).values():
        if isinstance(c, dict):
            durations.append(c.get("duration_sec") or 0)

    sensors = data.get("sensor_check", {}).get("sensors", {})
    for s in (sensors or {}).values():
        if isinstance(s, dict):
            durations.append(s.get("duration_sec") or 0)

    return max(durations, default=0.0)


def format_duration(total_seconds: float) -> str:
    h = int(total_seconds) // 3600
    m = (int(total_seconds) % 3600) // 60
    s = int(total_seconds) % 60
    return f"{h}h {m}m {s}s"


def format_size(total_bytes: int) -> str:
    gb = total_bytes / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.2f} Go"
    mb = total_bytes / (1024 ** 2)
    return f"{mb:.1f} Mo"


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_zip_to_mistral(zip_path: str) -> bool:
    path = Path(zip_path)
    if not path.exists() or path.suffix.lower() != ".zip":
        print(f"  Erreur : '{zip_path}' n'est pas un fichier .zip valide")
        return False

    file_name = path.stem
    session = requests.Session()

    payload = {
        "username": USERNAME,
        "password": PASSWORD,
        "repo_id": file_name,
        "filename": path.name,
    }

    print(f"  Demande d'URL signée pour '{path.name}'...")
    try:
        r = session.post(
            url=f"{BASE_URL}/pd/upload",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
    except requests.RequestException as e:
        print(f"  Erreur de connexion : {e}")
        return False

    print(f"  STATUS: {r.status_code}")

    if r.status_code != 200:
        try:
            err = r.json().get("error", "Unknown error")
        except Exception:
            err = r.text
        print(f"  Erreur serveur : {err}")
        return False

    signed_url = r.json().get("url")
    if not signed_url:
        print("  Pas d'URL d'upload reçue.")
        return False

    print(f"  Upload de '{path.name}' ({format_size(path.stat().st_size)})...")
    try:
        with open(path, "rb") as f:
            response = session.put(
                signed_url,
                data=f,
                headers={"Content-Type": "application/zip"},
                timeout=300,
            )
    except requests.RequestException as e:
        print(f"  Erreur upload : {e}")
        return False

    if response.status_code in (200, 201, 204):
        print("  Upload réussi !")
        return True
    else:
        print(f"  Upload échoué {response.status_code}: {response.text}")
        return False


# ---------------------------------------------------------------------------
# Move after upload
# ---------------------------------------------------------------------------

def move_session_to_sent(session_dir: Path, sent_dir: Path) -> bool:
    """Moves session_dir into sent_dir. Returns True on success."""
    try:
        sent_dir.mkdir(parents=True, exist_ok=True)
        dest = sent_dir / session_dir.name
        if dest.exists():
            # Already present (previous partial move?) — remove and replace
            shutil.rmtree(dest)
        shutil.move(str(session_dir), str(dest))
        print(f"  Déplacé vers {dest}")
        return True
    except Exception as exc:
        print(f"  Avertissement : impossible de déplacer '{session_dir.name}' : {exc}")
        return False


# ---------------------------------------------------------------------------
# Zip
# ---------------------------------------------------------------------------

def zip_session(session_dir: Path, tmp_dir: Path) -> Path:
    zip_base = tmp_dir / session_dir.name
    archive = shutil.make_archive(
        str(zip_base), "zip",
        root_dir=session_dir.parent,
        base_dir=session_dir.name,
    )
    return Path(archive)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Upload sessions to Mistral")
    parser.add_argument("dossier", help="Dossier racine contenant les sessions")
    parser.add_argument("--dry-run", action="store_true",
                        help="Analyse et affiche ce qui serait envoyé, sans rien uploader ni déplacer")
    parser.add_argument("--max-sessions", type=int, default=0,
                        help="Nombre maximum de sessions à envoyer par exécution (0 = illimité)")
    parser.add_argument("--all", action="store_true",
                        help="Envoie toutes les sessions valides, sans plafond de volume "
                             "(ignore MAX_RUN_GB). Combinable avec --max-sessions.")
    args = parser.parse_args()

    dry_run = args.dry_run
    max_sessions = args.max_sessions
    send_all = args.all
    max_run_bytes = float("inf") if send_all else MAX_RUN_BYTES
    root = Path(args.dossier)

    if not root.is_dir():
        print(f"Erreur : '{root}' n'est pas un dossier valide")
        sys.exit(1)

    if dry_run:
        print("*** MODE DRY-RUN — aucun upload, aucun déplacement, aucune écriture DB ***\n")

    sent_dir = root.parent / "session_envoye"

    dodge = load_dodge(root)
    already_done = {e["name"] for e in dodge["sessions"]}

    print(f"Lecture de '{root}'...", flush=True)
    all_sessions = sorted(p for p in root.iterdir() if p.is_dir() and p.name.lower().startswith("session"))

    if not all_sessions:
        print(f"Aucun dossier 'session*' trouvé dans '{root}'", flush=True)
        sys.exit(0)

    # Sessions déjà envoyées : on les saute sans les analyser
    skipped = [s.name for s in all_sessions if s.name in already_done]
    candidates = [s for s in all_sessions if s.name not in already_done]

    print(f"{len(all_sessions)} dossier(s) dont {len(skipped)} déjà envoyé(s). "
          f"Analyse de {len(candidates)} candidat(s)...", flush=True)

    # --- Analyse parallèle de tous les candidats (multiprocessing + threads) ---
    empty_sessions:    list[tuple[str, str]]       = []
    invalid_sessions:  list[tuple[str, list[str]]] = []
    rejected_sessions: list[tuple[str, list[str]]] = []
    to_send:           list[tuple[Path, int]]       = []
    capped_count = 0
    cumul_bytes  = 0

    results_by_name: dict = {}
    candidate_paths = [str(s) for s in candidates]
    if candidate_paths:
        chunk_size = max(1, len(candidate_paths) // (ANALYZE_PROCESSES * 4))
        chunks = [candidate_paths[i:i + chunk_size]
                  for i in range(0, len(candidate_paths), chunk_size)]
        with ProcessPoolExecutor(max_workers=min(ANALYZE_PROCESSES, len(chunks))) as pool:
            for chunk_results in pool.map(_analyze_chunk, chunks, chunksize=1):
                for name, kind, detail in chunk_results:
                    results_by_name[name] = (kind, detail)

    # --- Application du quota (max_sessions / MAX_RUN_GB), dans l'ordre d'origine ---
    for s in candidates:
        kind, detail = results_by_name[s.name]

        if kind == "invalid":
            invalid_sessions.append((s.name, detail))
            continue
        if kind == "rejected":
            rejected_sessions.append((s.name, detail))
            continue
        if kind == "empty":
            empty_sessions.append((s.name, detail))
            continue

        # kind == "valid" → detail = size
        size = detail
        if (max_sessions > 0 and len(to_send) >= max_sessions) or cumul_bytes + size > max_run_bytes:
            capped_count += 1
            continue

        to_send.append((s, size))
        cumul_bytes += size

    print(f"{len(all_sessions)} session(s) trouvée(s) au total :", flush=True)
    print(f"  {len(empty_sessions)} vide(s) — ignorées")
    print(f"  {len(invalid_sessions)} incomplète(s)/corrompue(s) — bloquées")
    print(f"  {len(rejected_sessions)} rejetée(s) — erreurs analysis.json")
    print(f"  {len(skipped)} déjà envoyée(s) — ignorées")
    plafond_label = "illimité (--all)" if send_all else format_size(MAX_RUN_BYTES)
    print(f"  {capped_count} non analysée(s) — plafond {plafond_label} atteint")
    print(f"  {len(to_send)} à envoyer ({format_size(cumul_bytes)})")

    if invalid_sessions:
        print("\nSessions incomplètes / corrompues (bloquées) :")
        for name, issues in invalid_sessions:
            print(f"  INVALIDE  {name}")
            for issue in issues:
                print(f"            - {issue}")

    if rejected_sessions:
        print("\nSessions rejetées (erreurs analysis.json) :")
        for name, errs in rejected_sessions:
            print(f"  REJET  {name}")
            for e in errs:
                print(f"         - {e}")

    if empty_sessions:
        print("\nSessions vides :")
        for name, reason in empty_sessions:
            print(f"  VIDE  {name}  ({reason})")

    if skipped:
        print(f"\nIgnorées (dodge) : {skipped}")
    if capped_count:
        print(f"\n{capped_count} session(s) non analysée(s) — seront traitées aux prochains runs.")
    print(flush=True)

    if not to_send:
        print("Aucune session à envoyer.", flush=True)
    elif dry_run:
        print("\nSessions qui seraient envoyées :")
        for i, (session_dir, size) in enumerate(to_send, 1):
            folder_name = session_dir.name
            db_session_id = db_resolve_session_id(folder_name)
            duration = read_duration(session_dir)
            print(f"  [{i}/{len(to_send)}] {folder_name}  ({format_size(size)}, {format_duration(duration)})")
            if db_session_id:
                print(f"           DB → {db_session_id}")
            else:
                print(f"           DB → introuvable (upload sans mise à jour DB)")
    else:
        dodge_lock  = threading.Lock()
        print_lock  = threading.Lock()

        def _upload_one(item: tuple) -> str:
            i, session_dir, _, tmp_dir = item
            folder_name = session_dir.name

            with print_lock:
                print(f"[{i}/{len(to_send)}] '{folder_name}'", flush=True)

            # Revalidation juste avant l'envoi : un fichier a pu disparaître/être
            # corrompu entre l'analyse initiale et le moment de l'envoi (gros run --all).
            issues = validate_session(session_dir)
            if issues:
                with print_lock:
                    print(f"  ANNULÉ : fichier(s) manquant(s)/invalide(s) détecté(s) juste avant l'envoi :", flush=True)
                    for issue in issues:
                        print(f"    - {issue}", flush=True)
                return folder_name

            # Résoudre le session_id DB (peut différer du nom de dossier NAS)
            db_session_id = db_resolve_session_id(folder_name)
            with print_lock:
                if db_session_id:
                    print(f"  DB session_id : {db_session_id}", flush=True)
                else:
                    print(f"  Avertissement : session '{folder_name}' introuvable en DB — upload sans mise à jour DB", flush=True)

            zip_path = zip_session(session_dir, Path(tmp_dir))
            zip_size = zip_path.stat().st_size
            with print_lock:
                print(f"  Archive : {zip_path.name}  ({format_size(zip_size)})", flush=True)

            if db_session_id:
                db_start_delivery(db_session_id, zip_size)

            success = upload_zip_to_mistral(str(zip_path))

            if success:
                duration = read_duration(session_dir)
                if db_session_id:
                    db_confirm_delivered(db_session_id, zip_size, duration)
                with dodge_lock:
                    mark_uploaded(root, dodge, folder_name, zip_size, duration)
                move_session_to_sent(session_dir, sent_dir)
            else:
                if db_session_id:
                    db_mark_delivery_failed(db_session_id)

            zip_path.unlink(missing_ok=True)

            with print_lock:
                print(flush=True)

            return folder_name

        with tempfile.TemporaryDirectory() as tmp_dir:
            items = [(i, session_dir, size, tmp_dir)
                     for i, (session_dir, size) in enumerate(to_send, 1)]
            with ThreadPoolExecutor(max_workers=min(UPLOAD_WORKERS, len(items))) as pool:
                list(pool.map(_upload_one, items))

    if dry_run:
        print("\n*** DRY-RUN terminé — rien n'a été modifié ***")
        sys.exit(0)

    # --- Résumé global depuis le dodge file ---
    total_bytes = sum(e["size_bytes"] for e in dodge["sessions"])
    total_seconds = sum(e["duration_seconds"] for e in dodge["sessions"])
    sent_this_run = [e for e in dodge["sessions"] if e["name"] not in already_done]
    sent_names = {e["name"] for e in dodge["sessions"]}
    failed_this_run = [s.name for s, _ in to_send if s.name not in sent_names]

    print("=== Résumé de cette exécution ===")
    for e in sent_this_run:
        print(f"  OK     {e['name']}  ({format_size(e['size_bytes'])}, {format_duration(e['duration_seconds'])})")
    for name in failed_this_run:
        print(f"  ECHEC  {name}")
    for name, issues in invalid_sessions:
        print(f"  INVALIDE  {name}  ({len(issues)} problème(s) : {issues[0]}{'…' if len(issues) > 1 else ''})")
    for name, errs in rejected_sessions:
        print(f"  REJET  {name}  ({len(errs)} erreur(s) : {errs[0]}{'…' if len(errs) > 1 else ''})")
    for name, reason in empty_sessions:
        print(f"  VIDE   {name}  ({reason})")
    if capped_count:
        print(f"  REPORT {capped_count} session(s) non analysée(s) — prochains runs")

    print()
    print("=== Cumul total envoyé (toutes exécutions) ===")
    print(f"  Sessions envoyées : {len(dodge['sessions'])}")
    print(f"  Volume total      : {format_size(total_bytes)}")
    print(f"  Durée totale      : {format_duration(total_seconds)}")

    sys.exit(0 if not failed_this_run else 1)


if __name__ == "__main__":
    main()
