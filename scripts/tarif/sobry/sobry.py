"""
sobry.py - Script tarif Sobry (tarif dynamique EPEX Spot Day-Ahead).
Expose 5 appels : GET, UPDATE, GET_PARAM, SET_PARAM, INIT_PARAM.

Unités :
  abonnement : €/mois HT
  marge_ht   : €/kWh HT
  marge_ttc  : €/kWh TTC

Formule de base (tarifs bruts, stockés dans cache_resultats) :
  prix_ht  = (price_ht_eur_kwh  + marge_ht)  × 100  [c€/kWh]
  prix_ttc = (price_ttc_eur_kwh + marge_ttc) × 100  [c€/kWh]

Formule produit (SoCap / SoFlex — colonnes affichées) :
  avg_mois = moyenne mensuelle de price_ht/ttc_eur_kwh pour ce TURPE
  base_eff = plafond_saison si avg_mois > plafond_saison, sinon price_ht/ttc_eur_kwh heure par heure
  prix_produit = (base_eff + marge + prime_couverture) × 100  [c€/kWh]

Cache à 2 niveaux :
  resultats[turpe_type][heure_ts].kwh == kwh → réutilisation directe
  tarifs[turpe_type][date_str][heure_h] présent → recalcul sans API
  Débit : au plus len(turpe_actifs) requêtes par UPDATE (< 100/min garantis)
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
TZ_PARIS = ZoneInfo("Europe/Paris")

logger = logging.getLogger("sobry")

_TURPE_TYPES  = ["CU", "CU4", "MU4", "MUDT", "LU"]
_TURPE_CEKWH  = {"CU": 5.10, "CU4": 4.80, "MU4": 3.80, "MUDT": 3.60, "LU": 2.90}
_GRANULARITES = ["quarter_hourly", "hourly", "daily"]
_PRODUITS     = ["socap", "soflex"]

TRANCHES_DEFAUT = [
    {"puissance":  3, "abonnement":  9.53, "extinction": False},
    {"puissance":  4, "abonnement": 10.53, "extinction": False},
    {"puissance":  5, "abonnement": 11.52, "extinction": False},
    {"puissance":  6, "abonnement": 12.52, "extinction": False},
    {"puissance":  7, "abonnement": 13.52, "extinction": False},
    {"puissance":  8, "abonnement": 14.51, "extinction": False},
    {"puissance":  9, "abonnement": 15.51, "extinction": False},
    {"puissance": 10, "abonnement": 16.51, "extinction": False},
    {"puissance": 11, "abonnement": 17.50, "extinction": False},
    {"puissance": 12, "abonnement": 18.50, "extinction": False},
    {"puissance": 13, "abonnement": 19.50, "extinction": False},
    {"puissance": 14, "abonnement": 20.49, "extinction": False},
    {"puissance": 15, "abonnement": 21.49, "extinction": False},
    {"puissance": 16, "abonnement": 23.79, "extinction": False},
    {"puissance": 17, "abonnement": 26.10, "extinction": False},
    {"puissance": 18, "abonnement": 28.40, "extinction": False},
    {"puissance": 19, "abonnement": 28.90, "extinction": False},
    {"puissance": 20, "abonnement": 29.40, "extinction": False},
    {"puissance": 21, "abonnement": 29.90, "extinction": False},
    {"puissance": 22, "abonnement": 30.39, "extinction": False},
    {"puissance": 23, "abonnement": 30.89, "extinction": False},
    {"puissance": 24, "abonnement": 31.39, "extinction": False},
    {"puissance": 25, "abonnement": 33.57, "extinction": False},
    {"puissance": 26, "abonnement": 35.75, "extinction": False},
    {"puissance": 27, "abonnement": 37.93, "extinction": False},
    {"puissance": 28, "abonnement": 40.10, "extinction": False},
    {"puissance": 29, "abonnement": 42.28, "extinction": False},
    {"puissance": 30, "abonnement": 44.46, "extinction": False},
    {"puissance": 31, "abonnement": 45.78, "extinction": False},
    {"puissance": 32, "abonnement": 47.10, "extinction": False},
    {"puissance": 33, "abonnement": 48.43, "extinction": False},
    {"puissance": 34, "abonnement": 49.75, "extinction": False},
    {"puissance": 35, "abonnement": 51.07, "extinction": False},
    {"puissance": 36, "abonnement": 52.39, "extinction": False},
]

# Champs mémorisés dans le cache tarifs pour chaque heure
_CHAMPS_CACHE = ("spot_price", "spot_price_eur_kwh", "price_ht_eur_kwh", "price_ttc_eur_kwh")


def _saison(mois):
    """Retourne 'ete' pour avril-octobre, 'hiver' sinon."""
    return "ete" if 4 <= mois <= 10 else "hiver"


def _extraire_champs(entry):
    """Extrait les 4 champs de tarification d'une entrée API."""
    spot_eur = entry.get("spot_price_eur_kwh")
    if spot_eur is None:
        sp = entry.get("spot_price")
        spot_eur = sp / 1000.0 if sp is not None else None
    return {
        "spot_price":         entry.get("spot_price"),
        "spot_price_eur_kwh": spot_eur,
        "price_ht_eur_kwh":   entry.get("price_ht_eur_kwh"),
        "price_ttc_eur_kwh":  entry.get("price_ttc_eur_kwh"),
    }


def _tarif_periode(date_debut, date_fin, adresse, granularity, turpe_type):
    """
    Appelle l'API Sobry pour une plage de dates et un type TURPE.
    Retourne (dict {date_str: {str(heure): {4 champs}}}, None) ou (None, erreur).
    Un seul appel couvre l'ensemble de la plage → au plus 1 appel par TURPE par UPDATE.
    """
    url = "{}?start={}&end={}&granularity={}&turpe={}".format(
        adresse, date_debut, date_fin, granularity, turpe_type
    )
    try:
        req = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("success", False):
            return None, "API a retourné success=false"
        entries = data.get("data", [])
        if not entries:
            return None, "Aucun tarif dans la réponse"

        resultat = {}

        if granularity == "daily":
            # Une entrée par jour → même valeur pour les 24 heures
            for entry in entries:
                ts = datetime.fromisoformat(entry["timestamp"]).astimezone(TZ_PARIS)
                date_str = ts.date().isoformat()
                vals = _extraire_champs(entry)
                resultat[date_str] = {str(h): vals for h in range(24)}
            return resultat, None

        # hourly ou quarter_hourly : agréger par date/heure (moyenne)
        sommes  = {}
        comptes = {}
        for entry in entries:
            ts       = datetime.fromisoformat(entry["timestamp"]).astimezone(TZ_PARIS)
            date_str = ts.date().isoformat()
            h        = str(ts.hour)
            if date_str not in sommes:
                sommes[date_str]  = {}
                comptes[date_str] = {}
            if h not in sommes[date_str]:
                sommes[date_str][h]  = {k: 0.0 for k in _CHAMPS_CACHE}
                comptes[date_str][h] = 0
            champs = _extraire_champs(entry)
            for k in _CHAMPS_CACHE:
                v = champs.get(k)
                sommes[date_str][h][k] += v if v is not None else 0.0
            comptes[date_str][h] += 1

        if not sommes:
            return None, "Aucun tarif horaire dans la réponse"

        for date_str in sommes:
            resultat[date_str] = {}
            for h, s in sommes[date_str].items():
                n = comptes[date_str][h]
                resultat[date_str][h] = {k: round(s[k] / n, 6) for k in _CHAMPS_CACHE}

        return resultat, None

    except urllib.error.HTTPError as e:
        return None, "HTTP {} pour {}".format(e.code, url)
    except urllib.error.URLError as e:
        return None, "Connexion impossible : {}".format(e.reason)
    except Exception as e:
        return None, str(e)


class ScriptSobry(BaseTarif):
    NOM = "sobry"
    PARAM_DEFAUT = {
        "adresse":             "https://api.sobry.co/api/prices/raw",
        "commentaire":         "Script Sobry — tarif dynamique EPEX Spot",
        "puissance_souscrite": 6,
        "granularity":         "hourly",
        "display":             "TTC",
        "turpe_actifs":        ["CU"],
        "marge_ht":            0.0080,
        "marge_ttc":           0.0096,
        "socap": {
            "plafond_ete_ht":    0.1125,
            "plafond_ete_ttc":   0.1350,
            "plafond_hiver_ht":  0.2167,
            "plafond_hiver_ttc": 0.2600,
            "prime_ht":          0.0070,
            "prime_ttc":         0.0084,
        },
        "soflex": {
            "plafond_ete_ht":    0.3333,
            "plafond_ete_ttc":   0.4000,
            "plafond_hiver_ht":  0.3333,
            "plafond_hiver_ttc": 0.4000,
            "prime_ht":          0.0020,
            "prime_ttc":         0.0024,
        },
        "tranches": TRANCHES_DEFAUT,
    }
    MOCK_DATA = {"tarif": "sobry", "source": "mock"}

    def _lire_cache(self):
        chemin = BASE_DIR / "sobry_cache.json"
        if not chemin.exists():
            return {"tarifs": {}, "resultats": {}}
        try:
            return json.loads(chemin.read_text(encoding="utf-8"))
        except Exception:
            return {"tarifs": {}, "resultats": {}}

    def _ecrire_cache(self, cache):
        chemin = BASE_DIR / "sobry_cache.json"
        chemin.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def cmd_update(self, params, global_param):
        """Calcule les prix Sobry par heure : bruts (cache) et produits SoCap/SoFlex."""
        if not global_param.get("api_conso_statut", False):
            return _reponse(False, "UPDATE", error="API conso non connectée")

        param = self._lire_param()
        if param is None:
            return _reponse(False, "UPDATE", error="Fichier param absent")

        # Tranche active
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

        adresse      = param.get("adresse",     self.PARAM_DEFAUT["adresse"])
        granularity  = param.get("granularity", self.PARAM_DEFAUT["granularity"])
        display      = param.get("display",     self.PARAM_DEFAUT["display"])
        marge_ht     = float(param.get("marge_ht",  self.PARAM_DEFAUT["marge_ht"]))
        marge_ttc    = float(param.get("marge_ttc", self.PARAM_DEFAUT["marge_ttc"]))
        turpe_actifs = [t for t in param.get("turpe_actifs", self.PARAM_DEFAUT["turpe_actifs"])
                        if t in _TURPE_CEKWH]

        # Paramètres produits SoCap / SoFlex
        produits_cfg = {
            p: param.get(p, self.PARAM_DEFAUT.get(p, {}))
            for p in _PRODUITS
        }

        # Chargement du cache
        cache           = self._lire_cache()
        cache_tarifs    = cache.get("tarifs", {})
        cache_resultats = cache.get("resultats", {})
        cache_modifie   = False

        # Migration : détecter l'ancien format (clés de tarifs = dates, pas TURPE)
        if cache_tarifs and not any(k in _TURPE_CEKWH for k in cache_tarifs):
            cache_tarifs    = {}
            cache_resultats = {}
            cache_modifie   = True

        # Agrégation des records en buckets horaires
        kwh_par_heure = {}
        for r in records:
            dt  = datetime.fromisoformat(r["ts"]).astimezone(TZ_PARIS)
            cle = dt.replace(minute=0, second=0, microsecond=0)
            kwh_par_heure[cle] = kwh_par_heure.get(cle, 0.0) + r["kwh"]

        sorted_heures = sorted(kwh_par_heure)

        # ── Phase 1 : mise à jour des caches bruts par TURPE (≤ 1 appel API par TURPE) ──

        for turpe_type in turpe_actifs:
            tarifs_turpe    = cache_tarifs.setdefault(turpe_type, {})
            resultats_turpe = cache_resultats.setdefault(turpe_type, {})

            # Heures dont le résultat est absent ou dont le kwh a changé
            heures_a_calculer = []
            for heure_dt in sorted_heures:
                kwh      = round(kwh_par_heure[heure_dt], 3)
                heure_ts = heure_dt.isoformat()
                cached   = resultats_turpe.get(heure_ts)
                if not cached or cached.get("kwh") != kwh:
                    heures_a_calculer.append(heure_dt)

            if not heures_a_calculer:
                continue

            # Dates dont les tarifs sont absents du cache pour ce TURPE
            dates_manquantes = set()
            for heure_dt in heures_a_calculer:
                date_str = heure_dt.date().isoformat()
                heure_h  = str(heure_dt.hour)
                if date_str not in tarifs_turpe or heure_h not in tarifs_turpe[date_str]:
                    dates_manquantes.add(heure_dt.date())

            if dates_manquantes:
                date_min = min(dates_manquantes)
                date_max = max(dates_manquantes)
                date_fin = (date_max + timedelta(days=1)).isoformat()

                nouveaux_tarifs, erreur = _tarif_periode(
                    date_min.isoformat(), date_fin, adresse, granularity, turpe_type
                )
                if erreur:
                    return _reponse(
                        False, "UPDATE",
                        error="Erreur API Sobry ({}, {}) : {}".format(turpe_type, date_min, erreur),
                    )
                for date_str, heures_dict in nouveaux_tarifs.items():
                    tarifs_turpe[date_str] = heures_dict
                cache_modifie = True

            # Calcul des résultats bruts manquants depuis le cache tarifs
            for heure_dt in heures_a_calculer:
                kwh      = round(kwh_par_heure[heure_dt], 3)
                heure_ts = heure_dt.isoformat()
                date_str = heure_dt.date().isoformat()
                heure_h  = str(heure_dt.hour)

                tarif_h = tarifs_turpe.get(date_str, {}).get(heure_h)
                if tarif_h is None:
                    return _reponse(
                        False, "UPDATE",
                        error="Tarif manquant pour {}h le {} (TURPE {})".format(
                            heure_h, date_str, turpe_type),
                    )

                ht_eur  = tarif_h.get("price_ht_eur_kwh")
                ttc_eur = tarif_h.get("price_ttc_eur_kwh")
                if ht_eur is None or ttc_eur is None:
                    return _reponse(
                        False, "UPDATE",
                        error="Champs prix absents de l'API pour {}h le {} (TURPE {})".format(
                            heure_h, date_str, turpe_type),
                    )

                spot_eur = tarif_h.get("spot_price_eur_kwh")
                resultats_turpe[heure_ts] = {
                    "kwh":  kwh,
                    "spot": "{:.4f}".format(spot_eur * 100.0) if spot_eur is not None else None,
                }
                cache_modifie = True

        # ── Phase 2 : récupérer le spot si turpe_actifs est vide ──

        if not turpe_actifs:
            dates_sans_spot = set()
            for heure_dt in sorted_heures:
                date_str = heure_dt.date().isoformat()
                heure_h  = str(heure_dt.hour)
                trouve   = any(
                    cache_tarifs.get(t, {}).get(date_str, {}).get(heure_h, {}).get(
                        "spot_price_eur_kwh") is not None
                    for t in _TURPE_TYPES
                )
                if not trouve:
                    dates_sans_spot.add(heure_dt.date())

            if dates_sans_spot:
                date_min = min(dates_sans_spot)
                date_max = max(dates_sans_spot)
                date_fin = (date_max + timedelta(days=1)).isoformat()
                tarifs_cu, erreur = _tarif_periode(
                    date_min.isoformat(), date_fin, adresse, granularity, "CU"
                )
                if not erreur:
                    cu = cache_tarifs.setdefault("CU", {})
                    for date_str, heures_dict in tarifs_cu.items():
                        cu[date_str] = heures_dict
                    cache_modifie = True

        # ── Phase 1b : calcul SoCap / SoFlex (mensuel, cap plafond) ──

        # resultats_produits[produit][turpe_type][heure_ts] = {kwh, prix_kwh, prix_eur}
        resultats_produits = {}

        for produit in _PRODUITS:
            p_cfg     = produits_cfg[produit]
            prime_ht  = float(p_cfg.get("prime_ht",  0.0))
            prime_ttc = float(p_cfg.get("prime_ttc", 0.0))
            prod_res  = {}

            for turpe_type in turpe_actifs:
                tarifs_turpe = cache_tarifs.get(turpe_type, {})

                # Grouper les heures disponibles par (année, mois)
                heures_par_mois = {}
                for heure_dt in sorted_heures:
                    date_str = heure_dt.date().isoformat()
                    heure_h  = str(heure_dt.hour)
                    tarif_h  = tarifs_turpe.get(date_str, {}).get(heure_h)
                    if tarif_h is None:
                        continue
                    cle_mois = (heure_dt.year, heure_dt.month)
                    if cle_mois not in heures_par_mois:
                        heures_par_mois[cle_mois] = []
                    heures_par_mois[cle_mois].append((heure_dt, tarif_h))

                turpe_res = {}
                for (annee, mois), heures_mois in heures_par_mois.items():
                    saison = _saison(mois)

                    if display == "TTC":
                        plafond = float(p_cfg.get("plafond_{}_ttc".format(saison), 999.0))
                        vals    = [h[1].get("price_ttc_eur_kwh") or 0.0 for h in heures_mois]
                        avg     = sum(vals) / len(vals) if vals else 0.0
                        # Si la moyenne mensuelle dépasse le plafond, tous les kWh du mois
                        # sont facturés au plafond (prix uniforme pour le mois)
                        base_mois = plafond if avg > plafond else None

                        for heure_dt, tarif_h in heures_mois:
                            heure_ts = heure_dt.isoformat()
                            kwh      = round(kwh_par_heure[heure_dt], 3)
                            base     = base_mois if base_mois is not None \
                                       else (tarif_h.get("price_ttc_eur_kwh") or 0.0)
                            prix     = (base + marge_ttc + prime_ttc) * 100.0
                            turpe_res[heure_ts] = {
                                "kwh":      kwh,
                                "prix_kwh": "{:.4f}".format(prix),
                                "prix_eur": "{:.4f}".format(kwh * prix / 100.0),
                            }

                    else:  # HT
                        plafond = float(p_cfg.get("plafond_{}_ht".format(saison), 999.0))
                        vals    = [h[1].get("price_ht_eur_kwh") or 0.0 for h in heures_mois]
                        avg     = sum(vals) / len(vals) if vals else 0.0
                        base_mois = plafond if avg > plafond else None

                        for heure_dt, tarif_h in heures_mois:
                            heure_ts = heure_dt.isoformat()
                            kwh      = round(kwh_par_heure[heure_dt], 3)
                            base     = base_mois if base_mois is not None \
                                       else (tarif_h.get("price_ht_eur_kwh") or 0.0)
                            prix     = (base + marge_ht + prime_ht) * 100.0
                            turpe_res[heure_ts] = {
                                "kwh":      kwh,
                                "prix_kwh": "{:.4f}".format(prix),
                                "prix_eur": "{:.4f}".format(kwh * prix / 100.0),
                            }

                prod_res[turpe_type] = turpe_res

            resultats_produits[produit] = prod_res

        # ── Phase 3 : construction de la liste résultats ──

        resultats = []
        for heure_dt in sorted_heures:
            kwh      = round(kwh_par_heure[heure_dt], 3)
            heure_ts = heure_dt.isoformat()
            date_str = heure_dt.date().isoformat()
            heure_h  = str(heure_dt.hour)
            entree   = {"ts": heure_ts, "kwh": kwh}

            # Colonnes SoCap / SoFlex (remplacent les colonnes TURPE brutes)
            for produit in _PRODUITS:
                for turpe_type in turpe_actifs:
                    cached = resultats_produits.get(produit, {}).get(turpe_type, {}).get(heure_ts)
                    if cached:
                        entree["sobry.{}.{}.prix_kwh".format(produit, turpe_type)] = cached["prix_kwh"]
                        entree["sobry.{}.{}.prix_eur".format(produit, turpe_type)] = cached["prix_eur"]

            # Prix spot (depuis cache_resultats bruts ou cache_tarifs)
            spot_str = None
            for turpe_type in turpe_actifs:
                cached = cache_resultats.get(turpe_type, {}).get(heure_ts)
                if cached and cached.get("spot"):
                    spot_str = cached["spot"]
                    break
            if spot_str is None:
                for t in _TURPE_TYPES:
                    tarif_h = cache_tarifs.get(t, {}).get(date_str, {}).get(heure_h)
                    if tarif_h and tarif_h.get("spot_price_eur_kwh") is not None:
                        spot_str = "{:.4f}".format(tarif_h["spot_price_eur_kwh"] * 100.0)
                        break

            if spot_str is not None:
                entree["sobry.prix_spot"] = spot_str

            resultats.append(entree)

        if cache_modifie:
            self._ecrire_cache({"tarifs": cache_tarifs, "resultats": cache_resultats})

        data = {
            "tarif":              "sobry",
            "puissance":          tranche["puissance"],
            "abonnement_mensuel": tranche["abonnement"],
            "turpe_actifs":       turpe_actifs,
            "produits":           _PRODUITS,
            "heures":             resultats,
        }
        self._ecrire_data(data)
        logger.info("sobry UPDATE : %d heures, TURPE=%s, produits=%s, display=%s",
                    len(resultats), turpe_actifs, _PRODUITS, display)
        return _reponse(True, "UPDATE", data=data)


_instance = ScriptSobry(BASE_DIR)


def run(mode: str, params: dict = None, global_param: dict = None) -> dict:
    """Point d'entrée du script."""
    return _instance.run(mode=mode, params=params, global_param=global_param)


if __name__ == "__main__":
    import sys as _sys
    mode_cli = _sys.argv[1] if len(_sys.argv) > 1 else "GET"
    print(json.dumps(run(mode_cli), indent=2, ensure_ascii=False))
