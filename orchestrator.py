"""
orchestrator.py - Point d'entrée principal et gestionnaire du pipeline de données.

Charge parametres.json → Global_param, appelle les modules pour construire DATA,
expose une interface simple au serveur Flask.

Règle du set complet :
  get_data() retourne DATA uniquement si le pipeline s'est terminé avec succès.
  Sinon il retourne {} — jamais de données partielles transmises au serveur.

Interface exposée au serveur :
  get_data()              → dict  (DATA complet ou {})
  relancer_pipeline()     → None  (pipeline complet : api_conso → generique → eco2mix)
  relancer_tarifs()       → None  (tous les tarifs découverts, sans rappeler api_conso)
  importer_csv(contenu)   → dict  (réponse brute api_conso après import CSV)
"""

import hashlib
import importlib.util
import json
import logging
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR         = Path(__file__).parent
PARAMS_FILE      = BASE_DIR / "parametres.json"
SCRIPTS_DIR      = BASE_DIR / "scripts"
MODULES_COMP_DIR = BASE_DIR / "modules_complementaires"
DATA_DIR         = BASE_DIR / "data"

TZ_PARIS = ZoneInfo("Europe/Paris")

# Modules tarif dépréciés : migrés vers generique, ignorés par le pipeline
SCRIPTS_DEPRECIES = {"sobry", "trv_base", "trv_hchp", "trv_tempo"}


# ─── Logging ──────────────────────────────────────────────────────────────────


def _configurer_logging():
    """Configure le logging : console + fichier tournant (50 Mo, 7 backups)."""
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")

    fichier_handler = RotatingFileHandler(
        log_dir / "ampersage.log",
        maxBytes=50 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8",
    )
    fichier_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fichier_handler)
    root.addHandler(console_handler)


_configurer_logging()
logger = logging.getLogger("orchestrator")


# ─── État global ──────────────────────────────────────────────────────────────

_global_param = {}   # type: dict
_data_complet = False
_lock = threading.Lock()


# ─── Helpers JSON ─────────────────────────────────────────────────────────────


def _ecrire_json_global(nom, data):
    # type: (str, dict) -> str
    """Écrit un fichier JSON dans DATA_DIR et retourne son hash SHA-256."""
    DATA_DIR.mkdir(exist_ok=True)
    contenu = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    with open(DATA_DIR / nom, "wb") as f:
        f.write(contenu)
    return hashlib.sha256(contenu).hexdigest()


def _ecrire_ndjson_global(nom, records):
    # type: (str, list) -> None
    """Écrit un fichier NDJSON dans DATA_DIR (un objet JSON compact par ligne)."""
    DATA_DIR.mkdir(exist_ok=True)
    lignes = [json.dumps(r, ensure_ascii=False) for r in records]
    contenu = "\n".join(lignes).encode("utf-8")
    with open(DATA_DIR / nom, "wb") as f:
        f.write(contenu)


# ─── Chargement dynamique de scripts ──────────────────────────────────────────


def _decouvrir_scripts_tarif():
    # type: () -> list
    """Retourne la liste des noms de scripts tarif actifs dans scripts/tarif/.
    Ignore les répertoires commençant par '_' et les modules dépréciés.
    """
    tarif_dir = SCRIPTS_DIR / "tarif"
    if not tarif_dir.exists():
        return []
    scripts = []
    for sous_dir in sorted(tarif_dir.iterdir()):
        if not sous_dir.is_dir():
            continue
        nom = sous_dir.name
        if nom.startswith("_"):
            continue
        if nom in SCRIPTS_DEPRECIES:
            continue
        if (sous_dir / "{}.py".format(nom)).exists():
            scripts.append(nom)
    return scripts


def _charger_script(nom_script):
    # type: (str) -> object
    """Importe dynamiquement un script par son nom (cherche dans les répertoires connus)."""
    candidats = [
        SCRIPTS_DIR / nom_script / "{}.py".format(nom_script),
        SCRIPTS_DIR / "tarif" / nom_script / "{}.py".format(nom_script),
        SCRIPTS_DIR / "api_conso" / "{}.py".format(nom_script),
        MODULES_COMP_DIR / nom_script / "{}.py".format(nom_script),
    ]
    for chemin in candidats:
        if chemin.exists():
            spec = importlib.util.spec_from_file_location(nom_script, chemin)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


# ─── Interface publique ────────────────────────────────────────────────────────


def get_data():
    # type: () -> dict
    """
    Retourne DATA si le dernier pipeline s'est terminé avec succès.
    Sinon retourne {} — jamais de set partiel transmis au serveur.
    """
    with _lock:
        if _data_complet:
            return dict(_global_param.get("DATA", {}))
        return {}


def rafraichir_api_conso():
    # type: () -> None
    """Appel HTTP vers l'API externe (UPDATE). À appeler au démarrage ou via auto-refresh."""
    module = _charger_script("api_conso")
    if module is None:
        return
    try:
        module.run(mode="UPDATE", params={}, global_param=_global_param)
        logger.info("rafraichir_api_conso : données mises à jour")
    except Exception as exc:
        logger.error("rafraichir_api_conso : exception → %s", exc)


def relancer_pipeline():
    # type: () -> None
    """
    Pipeline complet : api_conso GET_DATA → generique → eco2mix.
    Aucun appel HTTP : lit uniquement les données déjà sur disque.
    _data_complet passe à True si api_conso a des données disponibles.
    Le DATA final est posé atomiquement (sous lock) à la fin.
    """
    global _data_complet

    with _lock:
        _data_complet = False

    nouveau_data = {}

    # ── api_conso : lecture disque uniquement ─────────────────────────────────

    module_api_conso = _charger_script("api_conso")
    if module_api_conso is None:
        logger.error("Pipeline : script api_conso introuvable")
        _global_param["api_conso_statut"] = False
        _global_param["api_conso_STATUT"] = "erreur"
        return

    try:
        rep_gd = module_api_conso.run(mode="GET_DATA", params={}, global_param=_global_param)
        if rep_gd.get("status"):
            nouveau_data["api_conso"] = rep_gd.get("data", {})
            data = nouveau_data["api_conso"]
            _global_param["api_conso_statut"] = True
            _global_param["api_conso_STATUT"] = "mock" if data.get("mock") else "actif"
            _global_param["api_conso_mode"]   = "mock" if data.get("mock") else "reel"
            logger.info("Pipeline : api_conso GET_DATA OK (%d records)",
                        len(data.get("records", [])))
        else:
            _global_param["api_conso_statut"] = False
            _global_param["api_conso_STATUT"] = "erreur"
            logger.warning("Pipeline : api_conso GET_DATA → %s", rep_gd.get("error"))
            return
    except Exception as exc:
        _global_param["api_conso_statut"] = False
        _global_param["api_conso_STATUT"] = "erreur"
        logger.error("Pipeline : exception api_conso GET_DATA → %s", exc)
        return

    # Rendre api_conso disponible immédiatement pour les modules suivants
    _global_param.setdefault("DATA", {})["api_conso"] = nouveau_data["api_conso"]

    # ── scripts tarif (découverte dynamique) ─────────────────────────────────

    for nom_tarif in _decouvrir_scripts_tarif():
        module_tarif = _charger_script(nom_tarif)
        if module_tarif is None:
            _global_param["{}_STATUT".format(nom_tarif)] = "erreur"
            continue
        try:
            rep = module_tarif.run(mode="UPDATE", params={}, global_param=_global_param)
            if rep.get("status"):
                _global_param["{}_STATUT".format(nom_tarif)] = "actif"
                nouveau_data[nom_tarif] = rep.get("data", {})
                logger.info("Pipeline : %s actif", nom_tarif)
            else:
                _global_param["{}_STATUT".format(nom_tarif)] = "erreur"
                logger.warning("Pipeline : %s en erreur → %s", nom_tarif, rep.get("error"))
        except Exception as exc:
            _global_param["{}_STATUT".format(nom_tarif)] = "erreur"
            logger.error("Pipeline : exception %s → %s", nom_tarif, exc)

    # ── eco2mix ───────────────────────────────────────────────────────────────

    module_eco2mix = _charger_script("eco2mix")
    if module_eco2mix is not None:
        try:
            records_conso  = nouveau_data.get("api_conso", {}).get("records", [])
            ts_list        = [r["ts"] for r in records_conso if r.get("ts")]
            params_eco2mix = (
                {"date_debut": min(ts_list), "date_fin": max(ts_list)}
                if ts_list else {"nb_heures": 2}
            )
            if ts_list:
                logger.info("Pipeline : eco2mix plage %s → %s",
                            params_eco2mix["date_debut"][:10], params_eco2mix["date_fin"][:10])

            rep = module_eco2mix.run(mode="UPDATE", params=params_eco2mix, global_param=_global_param)
            if rep.get("status") and ts_list:
                rep_gd = module_eco2mix.run(
                    mode="GET_DATA",
                    params={"ts_list": ts_list},
                    global_param=_global_param,
                )
                if rep_gd.get("status"):
                    nouveau_data["eco2mix"] = rep_gd.get("data", {})
                    logger.info("Pipeline : eco2mix GET_DATA OK (%d slots)", len(ts_list))
                else:
                    logger.warning("Pipeline : eco2mix GET_DATA → %s", rep_gd.get("error"))
            elif not rep.get("status"):
                logger.warning("Pipeline : eco2mix en erreur → %s", rep.get("error"))
        except Exception as exc:
            logger.error("Pipeline : exception eco2mix → %s", exc)

    # ── Fichiers JSON globaux ─────────────────────────────────────────────────

    masques = {}

    # Masques api_conso
    try:
        rep = module_api_conso.run(mode="GET_MASKS", params={}, global_param=_global_param)
        if rep.get("status"):
            masques["api_conso"] = rep.get("data", {})
    except Exception as exc:
        logger.warning("Pipeline : api_conso GET_MASKS → %s", exc)

    # Masques eco2mix
    if module_eco2mix is not None:
        try:
            rep = module_eco2mix.run(mode="GET_MASKS", params={}, global_param=_global_param)
            if rep.get("status"):
                masques["eco2mix"] = rep.get("data", {})
        except Exception as exc:
            logger.warning("Pipeline : eco2mix GET_MASKS → %s", exc)

    # Masques tarifs (modules découverts)
    for nom_tarif in _decouvrir_scripts_tarif():
        m = _charger_script(nom_tarif)
        if m is None:
            continue
        try:
            rep = m.run(mode="GET_MASKS", params={}, global_param=_global_param)
            if rep.get("status"):
                masques[nom_tarif] = rep.get("data", {})
        except Exception as exc:
            logger.warning("Pipeline : %s GET_MASKS → %s", nom_tarif, exc)

    manifest = {}

    manifest["masques"] = _ecrire_json_global("masques.json", masques)
    logger.info("Pipeline : masques.json écrit (%d modules)", len(masques))

    # params.json — generique GET_CONFIG
    m_gen = _charger_script("generique")
    if m_gen is not None:
        try:
            rep = m_gen.run(mode="GET_CONFIG", params={}, global_param=_global_param)
            if rep.get("status"):
                manifest["params"] = _ecrire_json_global("params.json", rep.get("data", {}))
                logger.info("Pipeline : params.json écrit")
            else:
                logger.warning("Pipeline : generique GET_CONFIG → %s", rep.get("error"))
        except Exception as exc:
            logger.error("Pipeline : exception generique GET_CONFIG → %s", exc)

    # api_conso.json (métadonnées légères) + api_conso_records.ndjson (stream)
    if "api_conso" in nouveau_data:
        records = nouveau_data["api_conso"].get("records", [])
        meta = {
            "count":      len(records),
            "date_debut": records[0]["ts"][:10]  if records else "",
            "date_fin":   records[-1]["ts"][:10] if records else "",
            "mock":       nouveau_data["api_conso"].get("mock", False),
        }
        manifest["api_conso"] = _ecrire_json_global("api_conso.json", meta)
        _ecrire_ndjson_global("api_conso_records.ndjson", records)
        logger.info("Pipeline : api_conso.json + api_conso_records.ndjson écrits (%d records)",
                    len(records))

    # eco2mix.json
    if "eco2mix" in nouveau_data:
        manifest["eco2mix"] = _ecrire_json_global("eco2mix.json", nouveau_data["eco2mix"])
        logger.info("Pipeline : eco2mix.json écrit")

    # manifest.json — toujours écrit en dernier (atomic du point de vue du client)
    _ecrire_json_global("manifest.json", manifest)
    logger.info("Pipeline : manifest.json écrit (%d entrées)", len(manifest))

    # ── Commit atomique ────────────────────────────────────────────────────────

    with _lock:
        _global_param["DATA"] = nouveau_data
        _data_complet = True

    logger.info("Pipeline complet — modules : %s", list(nouveau_data.keys()))


def relancer_tarifs():
    # type: () -> None
    """Relance tous les scripts tarif découverts sans rappeler api_conso."""
    for nom_tarif in _decouvrir_scripts_tarif():
        module = _charger_script(nom_tarif)
        if module is None:
            _global_param["{}_STATUT".format(nom_tarif)] = "erreur"
            continue
        try:
            rep = module.run(mode="UPDATE", params={}, global_param=_global_param)
            if rep.get("status"):
                _global_param["{}_STATUT".format(nom_tarif)] = "actif"
                with _lock:
                    _global_param.setdefault("DATA", {})[nom_tarif] = rep.get("data", {})
                logger.info("%s relancé", nom_tarif)
            else:
                _global_param["{}_STATUT".format(nom_tarif)] = "erreur"
                logger.warning("%s en erreur → %s", nom_tarif, rep.get("error"))
        except Exception as exc:
            _global_param["{}_STATUT".format(nom_tarif)] = "erreur"
            logger.error("Exception %s → %s", nom_tarif, exc)


def importer_csv(contenu):
    # type: (str) -> dict
    """
    Importe un CSV Enedis via api_conso puis relance le pipeline complet.
    L'import lui-même est géré par api_conso (mode IMPORT_CSV).
    Retourne la réponse brute d'api_conso (status / data / error).
    """
    module = _charger_script("api_conso")
    if module is None:
        return {"status": False, "mode": "IMPORT_CSV",
                "error": "Script api_conso introuvable", "data": {}}

    reponse = module.run(
        mode="IMPORT_CSV",
        params={"csv_content": contenu},
        global_param=_global_param,
    )

    if reponse.get("status"):
        data_import = reponse.get("data", {})
        logger.info("Import CSV : %d records importés, total %d",
                    data_import.get("importes", 0), data_import.get("total", 0))
        relancer_pipeline()

    return reponse


# ─── Auto-refresh ─────────────────────────────────────────────────────────────


def _boucle_auto_refresh():
    # type: () -> None
    """Thread daemon : vérifie toutes les heures si un auto-refresh est dû."""
    param_file = SCRIPTS_DIR / "api_conso" / "api_conso_param.json"
    while True:
        time.sleep(3600)
        try:
            if not param_file.exists():
                continue
            with open(param_file, encoding="utf-8") as f:
                param = json.load(f)
            if not param.get("auto_refresh"):
                continue
            prochaine_str = param.get("prochaine_maj")
            if not prochaine_str:
                continue
            prochaine = datetime.fromisoformat(prochaine_str)
            if datetime.now(TZ_PARIS) >= prochaine:
                logger.info("Auto-refresh : déclenchement automatique")
                rafraichir_api_conso()
                relancer_pipeline()
        except Exception as exc:
            logger.error("Auto-refresh : erreur → %s", exc)


# ─── Paramètres ───────────────────────────────────────────────────────────────


def charger_ou_init_params():
    # type: () -> dict
    """Charge parametres.json si présent, sinon retourne les valeurs par défaut."""
    if PARAMS_FILE.exists():
        logger.info("Chargement de parametres.json")
        with open(PARAMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    logger.info("parametres.json absent → paramètres par défaut")
    return {
        "api_conso_statut": False,
        "DATA": {},
        "langue": "FR",
        "port":   8080,
    }


# ─── Point d'entrée ───────────────────────────────────────────────────────────


def main():
    global _global_param
    _global_param = charger_ou_init_params()
    _global_param.setdefault("DATA", {})

    def _demarrage():
        rafraichir_api_conso()   # appel HTTP unique au démarrage
        relancer_pipeline()      # lecture disque + calculs

    # Pipeline initial et auto-refresh lancés en arrière-plan
    threading.Thread(target=_demarrage, daemon=True, name="pipeline-init").start()
    threading.Thread(target=_boucle_auto_refresh, daemon=True, name="auto-refresh").start()

    import server
    server.demarrer(
        global_param=_global_param,
        get_data_fn=get_data,
        relancer_pipeline_fn=relancer_pipeline,
        relancer_tarifs_fn=relancer_tarifs,
        importer_csv_fn=importer_csv,
    )


if __name__ == "__main__":
    main()
