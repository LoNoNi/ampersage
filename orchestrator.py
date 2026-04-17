"""
orchestrator.py - Point d'entrée principal de l'application.
Initialise Global_param, exécute les scripts, lance le serveur.
"""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List

# Répertoire racine du projet (défini tôt pour configurer le logging)
BASE_DIR = Path(__file__).parent

def _configurer_logging():
    """Configure le logging : console + fichier tournant (50 Mo, 7 jours de backups)."""
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")

    # Handler fichier : 50 Mo max, 7 fichiers conservés
    fichier_handler = RotatingFileHandler(
        log_dir / "ampersage.log",
        maxBytes=50 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8"
    )
    fichier_handler.setFormatter(formatter)

    # Handler console
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fichier_handler)
    root.addHandler(console_handler)

_configurer_logging()
logger = logging.getLogger("orchestrator")


PARAMS_FILE = BASE_DIR / "parametres.json"
SCRIPTS_DIR = BASE_DIR / "scripts"

# Dossiers exclus du scan automatique
EXCLUDED_DIRS = {"voiture"}

# Variable globale partagée entre les modules
Global_param = {}


def _decouvrir_scripts() -> List[str]:
    """Parcourt scripts/ et retourne la liste des noms de scripts découverts."""
    noms = []
    for dossier in SCRIPTS_DIR.iterdir():
        if not dossier.is_dir():
            continue
        if dossier.name in EXCLUDED_DIRS:
            continue
        for fichier in dossier.glob("*.py"):
            # Ignorer les fichiers internes (ex: _base_tarif.py)
            if not fichier.stem.startswith("_"):
                noms.append(fichier.stem)
    return noms


def _init_global_param() -> dict:
    """Crée Global_param avec les valeurs par défaut et initialise les statuts des scripts découverts."""
    param = {
        "api_conso_statut": False,
        "DATA": {},
        "historique_start_date": None,
        "langue": "FR",
        "port": 8080,
    }
    for nom in _decouvrir_scripts():
        param[f"{nom}_STATUT"] = "non initialisé"
    return param


def charger_ou_init_params() -> dict:
    """Charge parametres.json si présent, sinon initialise Global_param par défaut."""
    if PARAMS_FILE.exists():
        logger.info("Chargement de parametres.json")
        with open(PARAMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        logger.info("parametres.json absent → initialisation des paramètres par défaut")
        return _init_global_param()


def appeler_script(module_path: str, mode: str, params: dict = None) -> dict:
    """
    Importe dynamiquement un script et appelle sa fonction run().
    Retourne la réponse standardisée du script.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("script_module", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run(mode=mode, params=params or {}, global_param=Global_param)


def lancer_api_conso() -> bool:
    """Appelle api_conso.py en mode UPDATE. Retourne True si succès (mock ou réel)."""
    chemin = SCRIPTS_DIR / "api_conso" / "api_conso.py"
    if not chemin.exists():
        logger.error("Script API conso introuvable : %s", chemin)
        Global_param["api_conso_STATUT"] = "erreur"
        Global_param["api_conso_mode"] = "erreur"
        Global_param["api_conso_message"] = "Script api_conso introuvable."
        return False

    logger.info("Appel API conso en mode UPDATE")
    reponse = appeler_script(str(chemin), mode="UPDATE")
    data = reponse.get("data", {})

    if reponse.get("status"):
        Global_param["api_conso_statut"] = True
        Global_param["DATA"]["api_conso"] = data
        if data.get("mock"):
            Global_param["api_conso_STATUT"] = "mock"
            Global_param["api_conso_mode"] = "mock"
            Global_param["api_conso_message"] = (
                "Aucun token configuré — données de démonstration affichées. "
                "Renseignez votre token dans le panneau API Conso pour accéder à vos vraies données."
            )
            logger.info("API conso : mode mock (token absent)")
        else:
            Global_param["api_conso_STATUT"] = "actif"
            Global_param["api_conso_mode"] = "reel"
            Global_param["api_conso_message"] = None
            logger.info("API conso : données réelles chargées")
        return True
    else:
        erreur = reponse.get("error", "erreur inconnue")
        Global_param["api_conso_statut"] = False
        Global_param["api_conso_STATUT"] = "erreur"
        Global_param["api_conso_mode"] = "erreur"
        Global_param["api_conso_message"] = "Erreur API : {}".format(erreur)
        # Conserver les données mémorisées si disponibles
        if data:
            Global_param["DATA"]["api_conso"] = data
        logger.error("API conso : échec → %s", erreur)
        return False


def lancer_scripts_tarif():
    """Appelle chaque script tarif de façon indépendante."""
    scripts_tarif = ["sobry", "trv_base", "trv_hchp", "trv_tempo"]

    for nom in scripts_tarif:
        chemin = SCRIPTS_DIR / "tarif" / nom / f"{nom}.py"
        if not chemin.exists():
            logger.warning("Script tarif introuvable : %s", chemin)
            Global_param[f"{nom}_STATUT"] = "erreur"
            continue

        logger.info("Appel %s en mode UPDATE", nom)
        try:
            reponse = appeler_script(str(chemin), mode="UPDATE")
            if reponse.get("status"):
                Global_param[f"{nom}_STATUT"] = "actif"
                Global_param["DATA"][nom] = reponse.get("data", {})
                logger.info("%s : mise à jour réussie", nom)
            else:
                Global_param[f"{nom}_STATUT"] = "erreur"
                logger.warning("%s : échec → %s", nom, reponse.get("error", "erreur inconnue"))
        except Exception as exc:
            # Une erreur sur un script tarif ne bloque pas les autres
            Global_param[f"{nom}_STATUT"] = "erreur"
            logger.error("%s : exception non gérée → %s", nom, exc)


def main():
    global Global_param

    # Étape 1 : charger ou initialiser les paramètres
    Global_param = charger_ou_init_params()
    Global_param.setdefault("DATA", {})

    # Étape 2a : lancer l'API conso
    api_conso_ok = lancer_api_conso()

    # Étape 2b : si API conso échoue, le serveur démarre quand même en état dégradé
    if not api_conso_ok:
        logger.warning(
            "API conso non disponible (%s). "
            "Le serveur démarre en état dégradé — configurez le token via l'IHM.",
            Global_param.get("api_conso_STATUT", "erreur")
        )
    else:
        # Étape 2c : lancer les scripts tarif uniquement si l'API conso est active
        lancer_scripts_tarif()

    # Étape 3 : démarrer le serveur Flask dans tous les cas
    logger.info("Démarrage du serveur Flask sur le port %d", Global_param.get("port", 8080))
    import server
    server.demarrer(Global_param)


if __name__ == "__main__":
    main()
