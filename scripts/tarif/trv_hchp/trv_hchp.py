"""
trv_hchp.py - Script tarif TRV option Heures Creuses / Heures Pleines.
Expose 5 appels : GET, UPDATE, GET_PARAM, SET_PARAM, INIT_PARAM.

Unités :
  abonnement : €/mois HT
  prix_hp    : c€/kWh HT (Heures Pleines)
  prix_hc    : c€/kWh HT (Heures Creuses)
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

logger = logging.getLogger("trv_hchp")

# Tranches tarifaires TRV option HC/HP
# Source : tarifs réglementés EDF (valeurs de référence)
TRANCHES_DEFAUT = [
    {"puissance": 3,  "abonnement": 12.63, "prix_hp": 20.65, "prix_hc": 15.79, "extinction": False},
    {"puissance": 6,  "abonnement": 16.73, "prix_hp": 20.65, "prix_hc": 15.79, "extinction": False},
    {"puissance": 9,  "abonnement": 20.90, "prix_hp": 20.65, "prix_hc": 15.79, "extinction": False},
    {"puissance": 12, "abonnement": 24.98, "prix_hp": 20.65, "prix_hc": 15.79, "extinction": False},
    {"puissance": 15, "abonnement": 28.97, "prix_hp": 20.65, "prix_hc": 15.79, "extinction": False},
    {"puissance": 18, "abonnement": 32.96, "prix_hp": 20.65, "prix_hc": 15.79, "extinction": False},
    {"puissance": 24, "abonnement": 41.24, "prix_hp": 20.65, "prix_hc": 15.79, "extinction": False},
    {"puissance": 30, "abonnement": 48.23, "prix_hp": 20.65, "prix_hc": 15.79, "extinction": False},
    {"puissance": 36, "abonnement": 56.22, "prix_hp": 20.65, "prix_hc": 15.79, "extinction": False},
]


def _parser_plage_hc(plage_hc):
    """
    Parse "22h00→6h00 + 12h00→14h00" → liste de tuples (debut_min, fin_min).
    Les minutes sont comptées depuis minuit. Nombre de créneaux variable.
    """
    creneaux = []
    for segment in plage_hc.split("+"):
        segment = segment.strip()
        if "\u2192" not in segment:
            continue
        debut_str, fin_str = segment.split("\u2192")

        def _hm(s):
            s = s.strip().replace("h", ":")
            h, m = s.split(":")
            return int(h) * 60 + int(m)

        creneaux.append((_hm(debut_str), _hm(fin_str)))
    return creneaux


def _est_hc(dt, creneaux):
    """Retourne True si l'heure dt tombe dans un créneau HC."""
    t = dt.hour * 60 + dt.minute
    for debut, fin in creneaux:
        if debut <= fin:
            # Créneau normal (ex: 12h00→14h00)
            if debut <= t < fin:
                return True
        else:
            # Créneau traversant minuit (ex: 22h00→6h00)
            if t >= debut or t < fin:
                return True
    return False


class ScriptTrvHcHp(BaseTarif):
    NOM = "trv_hchp"
    PARAM_DEFAUT = {
        "adresse": "https://mock.edf-trv-hchp.example.com/api",
        "commentaire": "Script TRV option HC/HP mock",
        "puissance_souscrite": 6,
        "plage_hc": "22h00\u21926h00 + 12h00\u219214h00",
        "tranches": TRANCHES_DEFAUT,
    }
    MOCK_DATA = {
        "tarif": "trv_hchp",
        "option": "HC/HP",
        "tranches": TRANCHES_DEFAUT,
        "source": "mock",
    }

    def cmd_update(self, params, global_param):
        """Calcule le prix heure par heure (HC ou HP) depuis les données de consommation."""
        if not global_param.get("api_conso_statut", False):
            return _reponse(False, "UPDATE", error="API conso non connectée")

        param = self._lire_param()
        if param is None:
            return _reponse(False, "UPDATE", error="Fichier param absent")

        # Sélection de la tranche active
        tranches = param.get("tranches", [])
        puissance_souscrite = param.get("puissance_souscrite", 6)
        tranche = next(
            (t for t in tranches
             if t["puissance"] == puissance_souscrite and not t.get("extinction", False)),
            None,
        )
        if tranche is None:
            tranche = next((t for t in tranches if not t.get("extinction", False)), None)
        if tranche is None:
            return _reponse(False, "UPDATE", error="Aucune tranche active trouvée")

        records = global_param.get("DATA", {}).get("api_conso", {}).get("records", [])
        if not records:
            return _reponse(False, "UPDATE", error="Aucune donnée de consommation disponible")

        creneaux = _parser_plage_hc(param.get("plage_hc", "22h00\u21926h00"))
        prix_hp  = tranche["prix_hp"]  # c€/kWh
        prix_hc  = tranche["prix_hc"]  # c€/kWh

        # Agrégation par heure — type HC/HP déterminé sur le début du créneau horaire
        heures = {}
        for r in records:
            dt  = datetime.fromisoformat(r["ts"]).astimezone(TZ_PARIS)
            cle = dt.replace(minute=0, second=0, microsecond=0)
            if cle not in heures:
                hc = _est_hc(cle, creneaux)
                heures[cle] = {
                    "kwh":      0.0,
                    "type":     "HC" if hc else "HP",
                    "prix_kwh": prix_hc if hc else prix_hp,
                }
            heures[cle]["kwh"] += r["kwh"]

        resultats = []
        for heure_dt, info in sorted(heures.items()):
            kwh = round(info["kwh"], 3)
            resultats.append({
                "ts":               heure_dt.isoformat(),
                "kwh":              kwh,
                "trv_hchp.type":     info["type"],
                "trv_hchp.prix_kwh": f"{info['prix_kwh']:.2f}",
                "trv_hchp.prix_eur": f"{kwh * info['prix_kwh'] / 100:.4f}",
            })

        data = {
            "tarif":             "trv_hchp",
            "puissance":         tranche["puissance"],
            "abonnement_mensuel": tranche["abonnement"],
            "heures":            resultats,
        }
        self._ecrire_data(data)
        logger.info("trv_hchp UPDATE : %d heures calculées (puissance %d kVA)",
                    len(resultats), tranche["puissance"])
        return _reponse(True, "UPDATE", data=data)


_instance = ScriptTrvHcHp(BASE_DIR)


def run(mode: str, params: dict = None, global_param: dict = None) -> dict:
    """Point d'entrée du script."""
    return _instance.run(mode=mode, params=params, global_param=global_param)


if __name__ == "__main__":
    mode_cli = sys.argv[1] if len(sys.argv) > 1 else "GET"
    print(json.dumps(run(mode_cli), indent=2, ensure_ascii=False))
