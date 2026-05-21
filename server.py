"""
server.py - Serveur Flask de l'application.
Expose les routes API et sert les fichiers statiques de l'IHM.
"""

import importlib.util
import json
import logging
import subprocess
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_from_directory

logger = logging.getLogger("server")

BASE_DIR = Path(__file__).parent
WEB_DIR = BASE_DIR / "web"
SCRIPTS_DIR = BASE_DIR / "scripts"
MODULES_COMP_DIR = BASE_DIR / "modules_complementaires"
DATA_DIR = BASE_DIR / "data"
PARAMS_FILE = BASE_DIR / "parametres.json"
VERSION_CHECK_FILE = BASE_DIR / "version_check.json"
GITHUB_REPO = "LoNoNi/ampersage"

# Modules tarif dépréciés : migrés vers generique, ignorés partout
SCRIPTS_DEPRECIES = {"sobry", "trv_base", "trv_hchp", "trv_tempo"}


def _decouvrir_scripts_tarif() -> list:
    """Retourne la liste des noms de scripts tarif actifs dans scripts/tarif/."""
    tarif_dir = SCRIPTS_DIR / "tarif"
    if not tarif_dir.exists():
        return []
    scripts = []
    for sous_dir in sorted(tarif_dir.iterdir()):
        if not sous_dir.is_dir():
            continue
        nom = sous_dir.name
        if nom.startswith("_") or nom in SCRIPTS_DEPRECIES:
            continue
        if (sous_dir / f"{nom}.py").exists():
            scripts.append(nom)
    return scripts

# Référence à Global_param et callbacks injectés au démarrage par orchestrator.py
_global_param: dict = {}
_get_data_fn = None
_relancer_pipeline_fn = None
_relancer_tarifs_fn = None
_importer_csv_fn = None

# Cache des résultats custom (invalidé à chaque nouveau pipeline)
_cache_custom: dict = {}

app = Flask(__name__, static_folder=str(WEB_DIR))


# ─── Gestion des versions ─────────────────────────────────────────────────────


def _lire_version_courante() -> str:
    """Retourne la version courante depuis le tag git le plus récent."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip().lstrip("v")
    except Exception:
        pass
    return "0.0.0"


def _comparer_versions(v1: str, v2: str) -> int:
    """Retourne 1 si v2 > v1, 0 si égaux, -1 si v2 < v1."""
    try:
        t1 = tuple(int(x) for x in v1.split("."))
        t2 = tuple(int(x) for x in v2.split("."))
        if t2 > t1:
            return 1
        if t2 < t1:
            return -1
        return 0
    except Exception:
        return 0


def _verifier_version_github() -> dict:
    """Interroge l'API GitHub Releases pour la dernière version disponible."""
    TZ = ZoneInfo("Europe/Paris")
    try:
        url = "https://api.github.com/repos/{}/releases/latest".format(GITHUB_REPO)
        req = urllib.request.Request(url, headers={"User-Agent": "ampersage-updater"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        tag = data.get("tag_name", "").lstrip("v")
        return {
            "latest": tag,
            "changelog_url": data.get("html_url", ""),
            "checked_at": datetime.now(TZ).isoformat(),
            "error": None,
        }
    except Exception as exc:
        return {
            "latest": None,
            "changelog_url": "",
            "checked_at": datetime.now(TZ).isoformat(),
            "error": str(exc),
        }


def _lire_version_check() -> dict:
    if VERSION_CHECK_FILE.exists():
        try:
            with open(VERSION_CHECK_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _ecrire_version_check(data: dict):
    with open(VERSION_CHECK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _charger_script(nom_script: str):
    """
    Résout et importe dynamiquement un script par son nom.
    Cherche dans scripts/<nom>/<nom>.py et scripts/tarif/<nom>.py.
    """
    candidats = [
        SCRIPTS_DIR / nom_script / f"{nom_script}.py",
        SCRIPTS_DIR / "tarif" / nom_script / f"{nom_script}.py",
        SCRIPTS_DIR / "api_conso" / f"{nom_script}.py",
        MODULES_COMP_DIR / nom_script / f"{nom_script}.py",
    ]
    for chemin in candidats:
        if chemin.exists():
            spec = importlib.util.spec_from_file_location(nom_script, chemin)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


def _sauvegarder_params():
    """Écrit Global_param dans parametres.json, sans la clé DATA (régénérée au démarrage)."""
    a_sauvegarder = {k: v for k, v in _global_param.items() if k != "DATA"}
    with open(PARAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(a_sauvegarder, f, ensure_ascii=False, indent=2)
    logger.info("parametres.json sauvegardé")


def _statut_eco2mix() -> str:
    """Retourne le statut du module éCO2mix (lecture fichier JSON uniquement)."""
    try:
        fichier = MODULES_COMP_DIR / "eco2mix" / "eco2mix.py"
        if not fichier.exists():
            return "module introuvable"
        donnees_file = MODULES_COMP_DIR / "eco2mix" / "data" / "eco2mix_donnees.json"
        if not donnees_file.exists():
            return "non initialisé"
        with open(donnees_file, "r", encoding="utf-8") as f:
            donnees = json.load(f)
        if not donnees:
            return "vide"
        return "actif"
    except Exception:
        return "erreur"


def _statuts_scripts() -> dict:
    """Construit la section 'scripts' de /api/status."""
    if not _global_param.get("api_conso_statut", False):
        return {nom: "non initialisé" for nom in _decouvrir_scripts_tarif()}
    return {
        nom: _global_param.get(f"{nom}_STATUT", "non initialisé")
        for nom in _decouvrir_scripts_tarif()
    }



def _statut_global() -> str:
    """Calcule le statut global de l'application."""
    if not _global_param.get("api_conso_statut", False):
        return "erreur"
    statuts = [
        _global_param.get(f"{nom}_STATUT", "non initialisé")
        for nom in _decouvrir_scripts_tarif()
    ]
    if any(s == "erreur" for s in statuts):
        return "erreur"
    if all(s == "actif" for s in statuts):
        return "ok"
    return "initialisation"


def _trouver_panel(nom_script: str):
    """Retourne le chemin du fichier NOM_panel.html si il existe, sinon None."""
    candidats = [
        SCRIPTS_DIR / nom_script / f"{nom_script}_panel.html",
        SCRIPTS_DIR / "tarif" / nom_script / f"{nom_script}_panel.html",
        MODULES_COMP_DIR / nom_script / f"{nom_script}_panel.html",
    ]
    for chemin in candidats:
        if chemin.exists():
            return chemin
    return None


# ─── Routes ───────────────────────────────────────────────────────────────────


@app.route("/")
@app.route("/debug")
def index():
    """Sert la page principale (mode debug si accès via /debug)."""
    return send_from_directory(str(WEB_DIR), "index.html")


_DATA_FILES_AUTORISES = {"manifest.json", "masques.json", "params.json",
                         "api_conso.json", "eco2mix.json",
                         "api_conso_records.ndjson"}

_MIMETYPES = {
    "api_conso_records.ndjson": "application/x-ndjson",
}
_MIMETYPE_NDJSON = "application/x-ndjson"


@app.route("/data/<fichier>", methods=["GET"])
def data_fichier(fichier: str):
    """Sert les fichiers JSON du pipeline (manifest, masques, params, api_conso, eco2mix, tarifs)."""
    autorise = (
        fichier in _DATA_FILES_AUTORISES
        or (fichier.startswith("tarif_") and fichier.endswith(".ndjson"))
    )
    if not autorise:
        return jsonify({"error": "Fichier non autorisé"}), 403
    chemin = DATA_DIR / fichier
    if not chemin.exists():
        return jsonify({"error": "Fichier non disponible — pipeline en attente"}), 503
    mimetype = _MIMETYPES.get(fichier,
                 _MIMETYPE_NDJSON if fichier.endswith(".ndjson") else "application/json")
    return send_from_directory(str(DATA_DIR), fichier, mimetype=mimetype)


@app.route("/api/panel/<nom_script>", methods=["GET"])
def api_panel(nom_script: str):
    """Retourne le HTML du panel de paramètres d'un script."""
    chemin = _trouver_panel(nom_script)
    if chemin is None:
        return "", 404
    return chemin.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/static/<path:filename>")
def static_files(filename):
    """Sert les fichiers statiques du répertoire web/."""
    return send_from_directory(str(WEB_DIR), filename)


@app.route("/api/status", methods=["GET"])
def api_status():
    """Retourne les statuts en temps réel."""
    return jsonify({
        "global": _statut_global(),
        "data_ready": bool(_get_data_fn and _get_data_fn()),
        "api_conso": _global_param.get("api_conso_STATUT", "non initialisé"),
        "scripts": _statuts_scripts(),
        "modules": {"eco2mix": _statut_eco2mix()},
    })


@app.route("/api/results", methods=["GET"])
def api_results():
    """Retourne DATA complet (via orchestrateur) ou {} si pipeline incomplet."""
    return jsonify(_get_data_fn() if _get_data_fn else {})


@app.route("/api/script", methods=["POST"])
def api_script():
    """Appelle un script dans un mode donné."""
    body = request.get_json(force=True) or {}
    nom_script = body.get("script", "")
    mode = body.get("mode", "")
    params = body.get("params", {})

    if not nom_script or not mode:
        return jsonify({"status": False, "error": "Champs 'script' et 'mode' requis"}), 400

    module = _charger_script(nom_script)
    if module is None:
        return jsonify({"status": False, "error": f"Script '{nom_script}' introuvable"}), 404

    try:
        reponse = module.run(mode=mode, params=params, global_param=_global_param)
    except Exception as exc:
        logger.error("Erreur script %s mode %s : %s", nom_script, mode, exc)
        return jsonify({"status": False, "error": str(exc)}), 500

    return jsonify(reponse)


@app.route("/api/save_params", methods=["POST"])
def api_save_params():
    """
    Sauvegarde un groupe de paramètres.
    Appelle SET_PARAM du script concerné ET met à jour parametres.json.
    """
    body = request.get_json(force=True) or {}
    groupe = body.get("groupe", "")
    params = body.get("params", {})

    if not groupe:
        return jsonify({"status": False, "error": "Champ 'groupe' requis"}), 400

    # Groupe spécial : paramètres globaux
    if groupe == "global":
        _global_param.update(params)
        _sauvegarder_params()
        _cache_custom.clear()
        if _relancer_pipeline_fn:
            _relancer_pipeline_fn()
        return jsonify({"status": True, "data": params})

    # Groupe spécial : config rapports (stockée dans global_param)
    if groupe == "rapport_config":
        _global_param.update(params)
        _sauvegarder_params()
        return jsonify({"status": True, "data": params})

    # Groupe script : appelle SET_PARAM
    module = _charger_script(groupe)
    if module is None:
        return jsonify({"status": False, "error": "Script '{}' introuvable".format(groupe)}), 404

    try:
        reponse = module.run(mode="SET_PARAM", params=params, global_param=_global_param)
    except Exception as exc:
        logger.error("Erreur SET_PARAM %s : %s", groupe, exc)
        return jsonify({"status": False, "error": str(exc)}), 500

    if reponse.get("status"):
        _sauvegarder_params()
        _cache_custom.clear()
        if _relancer_pipeline_fn:
            _relancer_pipeline_fn()

    return jsonify(reponse)


@app.route("/api/tarif/custom/<offre_id>", methods=["GET"])
def api_tarif_custom(offre_id: str):
    """Calcule et retourne les créneaux d'une offre custom (cache invalidé à chaque pipeline)."""
    global _cache_custom
    if offre_id in _cache_custom:
        return jsonify(_cache_custom[offre_id])

    index_file = SCRIPTS_DIR / "tarif" / "generique" / "generique_index.json"
    if not index_file.exists():
        return jsonify({"error": "Index generique absent — pipeline non exécuté"}), 503

    with open(index_file, encoding="utf-8") as f:
        index = json.load(f)

    offre_meta = index.get("offres", {}).get(offre_id)
    if not offre_meta:
        return jsonify({"error": "Offre inconnue : {}".format(offre_id)}), 404
    if offre_meta.get("source") != "custom":
        return jsonify({"error": "Offre non custom"}), 400

    module_nom = offre_meta.get("module", "")
    rep_module = offre_meta.get("rep_module", "")
    if not module_nom or not rep_module:
        return jsonify({"error": "Module non défini pour cette offre"}), 400

    chemin_module = Path(rep_module) / f"{module_nom}.py"
    if not chemin_module.exists():
        return jsonify({"error": f"Module {module_nom} introuvable"}), 404

    try:
        spec   = importlib.util.spec_from_file_location(module_nom, chemin_module)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        offre_config = offre_meta.get("offre_config", {})
        reponse = module.run(mode="GET_CUSTOM", params=offre_config, global_param=_global_param)
    except Exception as exc:
        logger.error("GET_CUSTOM %s : %s", offre_id, exc)
        return jsonify({"error": str(exc)}), 500

    if not reponse.get("status"):
        return jsonify({"error": reponse.get("error", "Erreur inconnue")}), 500

    data = reponse.get("data", {})
    _cache_custom[offre_id] = data
    return jsonify(data)


@app.route("/api/trigger_update", methods=["POST"])
def api_trigger_update():
    """Déclenche manuellement une mise à jour API conso + recalcul des tarifs."""
    _cache_custom.clear()
    if _relancer_pipeline_fn:
        _relancer_pipeline_fn()
    return jsonify({
        "status":  _global_param.get("api_conso_statut", False),
        "statut":  _global_param.get("api_conso_STATUT"),
        "message": _global_param.get("api_conso_message"),
    })


@app.route("/api/import_csv", methods=["POST"])
def api_import_csv():
    """Reçoit un fichier CSV Enedis et l'importe via l'orchestrateur."""
    if "file" not in request.files:
        return jsonify({"status": False, "error": "Fichier manquant"}), 400

    fichier = request.files["file"]
    try:
        contenu = fichier.read().decode("utf-8-sig")  # utf-8-sig gère le BOM éventuel
    except Exception as exc:
        return jsonify({"status": False, "error": "Impossible de lire le fichier : {}".format(exc)}), 400

    if not _importer_csv_fn:
        return jsonify({"status": False, "error": "Orchestrateur non initialisé"}), 503

    reponse = _importer_csv_fn(contenu)
    return jsonify(reponse)


@app.route("/api/version", methods=["GET"])
def api_version():
    """Retourne la version courante et la dernière disponible sur GitHub."""
    courante = _lire_version_courante()
    check = _lire_version_check()
    latest = check.get("latest")
    update_available = False
    if latest:
        update_available = _comparer_versions(courante, latest) > 0
    return jsonify({
        "current": courante,
        "latest": latest,
        "update_available": update_available,
        "changelog_url": check.get("changelog_url", ""),
        "checked_at": check.get("checked_at"),
        "error": check.get("error"),
        "server_path": str(BASE_DIR),
    })




@app.route("/api/eco2mix_creneaux", methods=["GET"])
def api_eco2mix_creneaux():
    """Retourne les créneaux éCO2mix pour le client JS (infobulles)."""
    data = _get_data_fn() if _get_data_fn else {}
    creneaux = data.get("eco2mix", {}).get("creneaux", {})
    return jsonify(creneaux)


@app.route("/api/expose", methods=["GET"])
def api_expose():
    """
    Expose la configuration et l'état des modules.
    Structure : { fournisseurs: {...}, modules_complementaires: {...} }
    """
    # ── Fournisseurs (tarifs + api_conso) ─────────────────────────────────────
    fournisseurs = {}
    for nom in ["api_conso"] + _decouvrir_scripts_tarif():
        module = _charger_script(nom)
        if module is None:
            fournisseurs[nom] = {"statut": "introuvable"}
            continue
        try:
            rep_param = module.run(mode="GET_PARAM", params={}, global_param=_global_param)
            fournisseurs[nom] = {
                "statut": _global_param.get(f"{nom}_STATUT", "non initialisé"),
                "parametres": rep_param.get("data", {}),
            }
        except Exception as exc:
            fournisseurs[nom] = {"statut": "erreur", "erreur": str(exc)}

    # ── Modules complémentaires ───────────────────────────────────────────────
    modules_comp = {}

    # eco2mix
    eco2mix_mod = _charger_script("eco2mix")
    if eco2mix_mod is not None:
        try:
            rep_get    = eco2mix_mod.run(mode="GET",      params={}, global_param=_global_param)
            rep_param  = eco2mix_mod.run(mode="GET_PARAM", params={}, global_param=_global_param)
            rep_stats  = eco2mix_mod.run(mode="GET_STATS", params={}, global_param=_global_param)
            donnee     = rep_get.get("data", {})
            co2_moyen  = (donnee.get("co2_moyen") or {}).get("taux_gco2_kwh")
            co2_marg   = (donnee.get("co2_marginal") or {}).get("taux_gco2_kwh")
            statut     = "actif" if rep_get.get("status") else "vide"
            modules_comp["eco2mix"] = {
                "statut":               statut,
                "description":          "Mix électrique national et CO2 en temps réel (RTE éCO2mix)",
                "note_methodes":        "CO2 moyen = taux officiel RTE / CO2 marginal = calcul AmperSage",
                "parametres":           rep_param.get("data", {}),
                "base_locale":          rep_stats.get("data", {}),
                "taux_co2_moyen_actuel":    co2_moyen,
                "taux_co2_marginal_actuel": co2_marg,
            }
        except Exception as exc:
            modules_comp["eco2mix"] = {"statut": "erreur", "erreur": str(exc)}
    else:
        modules_comp["eco2mix"] = {"statut": "module introuvable"}

    # ── Config globale exposée aux panels ────────────────────────────────────
    modules_comp["rapport_config"] = {
        "statut":     "actif",
        "parametres": {"rapport_config": _global_param.get("rapport_config", {})},
    }

    return jsonify({"fournisseurs": fournisseurs, "modules_complementaires": modules_comp})



# ─── Lancement ────────────────────────────────────────────────────────────────


def _boucle_verif_version():
    """Thread daemon : vérifie la version GitHub une fois par jour."""
    TZ = ZoneInfo("Europe/Paris")
    while True:
        try:
            check = _lire_version_check()
            besoin_verif = True
            checked_at = check.get("checked_at")
            if checked_at:
                try:
                    dt = datetime.fromisoformat(checked_at)
                    if (datetime.now(TZ) - dt).total_seconds() < 86400:
                        besoin_verif = False
                except Exception:
                    pass
            if besoin_verif:
                logger.info("Vérification de version GitHub...")
                result = _verifier_version_github()
                _ecrire_version_check(result)
                courante = _lire_version_courante()
                latest = result.get("latest")
                if latest and _comparer_versions(courante, latest) > 0:
                    logger.info("Nouvelle version disponible : %s (installée : %s)", latest, courante)
                else:
                    logger.info("Version à jour : %s", courante)
        except Exception as exc:
            logger.error("Vérification version : erreur → %s", exc)
        time.sleep(3600)


def demarrer(global_param, get_data_fn, relancer_pipeline_fn,
             relancer_tarifs_fn, importer_csv_fn):
    # type: (dict, object, object, object, object) -> None
    """Démarre le serveur Flask. Reçoit les callbacks de l'orchestrateur."""
    global _global_param, _get_data_fn, _relancer_pipeline_fn
    global _relancer_tarifs_fn, _importer_csv_fn
    _global_param         = global_param
    _get_data_fn          = get_data_fn
    _relancer_pipeline_fn = relancer_pipeline_fn
    _relancer_tarifs_fn   = relancer_tarifs_fn
    _importer_csv_fn      = importer_csv_fn

    threading.Thread(target=_boucle_verif_version, daemon=True, name="verif-version").start()
    port = global_param.get("port", 8080)
    logger.info("Serveur Flask démarré sur http://0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
