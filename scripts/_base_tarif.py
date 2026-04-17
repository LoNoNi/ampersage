"""
_base_tarif.py - Classe de base partagée par tous les scripts tarif.
Fournit l'implémentation commune des 5 appels (GET, UPDATE, GET_PARAM,
SET_PARAM, INIT_PARAM). Chaque script tarif hérite de cette base.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _reponse(status: bool, mode: str, data=None, error=None) -> dict:
    """Construit la réponse standardisée."""
    return {
        "status": status,
        "mode": mode,
        "error": error,
        "data": data or {},
    }


class BaseTarif:
    """
    Classe de base pour les scripts tarif mock.
    Chaque sous-classe définit NOM, PARAM_DEFAUT et MOCK_DATA.
    """

    NOM: str = ""
    PARAM_DEFAUT: dict = {}
    MOCK_DATA: dict = {}

    def __init__(self, base_dir: Path):
        self.data_file = base_dir / f"{self.NOM}_data.json"
        self.param_file = base_dir / f"{self.NOM}_param.json"

    # ─── Helpers I/O ──────────────────────────────────────────────────────────

    def _lire_data(self) -> Optional[dict]:
        if not self.data_file.exists():
            return None
        with open(self.data_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _ecrire_data(self, data: dict):
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _lire_param(self) -> Optional[dict]:
        if not self.param_file.exists():
            return None
        with open(self.param_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _ecrire_param(self, param: dict):
        with open(self.param_file, "w", encoding="utf-8") as f:
            json.dump(param, f, ensure_ascii=False, indent=2)

    # ─── Les 5 appels ─────────────────────────────────────────────────────────

    def cmd_get(self, params: dict, global_param: dict) -> dict:
        """Lecture seule des données."""
        data = self._lire_data()
        if data is None:
            return _reponse(False, "GET", error="Fichier data absent")
        return _reponse(True, "GET", data=data)

    def cmd_update(self, params: dict, global_param: dict) -> dict:
        """Simule un appel réseau et met à jour les données mock."""
        # Vérification que l'API conso est active
        if not global_param.get("api_conso_statut", False):
            return _reponse(False, "UPDATE", error="API conso non connectée")

        mode_erreur = params.get("simulate_error")
        donnees_existantes = self._lire_data()

        if mode_erreur == "timeout":
            return _reponse(False, "UPDATE",
                            data=donnees_existantes or {},
                            error="Erreur réseau : timeout")

        if mode_erreur == "http500":
            return _reponse(False, "UPDATE",
                            data=donnees_existantes or {},
                            error="Erreur serveur : HTTP 500")

        self._ecrire_data(self.MOCK_DATA)
        logger.info("%s UPDATE : données mock écrites", self.NOM)
        return _reponse(True, "UPDATE", data=self.MOCK_DATA)

    def cmd_get_param(self, params: dict, global_param: dict) -> dict:
        """Retourne les paramètres depuis NOM_param.json."""
        param = self._lire_param()
        if param is None:
            return _reponse(False, "GET_PARAM", error="Fichier param absent")
        return _reponse(True, "GET_PARAM", data=param)

    def cmd_set_param(self, params: dict, global_param: dict) -> dict:
        """Merge partiel des paramètres."""
        param_actuel = self._lire_param() or {}
        param_actuel.update(params)
        self._ecrire_param(param_actuel)
        logger.info("%s SET_PARAM : paramètres mis à jour", self.NOM)
        return _reponse(True, "SET_PARAM", data=param_actuel)

    def cmd_init_param(self, params: dict, global_param: dict) -> dict:
        """Recrée NOM_param.json avec les valeurs par défaut."""
        if not global_param.get("api_conso_statut", False):
            return _reponse(False, "INIT_PARAM", error="API conso non connectée")
        self._ecrire_param(self.PARAM_DEFAUT)
        logger.info("%s INIT_PARAM : paramètres réinitialisés", self.NOM)
        return _reponse(True, "INIT_PARAM", data=self.PARAM_DEFAUT)

    def run(self, mode: str, params: dict = None, global_param: dict = None) -> dict:
        """Point d'entrée appelé par orchestrator.py ou server.py."""
        params = params or {}
        global_param = global_param if global_param is not None else {}

        commandes = {
            "GET": self.cmd_get,
            "UPDATE": self.cmd_update,
            "GET_PARAM": self.cmd_get_param,
            "SET_PARAM": self.cmd_set_param,
            "INIT_PARAM": self.cmd_init_param,
        }

        if mode not in commandes:
            return _reponse(False, mode, error=f"Mode inconnu : {mode}")

        return commandes[mode](params, global_param)
