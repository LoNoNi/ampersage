"""
trv_base.py - Script tarif TRV option Base (tarif réglementé EDF).
Expose 5 appels : GET, UPDATE, GET_PARAM, SET_PARAM, INIT_PARAM.

Unités :
  abonnement : €/mois HT
  prix_kwh   : c€/kWh HT
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from _base_tarif import BaseTarif, _reponse

BASE_DIR = Path(__file__).parent
TZ_PARIS = ZoneInfo("Europe/Paris")

logger = logging.getLogger("trv_base")

# Tranches tarifaires TRV option Base
# Source : tarifs réglementés EDF (valeurs de référence)
TRANCHES_DEFAUT = [
    {"puissance": 3,  "abonnement": 12.03, "prix_kwh": 19.40, "extinction": False},
    {"puissance": 6,  "abonnement": 15.65, "prix_kwh": 19.40, "extinction": False},
    {"puissance": 9,  "abonnement": 19.56, "prix_kwh": 19.27, "extinction": True},
    {"puissance": 12, "abonnement": 23.32, "prix_kwh": 19.27, "extinction": True},
    {"puissance": 15, "abonnement": 26.84, "prix_kwh": 19.27, "extinction": True},
    {"puissance": 18, "abonnement": 30.49, "prix_kwh": 19.27, "extinction": True},
    {"puissance": 24, "abonnement": 38.24, "prix_kwh": 19.27, "extinction": True},
    {"puissance": 30, "abonnement": 45.37, "prix_kwh": 19.27, "extinction": True},
    {"puissance": 36, "abonnement": 52.54, "prix_kwh": 19.27, "extinction": True},
]


class ScriptTrvBase(BaseTarif):
    NOM = "trv_base"
    PARAM_DEFAUT = {
        "adresse": "https://mock.edf-trv-base.example.com/api",
        "commentaire": "Script TRV option Base mock",
        "puissance_souscrite": 6,
        "tranches": TRANCHES_DEFAUT,
    }

    def cmd_update(self, params: dict, global_param: dict) -> dict:
        """Calcule le prix heure par heure à partir des données de consommation."""
        if not global_param.get("api_conso_statut", False):
            return _reponse(False, "UPDATE", error="API conso non connectée")

        # Lecture des paramètres tarifaires
        param = self._lire_param()
        if param is None:
            return _reponse(False, "UPDATE", error="Fichier param absent")

        # Sélection de la tranche active
        tranches = param.get("tranches", [])
        puissance_souscrite = param.get("puissance_souscrite")
        if puissance_souscrite:
            tranche = next(
                (t for t in tranches
                 if t["puissance"] == puissance_souscrite and not t.get("extinction", False)),
                None
            )
        else:
            tranche = next((t for t in tranches if not t.get("extinction", False)), None)

        if tranche is None:
            return _reponse(False, "UPDATE", error="Aucune tranche active trouvée")

        # Récupération des records de consommation
        records = global_param.get("DATA", {}).get("api_conso", {}).get("records", [])
        if not records:
            return _reponse(False, "UPDATE", error="Aucune donnée de consommation disponible")

        # Agrégation par heure et calcul du prix
        prix_kwh = tranche["prix_kwh"]  # c€/kWh
        heures: dict = {}
        for r in records:
            dt = datetime.fromisoformat(r["ts"]).astimezone(TZ_PARIS)
            cle = dt.replace(minute=0, second=0, microsecond=0)
            heures[cle] = heures.get(cle, 0.0) + r["kwh"]

        resultats = []
        for heure_dt, kwh_total in sorted(heures.items()):
            resultats.append({
                "ts":           heure_dt.isoformat(),
                "kwh":          round(kwh_total, 3),
                "trv_base.prix": f"{kwh_total * prix_kwh / 100:.4f}",
            })

        data = {
            "tarif":             "trv_base",
            "puissance":         tranche["puissance"],
            "prix_kwh":          prix_kwh,
            "abonnement_mensuel": tranche["abonnement"],
            "heures":            resultats,
        }

        self._ecrire_data(data)
        logger.info("trv_base UPDATE : %d heures calculées (puissance %d kVA)",
                    len(resultats), tranche["puissance"])
        return _reponse(True, "UPDATE", data=data)


_instance = ScriptTrvBase(BASE_DIR)


def run(mode: str, params: dict = None, global_param: dict = None) -> dict:
    """Point d'entrée du script."""
    return _instance.run(mode=mode, params=params, global_param=global_param)


if __name__ == "__main__":
    mode_cli = sys.argv[1] if len(sys.argv) > 1 else "GET"
    print(json.dumps(run(mode_cli), indent=2, ensure_ascii=False))
