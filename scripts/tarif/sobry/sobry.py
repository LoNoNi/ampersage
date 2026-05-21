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

# Grilles officielles Sobry (source : sobry.co/grille-tarifaire)
# Abonnement total HT = Acheminement TURPE + Abo Sobry
TRANCHES_CU4 = [
    {"puissance":  3, "abonnement":  9.76, "extinction": False},
    {"puissance":  4, "abonnement": 12.26, "extinction": False},
    {"puissance":  5, "abonnement": 14.37, "extinction": False},
    {"puissance":  6, "abonnement": 16.29, "extinction": False},
    {"puissance":  7, "abonnement": 18.08, "extinction": False},
    {"puissance":  8, "abonnement": 19.77, "extinction": False},
    {"puissance":  9, "abonnement": 21.40, "extinction": False},
    {"puissance": 10, "abonnement": 22.97, "extinction": False},
    {"puissance": 11, "abonnement": 24.50, "extinction": False},
    {"puissance": 12, "abonnement": 25.99, "extinction": False},
    {"puissance": 13, "abonnement": 27.45, "extinction": False},
    {"puissance": 14, "abonnement": 28.88, "extinction": False},
    {"puissance": 15, "abonnement": 30.29, "extinction": False},
    {"puissance": 16, "abonnement": 31.68, "extinction": False},
    {"puissance": 17, "abonnement": 33.05, "extinction": False},
    {"puissance": 18, "abonnement": 34.40, "extinction": False},
    {"puissance": 19, "abonnement": 35.73, "extinction": False},
    {"puissance": 20, "abonnement": 37.05, "extinction": False},
    {"puissance": 21, "abonnement": 38.36, "extinction": False},
    {"puissance": 22, "abonnement": 39.66, "extinction": False},
    {"puissance": 23, "abonnement": 40.94, "extinction": False},
    {"puissance": 24, "abonnement": 42.21, "extinction": False},
    {"puissance": 25, "abonnement": 43.48, "extinction": False},
    {"puissance": 26, "abonnement": 44.73, "extinction": False},
    {"puissance": 27, "abonnement": 45.98, "extinction": False},
    {"puissance": 28, "abonnement": 47.22, "extinction": False},
    {"puissance": 29, "abonnement": 48.45, "extinction": False},
    {"puissance": 30, "abonnement": 49.67, "extinction": False},
    {"puissance": 31, "abonnement": 50.89, "extinction": False},
    {"puissance": 32, "abonnement": 52.10, "extinction": False},
    {"puissance": 33, "abonnement": 53.31, "extinction": False},
    {"puissance": 34, "abonnement": 54.51, "extinction": False},
    {"puissance": 35, "abonnement": 55.70, "extinction": False},
    {"puissance": 36, "abonnement": 56.89, "extinction": False},
]

TRANCHES_MU4 = [
    {"puissance":  3, "abonnement": 10.26, "extinction": False},
    {"puissance":  4, "abonnement": 12.93, "extinction": False},
    {"puissance":  5, "abonnement": 15.21, "extinction": False},
    {"puissance":  6, "abonnement": 17.29, "extinction": False},
    {"puissance":  7, "abonnement": 19.25, "extinction": False},
    {"puissance":  8, "abonnement": 21.11, "extinction": False},
    {"puissance":  9, "abonnement": 22.91, "extinction": False},
    {"puissance": 10, "abonnement": 24.65, "extinction": False},
    {"puissance": 11, "abonnement": 26.34, "extinction": False},
    {"puissance": 12, "abonnement": 28.00, "extinction": False},
    {"puissance": 13, "abonnement": 29.63, "extinction": False},
    {"puissance": 14, "abonnement": 31.23, "extinction": False},
    {"puissance": 15, "abonnement": 32.81, "extinction": False},
    {"puissance": 16, "abonnement": 34.36, "extinction": False},
    {"puissance": 17, "abonnement": 35.90, "extinction": False},
    {"puissance": 18, "abonnement": 37.41, "extinction": False},
    {"puissance": 19, "abonnement": 38.92, "extinction": False},
    {"puissance": 20, "abonnement": 40.40, "extinction": False},
    {"puissance": 21, "abonnement": 41.88, "extinction": False},
    {"puissance": 22, "abonnement": 43.34, "extinction": False},
    {"puissance": 23, "abonnement": 44.79, "extinction": False},
    {"puissance": 24, "abonnement": 46.23, "extinction": False},
    {"puissance": 25, "abonnement": 47.67, "extinction": False},
    {"puissance": 26, "abonnement": 49.09, "extinction": False},
    {"puissance": 27, "abonnement": 50.50, "extinction": False},
    {"puissance": 28, "abonnement": 51.91, "extinction": False},
    {"puissance": 29, "abonnement": 53.31, "extinction": False},
    {"puissance": 30, "abonnement": 54.70, "extinction": False},
    {"puissance": 31, "abonnement": 56.08, "extinction": False},
    {"puissance": 32, "abonnement": 57.46, "extinction": False},
    {"puissance": 33, "abonnement": 58.83, "extinction": False},
    {"puissance": 34, "abonnement": 60.20, "extinction": False},
    {"puissance": 35, "abonnement": 61.56, "extinction": False},
    {"puissance": 36, "abonnement": 62.92, "extinction": False},
]

# Champs mémorisés dans le cache tarifs pour chaque heure
_CHAMPS_CACHE = ("spot_price", "spot_price_eur_kwh", "price_ht_eur_kwh", "price_ttc_eur_kwh")


def _get_tranches(param, turpe_type):
    """Retourne la grille d'abonnements pour le type TURPE donné."""
    if turpe_type == "MU4":
        return param.get("tranches_mu4", TRANCHES_MU4)
    return param.get("tranches_cu4", TRANCHES_CU4)


def _deduire_tranche(pmax_kw, tranches):
    # type: (float, list) -> Optional[dict]
    """Retourne la tranche active avec la plus petite puissance >= pmax_kw."""
    actives = sorted(
        (t for t in tranches if not t.get("extinction", False)),
        key=lambda t: t["puissance"],
    )
    for t in actives:
        if t["puissance"] >= pmax_kw:
            return t
    return actives[-1] if actives else None


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
        "adresse":      "https://api.sobry.co/api/prices/raw",
        "commentaire":  "Script Sobry — tarif dynamique EPEX Spot",
        "granularity":  "hourly",
        "display":      "TTC",
        "turpe_actifs": ["CU4"],
        "marge_ht":     0.0080,
        "marge_ttc":    0.0096,
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
        "tranches_cu4": TRANCHES_CU4,
        "tranches_mu4": TRANCHES_MU4,
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

        turpe_principal = turpe_actifs[0] if turpe_actifs else "CU4"
        pmax_periode = max((r.get("pmax", 0) for r in records), default=0)
        tranches     = _get_tranches(param, turpe_principal)
        tranche      = _deduire_tranche(pmax_periode, tranches)
        if tranche is None:
            return _reponse(False, "UPDATE", error="Aucune tranche active trouvée")

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


    def cmd_get_masks(self, params, global_param):
        """Retourne les templates HTML pour l'affichage custom (params, detail_bandeau, detail_ligne)."""
        def _lire_tpl(nom):
            p = BASE_DIR / nom
            return p.read_text(encoding="utf-8") if p.exists() else ""

        param    = self._lire_param() or self.PARAM_DEFAUT
        display  = param.get("display", "TTC")
        marge    = param.get("marge_ttc" if display == "TTC" else "marge_ht",
                             self.PARAM_DEFAUT["marge_ttc"])

        turpe_actif = (param.get("turpe_actifs") or ["CU4"])[0]
        if turpe_actif not in ("CU4", "MU4"):
            turpe_actif = "CU4"

        # Déduction de la puissance active depuis le pmax des records (pas de param utilisateur)
        records      = (global_param or {}).get("DATA", {}).get("api_conso", {}).get("records", [])
        pmax_periode = max((r.get("pmax", 0) for r in records), default=0)

        produit = params.get("produit", "socap")
        p_cfg   = param.get(produit, self.PARAM_DEFAUT.get(produit, {}))

        def _render_lignes(turpe_key):
            tr_list  = _get_tranches(param, turpe_key)
            tr_actif = _deduire_tranche(pmax_periode, tr_list)
            pact     = tr_actif["puissance"] if tr_actif else None
            rows = []
            for t in tr_list:
                if t.get("extinction", False):
                    continue
                kva     = t["puissance"]
                abo_ht  = t["abonnement"]
                abo_ttc = round(abo_ht * 1.055, 2)
                cls     = " class=\"gen-pi-active\"" if kva == pact else ""
                rows.append(
                    "<tr{}>"
                    "<td class=\"gen-pi-td\">{} kVA</td>"
                    "<td class=\"gen-pi-td\">{:.2f} \u20ac/mois</td>"
                    "<td class=\"gen-pi-td\">{:.2f} \u20ac/mois</td>"
                    "</tr>".format(cls, kva, abo_ht, abo_ttc)
                )
            return "\n          ".join(rows)

        params_html = (
            _lire_tpl("sobry_params.html")
            .replace("{checked_cu4}",       "checked" if turpe_actif == "CU4" else "")
            .replace("{checked_mu4}",       "checked" if turpe_actif == "MU4" else "")
            .replace("{style_cu4}",         "" if turpe_actif == "CU4" else "display:none")
            .replace("{style_mu4}",         "" if turpe_actif == "MU4" else "display:none")
            .replace("{display}",           display)
            .replace("{marge}",             str(marge))
            .replace("{lignes_tranches_cu4}", _render_lignes("CU4"))
            .replace("{lignes_tranches_mu4}", _render_lignes("MU4"))
            .replace("{produit_label}",     produit.capitalize())
            .replace("{plafond_ete_ht}",    str(p_cfg.get("plafond_ete_ht",  "")))
            .replace("{plafond_ete_ttc}",   str(p_cfg.get("plafond_ete_ttc", "")))
            .replace("{plafond_hiver_ht}",  str(p_cfg.get("plafond_hiver_ht",  "")))
            .replace("{plafond_hiver_ttc}", str(p_cfg.get("plafond_hiver_ttc", "")))
            .replace("{prime_ht}",          str(p_cfg.get("prime_ht",  "")))
            .replace("{prime_ttc}",         str(p_cfg.get("prime_ttc", "")))
        )

        return _reponse(True, "GET_MASKS", data={
            "params":         params_html,
            "detail_bandeau": _lire_tpl("sobry_detail_bandeau.html"),
            "detail_ligne":   _lire_tpl("sobry_detail_ligne.html"),
            "save_groupe":    "sobry",
        })

    def cmd_get_custom(self, params, global_param):
        """
        Calcule les créneaux 30 min pour le tarif Sobry.
        Retourne {creneaux: [{ts, kwh, prix_kwh, prix_spot}], puissance, abonnement_mensuel}.
          prix_kwh  : prix effectif SoCap en €/kWh (pour calcul du coût)
          prix_spot : spot brut en c€/kWh (string, pour affichage)
        """
        if not global_param.get("api_conso_statut", False):
            return _reponse(False, "GET_CUSTOM", error="API conso non connectée")

        param = self._lire_param()
        if param is None:
            return _reponse(False, "GET_CUSTOM", error="Fichier param absent — lancez INIT_PARAM")

        records = global_param.get("DATA", {}).get("api_conso", {}).get("records", [])
        if not records:
            return _reponse(False, "GET_CUSTOM", error="Aucune donnée de consommation disponible")

        pmax_periode = max((r.get("pmax", 0) for r in records), default=0)

        adresse     = param.get("adresse",     self.PARAM_DEFAUT["adresse"])
        granularity = param.get("granularity", self.PARAM_DEFAUT["granularity"])
        display     = param.get("display",     "TTC")
        marge_ht    = float(param.get("marge_ht",  self.PARAM_DEFAUT["marge_ht"]))
        marge_ttc   = float(param.get("marge_ttc", self.PARAM_DEFAUT["marge_ttc"]))
        turpe_actifs = [t for t in param.get("turpe_actifs", self.PARAM_DEFAUT["turpe_actifs"])
                        if t in _TURPE_CEKWH]
        if not turpe_actifs:
            turpe_actifs = ["CU4"]
        turpe_type = turpe_actifs[0]

        tranches  = _get_tranches(param, turpe_type)
        tranche   = _deduire_tranche(pmax_periode, tranches)
        if tranche is None:
            return _reponse(False, "GET_CUSTOM", error="Aucune tranche active trouvée")

        marge   = marge_ttc if display == "TTC" else marge_ht
        prix_key = "price_ttc_eur_kwh" if display == "TTC" else "price_ht_eur_kwh"

        # Produit demandé (passé par generique/server via offre_config)
        produit = params.get("produit", "socap")
        if produit not in _PRODUITS:
            produit = "socap"
        p_cfg   = param.get(produit, self.PARAM_DEFAUT.get(produit, {}))
        prime   = float(p_cfg.get("prime_ttc" if display == "TTC" else "prime_ht", 0.0))

        # ── Agrégation horaire (nécessaire pour le calcul du plafond mensuel) ──
        kwh_par_heure = {}
        for r in records:
            dt  = datetime.fromisoformat(r["ts"]).astimezone(TZ_PARIS)
            cle = dt.replace(minute=0, second=0, microsecond=0)
            kwh_par_heure[cle] = kwh_par_heure.get(cle, 0.0) + r["kwh"]

        sorted_heures = sorted(kwh_par_heure)
        if not sorted_heures:
            return _reponse(False, "GET_CUSTOM", error="Aucun créneau horaire calculable")

        # ── Récupération / mise à jour du cache EPEX ──
        cache         = self._lire_cache()
        cache_tarifs  = cache.get("tarifs", {})
        cache_modifie = False

        # Migration format ancien cache
        if cache_tarifs and not any(k in _TURPE_CEKWH for k in cache_tarifs):
            cache_tarifs  = {}
            cache_modifie = True

        tarifs_turpe = cache_tarifs.setdefault(turpe_type, {})

        dates_manquantes = set()
        for heure_dt in sorted_heures:
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
                return _reponse(False, "GET_CUSTOM",
                                error="Erreur API Sobry ({}) : {}".format(turpe_type, erreur))
            for date_str, heures_dict in nouveaux_tarifs.items():
                tarifs_turpe[date_str] = heures_dict
            cache_modifie = True

        if cache_modifie:
            cache["tarifs"] = cache_tarifs
            self._ecrire_cache(cache)

        # ── Plafond mensuel SoCap ──
        heures_par_mois = {}
        for heure_dt in sorted_heures:
            date_str = heure_dt.date().isoformat()
            heure_h  = str(heure_dt.hour)
            tarif_h  = tarifs_turpe.get(date_str, {}).get(heure_h)
            if tarif_h is None:
                continue
            cle_mois = (heure_dt.year, heure_dt.month)
            heures_par_mois.setdefault(cle_mois, []).append(tarif_h)

        plafond_par_mois = {}
        for cle_mois, tarifs_mois in heures_par_mois.items():
            saison   = _saison(cle_mois[1])
            plafond_key = "plafond_{}_{}".format(saison, "ttc" if display == "TTC" else "ht")
            plafond  = float(p_cfg.get(plafond_key, 999.0))
            vals     = [t.get(prix_key) or 0.0 for t in tarifs_mois]
            avg      = sum(vals) / len(vals) if vals else 0.0
            plafond_par_mois[cle_mois] = plafond if avg > plafond else None

        # ── Créneaux 30 min ──
        creneaux = []
        for r in records:
            dt       = datetime.fromisoformat(r["ts"]).astimezone(TZ_PARIS)
            heure_dt = dt.replace(minute=0, second=0, microsecond=0)
            date_str = heure_dt.date().isoformat()
            heure_h  = str(heure_dt.hour)

            tarif_h = tarifs_turpe.get(date_str, {}).get(heure_h)
            if tarif_h is None:
                continue

            cle_mois   = (heure_dt.year, heure_dt.month)
            base_mois  = plafond_par_mois.get(cle_mois)
            base       = base_mois if base_mois is not None else (tarif_h.get(prix_key) or 0.0)
            prix_kwh   = round(base + marge + prime, 6)  # €/kWh

            spot_eur   = tarif_h.get("spot_price_eur_kwh")
            entree     = {
                "ts":       dt.isoformat(),
                "kwh":      round(r["kwh"], 3),
                "prix_kwh": prix_kwh,
                # Composantes pour popup de détail (c€/kWh)
                "_base":  round(base  * 100.0, 4),  # base TTC effective (plafond ou marché)
                "_marge": round(marge * 100.0, 4),
                "_prime": round(prime * 100.0, 4),
                "_cap":   1 if base_mois is not None else 0,
            }
            if spot_eur is not None:
                entree["_spot"] = round(spot_eur * 100.0, 4)
            creneaux.append(entree)

        logger.info("sobry GET_CUSTOM : %d créneaux 30 min (TURPE=%s, produit=%s, display=%s)",
                    len(creneaux), turpe_type, produit, display)

        # Barème complet des tranches actives (format {kva, ht, ttc}) pour la résolution
        # côté client (puissance conseillée, personnalisée, avertissements de palier)
        abonnements = [
            {
                "kva": t["puissance"],
                "ht":  t["abonnement"],
                "ttc": round(t["abonnement"] * 1.055, 2),
            }
            for t in tranches
            if not t.get("extinction", False)
        ]

        return _reponse(True, "GET_CUSTOM", data={
            "creneaux":               creneaux,
            "puissance":              tranche["puissance"],
            "abonnement_mensuel_ht":  tranche["abonnement"],
            "abonnement_mensuel_ttc": round(tranche["abonnement"] * 1.055, 2),
            "abonnements":            abonnements,
            "turpe":                  turpe_type,
            "produit":                produit,
        })

    def run(self, mode, params=None, global_param=None):
        """Point d'entrée — étend BaseTarif avec GET_MASKS et GET_CUSTOM."""
        params       = params or {}
        global_param = global_param if global_param is not None else {}
        extra = {
            "GET_MASKS":  self.cmd_get_masks,
            "GET_CUSTOM": self.cmd_get_custom,
        }
        if mode in extra:
            return extra[mode](params, global_param)
        return super().run(mode=mode, params=params, global_param=global_param)


_instance = ScriptSobry(BASE_DIR)


def run(mode: str, params: dict = None, global_param: dict = None) -> dict:
    """Point d'entrée du script."""
    return _instance.run(mode=mode, params=params, global_param=global_param)


if __name__ == "__main__":
    import sys as _sys
    mode_cli = _sys.argv[1] if len(_sys.argv) > 1 else "GET"
    print(json.dumps(run(mode_cli), indent=2, ensure_ascii=False))
