"""
trv_tempo.py - Script tarif TRV option Tempo (jours Bleu/Blanc/Rouge).
Expose 5 appels : GET, UPDATE, GET_PARAM, SET_PARAM, INIT_PARAM.

Unités :
  abonnement : €/mois TTC
  prix_*     : c€/kWh TTC
"""

import json
import logging
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from _base_tarif import BaseTarif, _reponse

BASE_DIR = Path(__file__).parent
TZ_PARIS  = ZoneInfo("Europe/Paris")

logger = logging.getLogger("trv_tempo")

# codeJour renvoyé par api-couleur-tempo.fr
_CODE_COULEUR = {1: "BLEU", 2: "BLANC", 3: "ROUGE"}
_COULEUR_CLASS = {"BLEU": "tempo-bleu", "BLANC": "tempo-blanc", "ROUGE": "tempo-rouge"}

# Tranches tarifaires TRV option Tempo
# Source : tarifs réglementés EDF (valeurs de référence)
TRANCHES_DEFAUT = [
    {"puissance": 6,  "abonnement": 15.59, "bleu_hc": 13.25, "bleu_hp": 16.12, "blanc_hc": 14.99, "blanc_hp": 18.71, "rouge_hc": 15.75, "rouge_hp": 70.60, "extinction": False},
    {"puissance": 9,  "abonnement": 19.38, "bleu_hc": 13.25, "bleu_hp": 16.12, "blanc_hc": 14.99, "blanc_hp": 18.71, "rouge_hc": 15.75, "rouge_hp": 70.60, "extinction": False},
    {"puissance": 12, "abonnement": 23.07, "bleu_hc": 13.25, "bleu_hp": 16.12, "blanc_hc": 14.99, "blanc_hp": 18.71, "rouge_hc": 15.75, "rouge_hp": 70.60, "extinction": False},
    {"puissance": 15, "abonnement": 26.47, "bleu_hc": 13.25, "bleu_hp": 16.12, "blanc_hc": 14.99, "blanc_hp": 18.71, "rouge_hc": 15.75, "rouge_hp": 70.60, "extinction": False},
    {"puissance": 18, "abonnement": 30.04, "bleu_hc": 13.25, "bleu_hp": 16.12, "blanc_hc": 14.99, "blanc_hp": 18.71, "rouge_hc": 15.75, "rouge_hp": 70.60, "extinction": False},
    {"puissance": 30, "abonnement": 44.73, "bleu_hc": 13.25, "bleu_hp": 16.12, "blanc_hc": 14.99, "blanc_hp": 18.71, "rouge_hc": 15.75, "rouge_hp": 70.60, "extinction": False},
    {"puissance": 36, "abonnement": 52.42, "bleu_hc": 13.25, "bleu_hp": 16.12, "blanc_hc": 14.99, "blanc_hp": 18.71, "rouge_hc": 15.75, "rouge_hp": 70.60, "extinction": False},
]


def _parser_heure(heure_str):
    """Parse "6h00" → nombre de minutes depuis minuit."""
    s = heure_str.strip().replace("h", ":")
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _date_tempo(dt, changement_min):
    """
    Retourne la date Tempo applicable pour un datetime donné.
    Si l'heure est avant heure_changement → date J-1.
    """
    t = dt.hour * 60 + dt.minute
    if t < changement_min:
        return (dt - timedelta(days=1)).date()
    return dt.date()


def _parser_plage_hc(plage_hc):
    """Parse "22h00→6h00 + 12h00→14h00" → liste de tuples (debut_min, fin_min)."""
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
            if debut <= t < fin:
                return True
        else:
            if t >= debut or t < fin:
                return True
    return False


def _couleur_jour(date_str, adresse):
    """
    Appelle api-couleur-tempo.fr pour récupérer la couleur d'un jour donné.
    Retourne (couleur_str, None) ou (None, message_erreur).
    """
    url = "{}/jourTempo/{}".format(adresse.rstrip("/"), date_str)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        code = data.get("codeJour")
        if code not in _CODE_COULEUR:
            return None, "codeJour inconnu : {}".format(code)
        return _CODE_COULEUR[code], None
    except urllib.error.HTTPError as e:
        return None, "HTTP {} pour {}".format(e.code, url)
    except urllib.error.URLError as e:
        return None, "Connexion impossible : {}".format(e.reason)
    except Exception as e:
        return None, str(e)


class ScriptTrvTempo(BaseTarif):
    NOM = "trv_tempo"
    PARAM_DEFAUT = {
        "adresse":                "https://api-couleur-tempo.fr/api",
        "commentaire":            "Script TRV option Tempo",
        "puissance_souscrite":    6,
        "plage_hc":               "22h00\u21926h00",
        "heure_changement_jour":  "6h00",
        "tranches":               TRANCHES_DEFAUT,
    }
    MOCK_DATA = {
        "tarif": "trv_tempo",
        "option": "Tempo",
        "tranches": TRANCHES_DEFAUT,
        "source": "mock",
    }

    def _lire_cache(self):
        """Charge le cache local (couleurs + résultats). Retourne un dict vide si absent."""
        chemin = BASE_DIR / "trv_tempo_cache.json"
        if not chemin.exists():
            return {"couleurs": {}, "resultats": {}}
        try:
            return json.loads(chemin.read_text(encoding="utf-8"))
        except Exception:
            return {"couleurs": {}, "resultats": {}}

    def _ecrire_cache(self, cache):
        """Sauvegarde le cache local."""
        chemin = BASE_DIR / "trv_tempo_cache.json"
        chemin.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def cmd_update(self, params, global_param):
        """Calcule les prix Tempo heure par heure avec cache local des couleurs et résultats."""
        if not global_param.get("api_conso_statut", False):
            return _reponse(False, "UPDATE", error="API conso non connectée")

        param = self._lire_param()
        if param is None:
            return _reponse(False, "UPDATE", error="Fichier param absent")

        # Sélection de la tranche active
        tranches            = param.get("tranches", [])
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

        adresse        = param.get("adresse", self.PARAM_DEFAUT["adresse"])
        creneaux       = _parser_plage_hc(param.get("plage_hc", self.PARAM_DEFAUT["plage_hc"]))
        changement_min = _parser_heure(
            param.get("heure_changement_jour", self.PARAM_DEFAUT["heure_changement_jour"])
        )

        # Chargement du cache
        cache           = self._lire_cache()
        cache_couleurs  = cache.get("couleurs", {})
        cache_resultats = cache.get("resultats", {})
        cache_modifie   = False

        # Agrégation des records en buckets horaires (kwh brut)
        kwh_par_heure = {}
        for r in records:
            dt  = datetime.fromisoformat(r["ts"]).astimezone(TZ_PARIS)
            cle = dt.replace(minute=0, second=0, microsecond=0)
            kwh_par_heure[cle] = kwh_par_heure.get(cle, 0.0) + r["kwh"]

        # Calcul ou récupération depuis le cache pour chaque heure
        resultats = []
        for heure_dt in sorted(kwh_par_heure):
            kwh      = round(kwh_par_heure[heure_dt], 3)
            heure_ts = heure_dt.isoformat()

            # Résultat déjà en cache avec le même kwh → réutilisation directe
            cached = cache_resultats.get(heure_ts)
            if cached and cached.get("kwh") == kwh:
                resultats.append(cached)
                continue

            # Couleur : cache local d'abord, sinon appel API
            date_str = _date_tempo(heure_dt, changement_min).isoformat()
            if date_str not in cache_couleurs:
                couleur, erreur = _couleur_jour(date_str, adresse)
                if erreur:
                    return _reponse(False, "UPDATE",
                                    error="Erreur API couleur Tempo ({}) : {}".format(date_str, erreur))
                cache_couleurs[date_str] = couleur
                cache_modifie = True
            else:
                couleur = cache_couleurs[date_str]

            # Calcul
            hc        = _est_hc(heure_dt, creneaux)
            type_slot = "HC" if hc else "HP"
            col_key   = "{}_{}".format(couleur.lower(), type_slot.lower())
            prix_kwh  = tranche.get(col_key, 0.0)

            entree = {
                "ts":                      heure_ts,
                "kwh":                     kwh,
                "trv_tempo.couleur":        couleur,
                "trv_tempo.couleur_class":  _COULEUR_CLASS[couleur],
                "trv_tempo.type":           type_slot,
                "trv_tempo.prix_kwh":       "{:.2f}".format(prix_kwh),
                "trv_tempo.prix_eur":       "{:.4f}".format(kwh * prix_kwh / 100),
            }
            cache_resultats[heure_ts] = entree
            cache_modifie = True
            resultats.append(entree)

        # Sauvegarde du cache uniquement si modifié
        if cache_modifie:
            self._ecrire_cache({"couleurs": cache_couleurs, "resultats": cache_resultats})

        data = {
            "tarif":              "trv_tempo",
            "puissance":          tranche["puissance"],
            "abonnement_mensuel": tranche["abonnement"],
            "heures":             resultats,
        }
        self._ecrire_data(data)
        logger.info("trv_tempo UPDATE : %d heures calculées (%s)",
                    len(resultats), ", ".join(
                        "{}: {}".format(c, sum(1 for r in resultats if r["trv_tempo.couleur"] == c))
                        for c in ["BLEU", "BLANC", "ROUGE"]
                    ))
        return _reponse(True, "UPDATE", data=data)


_instance = ScriptTrvTempo(BASE_DIR)


def run(mode: str, params: dict = None, global_param: dict = None) -> dict:
    """Point d'entrée du script."""
    return _instance.run(mode=mode, params=params, global_param=global_param)


if __name__ == "__main__":
    mode_cli = sys.argv[1] if len(sys.argv) > 1 else "GET"
    print(json.dumps(run(mode_cli), indent=2, ensure_ascii=False))
