"""
server.py - Serveur Flask de l'application.
Expose les routes API et sert les fichiers statiques de l'IHM.
"""

import importlib.util
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_from_directory

logger = logging.getLogger("server")

BASE_DIR = Path(__file__).parent
WEB_DIR = BASE_DIR / "web"
SCRIPTS_DIR = BASE_DIR / "scripts"
PARAMS_FILE = BASE_DIR / "parametres.json"

# Référence à Global_param injectée au démarrage
_global_param: dict = {}

app = Flask(__name__, static_folder=str(WEB_DIR))


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


def _statuts_scripts() -> dict:
    """Construit la section 'scripts' de /api/status."""
    noms = ["sobry", "trv_base", "trv_hchp", "trv_tempo"]
    api_conso_actif = _global_param.get("api_conso_statut", False)
    result = {}
    for nom in noms:
        if not api_conso_actif:
            result[nom] = "non initialisé"
        else:
            result[nom] = _global_param.get(f"{nom}_STATUT", "non initialisé")
    return result


def _relancer_pipeline():
    """
    Relance le pipeline complet : API conso UPDATE puis scripts tarif.
    Appelé après chaque sauvegarde de paramètres.
    """
    scripts_tarif = ["sobry", "trv_base", "trv_hchp", "trv_tempo"]

    # API conso
    module_api_conso = _charger_script("api_conso")
    if module_api_conso is None:
        logger.error("Pipeline : script API conso introuvable")
        _global_param["api_conso_statut"] = False
        _global_param["api_conso_STATUT"] = "erreur"
        return

    logger.info("Pipeline : appel API conso UPDATE")
    try:
        reponse = module_api_conso.run(mode="UPDATE", params={}, global_param=_global_param)
    except Exception as exc:
        logger.error("Pipeline : exception API conso → %s", exc)
        _global_param["api_conso_statut"] = False
        _global_param["api_conso_STATUT"] = "erreur"
        _global_param["api_conso_mode"] = "erreur"
        _global_param["api_conso_message"] = "Erreur inattendue : {}".format(exc)
        return

    data = reponse.get("data", {})

    if reponse.get("status"):
        _global_param["api_conso_statut"] = True
        _global_param.setdefault("DATA", {})["api_conso"] = data
        if data.get("mock"):
            _global_param["api_conso_STATUT"] = "mock"
            _global_param["api_conso_mode"] = "mock"
            _global_param["api_conso_message"] = (
                "Aucun token configuré — données de démonstration affichées. "
                "Renseignez votre token dans le panneau API Conso pour accéder à vos vraies données."
            )
            logger.info("Pipeline : API conso mode mock")
        else:
            _global_param["api_conso_STATUT"] = "actif"
            _global_param["api_conso_mode"] = "reel"
            _global_param["api_conso_message"] = None
            logger.info("Pipeline : API conso données réelles")
    else:
        erreur = reponse.get("error", "erreur inconnue")
        _global_param["api_conso_statut"] = False
        _global_param["api_conso_STATUT"] = "erreur"
        _global_param["api_conso_mode"] = "erreur"
        _global_param["api_conso_message"] = "Erreur API : {}".format(erreur)
        # Conserver les données mémorisées si disponibles
        if data:
            _global_param.setdefault("DATA", {})["api_conso"] = data
        logger.warning("Pipeline : API conso en erreur → %s", erreur)
        return

    # Scripts tarif (indépendants)
    for nom in scripts_tarif:
        module = _charger_script(nom)
        if module is None:
            _global_param[f"{nom}_STATUT"] = "erreur"
            continue
        try:
            rep = module.run(mode="UPDATE", params={}, global_param=_global_param)
            if rep.get("status"):
                _global_param[f"{nom}_STATUT"] = "actif"
                _global_param["DATA"][nom] = rep.get("data", {})
                logger.info("Pipeline : %s actif", nom)
            else:
                _global_param[f"{nom}_STATUT"] = "erreur"
                logger.warning("Pipeline : %s en erreur → %s", nom, rep.get("error"))
        except Exception as exc:
            _global_param[f"{nom}_STATUT"] = "erreur"
            logger.error("Pipeline : exception %s → %s", nom, exc)


def _relancer_tarifs():
    """Relance uniquement les scripts tarif, sans rappeler l'API conso."""
    for nom in ["sobry", "trv_base", "trv_hchp", "trv_tempo"]:
        module = _charger_script(nom)
        if module is None:
            _global_param[f"{nom}_STATUT"] = "erreur"
            continue
        try:
            rep = module.run(mode="UPDATE", params={}, global_param=_global_param)
            if rep.get("status"):
                _global_param[f"{nom}_STATUT"] = "actif"
                _global_param["DATA"][nom] = rep.get("data", {})
            else:
                _global_param[f"{nom}_STATUT"] = "erreur"
                logger.warning("Tarif %s en erreur → %s", nom, rep.get("error"))
        except Exception as exc:
            _global_param[f"{nom}_STATUT"] = "erreur"
            logger.error("Exception tarif %s → %s", nom, exc)


def _statut_global() -> str:
    """Calcule le statut global de l'application."""
    if not _global_param.get("api_conso_statut", False):
        return "erreur"
    statuts = [
        _global_param.get(f"{nom}_STATUT", "non initialisé")
        for nom in ["sobry", "trv_base", "trv_hchp", "trv_tempo"]
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
    ]
    for chemin in candidats:
        if chemin.exists():
            return chemin
    return None


# ─── Routes ───────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    """Sert la page principale."""
    return send_from_directory(str(WEB_DIR), "index.html")


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
        "api_conso": _global_param.get("api_conso_STATUT", "non initialisé"),
        "scripts": _statuts_scripts(),
    })


@app.route("/api/results", methods=["GET"])
def api_results():
    """Retourne Global_param.DATA."""
    return jsonify(_global_param.get("DATA", {}))


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
        _relancer_pipeline()
        return jsonify({"status": True, "data": params})

    # Groupe script : appelle SET_PARAM
    module = _charger_script(groupe)
    if module is None:
        return jsonify({"status": False, "error": f"Script '{groupe}' introuvable"}), 404

    try:
        reponse = module.run(mode="SET_PARAM", params=params, global_param=_global_param)
    except Exception as exc:
        logger.error("Erreur SET_PARAM %s : %s", groupe, exc)
        return jsonify({"status": False, "error": str(exc)}), 500

    if reponse.get("status"):
        _sauvegarder_params()
        _relancer_pipeline()

    return jsonify(reponse)


@app.route("/api/trigger_update", methods=["POST"])
def api_trigger_update():
    """Déclenche manuellement une mise à jour API conso + recalcul des tarifs."""
    _relancer_pipeline()
    return jsonify({
        "status": _global_param.get("api_conso_statut", False),
        "statut": _global_param.get("api_conso_STATUT"),
        "message": _global_param.get("api_conso_message"),
    })


@app.route("/api/import_csv", methods=["POST"])
def api_import_csv():
    """Reçoit un fichier CSV Enedis et l'importe via api_conso IMPORT_CSV."""
    if "file" not in request.files:
        return jsonify({"status": False, "error": "Fichier manquant"}), 400

    fichier = request.files["file"]
    try:
        # utf-8-sig pour gérer le BOM éventuel
        contenu = fichier.read().decode("utf-8-sig")
    except Exception as exc:
        return jsonify({"status": False, "error": "Impossible de lire le fichier : {}".format(exc)}), 400

    module = _charger_script("api_conso")
    if module is None:
        return jsonify({"status": False, "error": "Script api_conso introuvable"}), 404

    reponse = module.run(mode="IMPORT_CSV", params={"csv_content": contenu}, global_param=_global_param)

    if reponse.get("status"):
        data = reponse.get("data", {})
        _global_param.setdefault("DATA", {})["api_conso"] = data
        _global_param["api_conso_statut"] = True
        _global_param["api_conso_mode"] = "reel" if not data.get("mock") else "mock"
        _global_param["api_conso_message"] = None
        _relancer_tarifs()
        logger.info("Import CSV : %d records importés, total %d",
                    data.get("importes", 0), data.get("total", 0))

    return jsonify(reponse)


@app.route("/api/main", methods=["GET"])
def api_main():
    """Exécute main.py et retourne le HTML produit."""
    main_path = BASE_DIR / "main.py"
    if not main_path.exists():
        return jsonify({"status": False, "error": "main.py introuvable"}), 404

    spec = importlib.util.spec_from_file_location("main", main_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    html = module.run(_global_param)
    return jsonify({"status": True, "html": html})


# ─── Lancement ────────────────────────────────────────────────────────────────


def _boucle_auto_refresh():
    """Thread daemon : vérifie toutes les heures si un auto-refresh est dû."""
    TZ = ZoneInfo("Europe/Paris")
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
            if datetime.now(TZ) >= prochaine:
                logger.info("Auto-refresh : déclenchement automatique")
                _relancer_pipeline()
        except Exception as exc:
            logger.error("Auto-refresh : erreur → %s", exc)


def demarrer(global_param: dict):
    """Démarre le serveur Flask avec les paramètres globaux."""
    global _global_param
    _global_param = global_param
    threading.Thread(target=_boucle_auto_refresh, daemon=True, name="auto-refresh").start()
    port = global_param.get("port", 8080)
    logger.info("Serveur Flask démarré sur http://0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    # Lancement autonome (sans orchestrator)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    demarrer({"port": 8080, "api_conso_statut": False, "DATA": {}})
