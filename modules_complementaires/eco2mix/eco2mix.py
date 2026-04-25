"""
eco2mix.py — Module complémentaire éCO2mix.
Récupère les données de mix électrique et CO2 depuis l'API RTE (ODRE).

CO2 moyen  : taux_co2_rte fourni directement par RTE, stocké tel quel.
CO2 marginal : calculé par AmperSage via FACTEURS_EMISSION (parametres.py).
"""

import importlib
import importlib.util
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("eco2mix")
TZ_PARIS = ZoneInfo("Europe/Paris")
BASE_DIR = Path(__file__).parent

# ─── Chargement dynamique de parametres.py ────────────────────────────────────

import sys as _sys

_param_spec = importlib.util.spec_from_file_location(
    "eco2mix_parametres", BASE_DIR / "parametres.py"
)
_parametres = importlib.util.module_from_spec(_param_spec)
# Enregistrement obligatoire dans sys.modules avant exec_module,
# sans quoi importlib.reload() échoue avec "module not in sys.modules".
_sys.modules["eco2mix_parametres"] = _parametres
_param_spec.loader.exec_module(_parametres)

# ─── État module ──────────────────────────────────────────────────────────────

_lock = threading.RLock()
_dernier_fetch_api: Optional[str] = None


# ─── Helpers réponse ──────────────────────────────────────────────────────────


def _reponse(status: bool, mode: str, data=None, error=None) -> dict:
    """Construit la réponse standardisée."""
    return {"status": status, "mode": mode, "error": error, "data": data or {}}


# ─── Persistance locale ───────────────────────────────────────────────────────


def _fichier_donnees() -> Path:
    return BASE_DIR / _parametres.FICHIER_DONNEES


def _donnees_vides() -> dict:
    return {
        "meta": {
            "module": "eco2mix",
            "version": "1.0",
            "source": "RTE éCO2mix",
            "premier_enregistrement": None,
            "dernier_enregistrement": None,
            "total_creneaux": 0,
        },
        "creneaux": {},
    }


def _charger_donnees() -> dict:
    """Lit le fichier JSON local. Retourne une structure vide si absent."""
    chemin = _fichier_donnees()
    if not chemin.exists():
        return _donnees_vides()
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def _sauvegarder_donnees(donnees: dict):
    """Écrit le fichier JSON local. Met à jour les métadonnées."""
    chemin = _fichier_donnees()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    creneaux = donnees.get("creneaux", {})
    if creneaux:
        dates = sorted(creneaux.keys())
        donnees["meta"]["premier_enregistrement"] = dates[0]
        donnees["meta"]["dernier_enregistrement"] = dates[-1]
        donnees["meta"]["total_creneaux"] = len(creneaux)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=2)


# ─── Gestion des créneaux manquants ──────────────────────────────────────────

TZ_UTC = ZoneInfo("UTC")


def _grouper_contigus(manquants: list) -> list:
    """
    Regroupe une liste de datetime UTC (pas 15 min) en sous-listes contiguës.
    Exemple : [t1, t2, t3, t7, t8] → [[t1,t2,t3], [t7,t8]]
    Permet de ne fetcher que les petites plages réellement manquantes.
    """
    if not manquants:
        return []
    groupes = []
    groupe_courant = [manquants[0]]
    for i in range(1, len(manquants)):
        if manquants[i] - manquants[i - 1] == timedelta(minutes=15):
            groupe_courant.append(manquants[i])
        else:
            groupes.append(groupe_courant)
            groupe_courant = [manquants[i]]
    groupes.append(groupe_courant)
    return groupes


def _creneaux_manquants(date_debut: datetime, date_fin: datetime) -> list:
    """
    Retourne la liste des créneaux 15-min absents de la base locale
    entre date_debut et date_fin inclus.
    Itération en UTC : aucun problème de changement d'heure.
    """
    donnees   = _charger_donnees()
    existants = donnees.get("creneaux", {})

    # Convertir en UTC et normaliser sur 15 min
    dt           = date_debut.astimezone(TZ_UTC)
    dt           = dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)
    date_fin_utc = date_fin.astimezone(TZ_UTC)

    # Délai avant de re-tenter un créneau temporairement indisponible (en secondes)
    delai_retry = _parametres.DELAI_RETRY_INDISPONIBLE_HEURES * 3600
    now_utc = datetime.now(TZ_UTC)

    manquants = []
    while dt <= date_fin_utc:
        cle = dt.isoformat()
        entree = existants.get(cle)
        if entree is None:
            manquants.append(dt)
        elif entree.get("indisponible_tmp"):
            # Re-tenter si le délai est écoulé
            depuis_str = entree.get("depuis", "")
            try:
                depuis = datetime.fromisoformat(depuis_str)
                if (now_utc - depuis).total_seconds() >= delai_retry:
                    manquants.append(dt)
            except Exception:
                manquants.append(dt)
        # indisponible permanent → ignoré (jamais re-tenté)
        dt = dt + timedelta(minutes=15)

    return manquants


# ─── Appel API RTE ────────────────────────────────────────────────────────────

# L'API temps réel (eco2mix-national-tr) ne conserve que quelques mois.
# Pour les données plus anciennes, on utilise le dataset consolidé (eco2mix-national-cons-def).
_SEUIL_HISTORIQUE_JOURS = 60


def _normaliser_record_tr(r: dict) -> Optional[dict]:
    """Normalise un enregistrement de l'API temps réel."""
    cle_brute = r.get("date_heure")
    if not cle_brute:
        return None

    def _mw(val):
        return val if isinstance(val, (int, float)) else 0

    try:
        cle = datetime.fromisoformat(cle_brute).astimezone(TZ_UTC).isoformat()
    except Exception:
        cle = cle_brute

    return cle, {
        "consommation": _mw(r.get("consommation")),
        "mix": {
            "nucleaire":        _mw(r.get("nucleaire")),
            "hydraulique":      _mw(r.get("hydraulique")),
            "gaz":              _mw(r.get("gaz")),
            "eolien_terrestre": _mw(r.get("eolien_terrestre")),
            "eolien_offshore":  _mw(r.get("eolien_offshore")),
            "solaire":          _mw(r.get("solaire")),
            "bioenergies":      _mw(r.get("bioenergies")),
            "fioul":            _mw(r.get("fioul")),
            "charbon":          _mw(r.get("charbon")),
            "pompage":          _mw(r.get("pompage")),
        },
        "echanges": {
            "angleterre":         _mw(r.get("ech_comm_angleterre")),
            "espagne":            _mw(r.get("ech_comm_espagne")),
            "italie":             _mw(r.get("ech_comm_italie")),
            "suisse":             _mw(r.get("ech_comm_suisse")),
            "allemagne_belgique": _mw(r.get("ech_comm_allemagne_belgique")),
        },
        "taux_co2_rte": r.get("taux_co2"),
        "fetch_timestamp": datetime.now(TZ_UTC).isoformat(),
    }


def _normaliser_record_historique(r: dict) -> Optional[dict]:
    """
    Normalise un enregistrement du dataset consolidé (eco2mix-national-cons-def).
    Différences : champ 'eolien' au lieu de 'eolien_terrestre'/'eolien_offshore'.
    """
    cle_brute = r.get("date_heure")
    if not cle_brute:
        return None

    def _mw(val):
        return val if isinstance(val, (int, float)) else 0

    try:
        cle = datetime.fromisoformat(cle_brute).astimezone(TZ_UTC).isoformat()
    except Exception:
        cle = cle_brute

    # Le dataset consolidé fusionne éolien terrestre + offshore dans 'eolien'.
    # Les deux ont le même facteur d'émission (11 gCO2/kWh) → on met tout en terrestre.
    eolien_total = _mw(r.get("eolien"))

    return cle, {
        "consommation": _mw(r.get("consommation")),
        "mix": {
            "nucleaire":        _mw(r.get("nucleaire")),
            "hydraulique":      _mw(r.get("hydraulique")),
            "gaz":              _mw(r.get("gaz")),
            "eolien_terrestre": eolien_total,
            "eolien_offshore":  0,
            "solaire":          _mw(r.get("solaire")),
            "bioenergies":      _mw(r.get("bioenergies")),
            "fioul":            _mw(r.get("fioul")),
            "charbon":          _mw(r.get("charbon")),
            "pompage":          _mw(r.get("pompage")),
        },
        "echanges": {
            "angleterre":         _mw(r.get("ech_comm_angleterre")),
            "espagne":            _mw(r.get("ech_comm_espagne")),
            "italie":             _mw(r.get("ech_comm_italie")),
            "suisse":             _mw(r.get("ech_comm_suisse")),
            "allemagne_belgique": _mw(r.get("ech_comm_allemagne_belgique")),
        },
        "taux_co2_rte": r.get("taux_co2"),
        "fetch_timestamp": datetime.now(TZ_PARIS).isoformat(),
    }


def _fetch_pagine(url_base: str, champs: str, filtre: str, normaliser_fn) -> dict:
    """Pagination générique sur une URL ODRE. Retourne un dict {cle_iso: creneau}."""
    global _dernier_fetch_api
    limite = _parametres.LIMITE_ENREGISTREMENTS
    nouveaux = {}
    offset = 0
    total_count = None

    while True:
        qs = urllib.parse.urlencode({
            "select":   champs,
            "where":    filtre,
            "order_by": "date_heure asc",
            "limit":    str(limite),
            "offset":   str(offset),
        })
        url = url_base + "?" + qs
        logger.info("éCO2mix fetch offset=%d : %s", offset, url[:120])

        req = urllib.request.Request(url, headers={"User-Agent": "Ampersage/1.0"})
        with urllib.request.urlopen(req, timeout=_parametres.TIMEOUT_SECONDES) as resp:
            corps = json.loads(resp.read().decode("utf-8"))

        if total_count is None:
            total_count = corps.get("total_count", 0)
            logger.info("éCO2mix total_count=%d", total_count)

        resultats = corps.get("results", [])
        for r in resultats:
            res = normaliser_fn(r)
            if res is not None:
                cle, creneau = res
                nouveaux[cle] = creneau

        offset += len(resultats)
        if not resultats or (total_count is not None and offset >= total_count):
            break

    _dernier_fetch_api = datetime.now(TZ_PARIS).isoformat()
    return nouveaux


_CHAMPS_TR = (
    "date_heure,consommation,nucleaire,hydraulique,gaz,eolien_terrestre,"
    "eolien_offshore,solaire,bioenergies,fioul,charbon,pompage,taux_co2,"
    "ech_comm_angleterre,ech_comm_espagne,ech_comm_italie,ech_comm_suisse,"
    "ech_comm_allemagne_belgique"
)
_CHAMPS_H = (
    "date_heure,consommation,nucleaire,hydraulique,gaz,eolien,solaire,"
    "bioenergies,fioul,charbon,pompage,taux_co2,"
    "ech_comm_angleterre,ech_comm_espagne,ech_comm_italie,ech_comm_suisse,"
    "ech_comm_allemagne_belgique"
)


def _tranches_mensuelles(date_debut: datetime, date_fin: datetime):
    """
    Génère des paires (début, fin) mois par mois.
    Le premier mois commence à date_debut exact (pas au 1er du mois)
    pour éviter de re-fetcher des données déjà en cache.
    """
    import calendar
    dt_mois = date_debut.replace(day=1, hour=0, minute=0, second=0)
    premier = True
    while dt_mois <= date_fin:
        dernier_jour = calendar.monthrange(dt_mois.year, dt_mois.month)[1]
        fin_mois = dt_mois.replace(day=dernier_jour, hour=23, minute=59, second=59)
        debut_tranche = date_debut if premier else dt_mois
        premier = False
        yield debut_tranche, min(fin_mois, date_fin)
        # Passer au mois suivant
        if dt_mois.month == 12:
            dt_mois = dt_mois.replace(year=dt_mois.year + 1, month=1)
        else:
            dt_mois = dt_mois.replace(month=dt_mois.month + 1)


def _fetch_historique_par_mois(date_debut: datetime, date_fin: datetime) -> dict:
    """
    Fetch historique découpé mois par mois pour contourner la limite
    de 10 000 enregistrements par requête de l'API ODRE.
    """
    url_h = getattr(
        _parametres, "URL_API_RTE_HISTORIQUE",
        "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/eco2mix-national-cons-def/records"
    )
    nouveaux = {}
    for d_mois, f_mois in _tranches_mensuelles(date_debut, date_fin):
        filtre = "date_heure >= '{}' and date_heure <= '{}'".format(
            d_mois.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            f_mois.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        )
        logger.info("éCO2mix historique %s → %s",
                    d_mois.strftime("%Y-%m"), f_mois.strftime("%Y-%m-%d"))
        nouveaux.update(_fetch_pagine(url_h, _CHAMPS_H, filtre, _normaliser_record_historique))
    return nouveaux


def _fetch_et_persister(date_debut: datetime, date_fin: datetime) -> dict:
    """
    Récupère les créneaux sur la plage donnée (date_debut/date_fin en UTC).
    Route vers l'API temps réel (récent) ou historique (ancien, par mois).
    Filtres passés en UTC explicite — sans ambiguïté DST.
    Persiste tout sans purge.
    """
    now   = datetime.now(TZ_UTC)
    seuil = now - timedelta(days=_SEUIL_HISTORIQUE_JOURS)

    debut_utc = date_debut.astimezone(TZ_UTC)
    fin_utc   = date_fin.astimezone(TZ_UTC)

    nouveaux = {}

    if fin_utc < seuil:
        nouveaux.update(_fetch_historique_par_mois(debut_utc, fin_utc))
    elif debut_utc >= seuil:
        filtre = "date_heure >= '{}' and date_heure <= '{}'".format(
            debut_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            fin_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        )
        nouveaux.update(_fetch_pagine(_parametres.URL_API_RTE, _CHAMPS_TR, filtre, _normaliser_record_tr))
    else:
        # Plage mixte : partie ancienne mois par mois, partie récente en un bloc
        nouveaux.update(_fetch_historique_par_mois(debut_utc, seuil))
        filtre_r = "date_heure >= '{}' and date_heure <= '{}'".format(
            seuil.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            fin_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        )
        nouveaux.update(_fetch_pagine(_parametres.URL_API_RTE, _CHAMPS_TR, filtre_r, _normaliser_record_tr))

    with _lock:
        donnees = _charger_donnees()
        donnees["creneaux"].update(nouveaux)
        _sauvegarder_donnees(donnees)

    logger.info("éCO2mix : %d créneaux persistés", len(nouveaux))
    return _charger_donnees()


# ─── Calcul CO2 marginal ──────────────────────────────────────────────────────


def calcul_co2_marginal(creneau: dict) -> dict:
    """
    Calcule le CO2 marginal pour un créneau donné.

    Méthode AmperSage :
    - Filières triées par facteur d'émission décroissant
    - Cumul MW jusqu'à SEUIL_MARGINAL × production totale
    - Dernière filière incluse en prorata si nécessaire
    - Résultat = moyenne pondérée des facteurs sur les MW retenus

    N'utilise pas taux_co2_rte. Ne modifie pas le CO2 moyen.
    """
    mix = creneau.get("mix", {})
    facteurs = _parametres.FACTEURS_EMISSION
    seuil = _parametres.SEUIL_MARGINAL

    production_totale = sum(
        v for k, v in mix.items()
        if k != "pompage" and isinstance(v, (int, float)) and v > 0
    )

    seuil_mw = production_totale * seuil

    if production_totale <= 0 or seuil_mw <= 0:
        return {
            "taux_gco2_kwh": 0,
            "seuil_pct": seuil,
            "seuil_mw": 0.0,
            "filieres_retenues": [],
            "methode": "marginale",
        }

    # Filières connues, triées par facteur décroissant
    filieres = [
        (filiere, float(mix.get(filiere, 0) or 0), float(facteur))
        for filiere, facteur in facteurs.items()
        if (mix.get(filiere) or 0) > 0
    ]
    filieres.sort(key=lambda x: x[2], reverse=True)

    cumul_mw = 0.0
    poids_co2 = 0.0
    filieres_retenues = []

    for filiere, mw_total, facteur in filieres:
        if cumul_mw >= seuil_mw:
            break
        restant = seuil_mw - cumul_mw
        partiel = mw_total > restant
        mw_retenus = restant if partiel else mw_total
        co2_th = facteur * mw_retenus

        filieres_retenues.append({
            "filiere":    filiere,
            "mw_total":   round(mw_total, 1),
            "mw_retenus": round(mw_retenus, 1),
            "facteur":    facteur,
            "co2_th":     round(co2_th, 1),
            "partiel":    partiel,
        })
        cumul_mw += mw_retenus
        poids_co2 += co2_th

    taux = poids_co2 / cumul_mw if cumul_mw > 0 else 0

    return {
        "taux_gco2_kwh":    round(taux, 1),
        "seuil_pct":        seuil,
        "seuil_mw":         round(seuil_mw, 1),
        "filieres_retenues": filieres_retenues,
        "methode":          "marginale",
    }


# ─── Fonctions principales ───────────────────────────────────────────────────


def recharger_parametres():
    """
    Recharge parametres.py en ré-exécutant le loader du spec.
    importlib.reload() échoue sur les modules chargés via spec_from_file_location
    car il tente une résolution par nom dans sys.path → on ré-exécute directement.
    """
    _param_spec.loader.exec_module(_parametres)
    logger.info("éCO2mix : parametres.py rechargé (CO2 marginal recalculé à la prochaine demande)")


def get_co2_creneau(date_heure: str) -> dict:
    """
    Retourne les données CO2 pour un créneau donné.
    source_donnee = "cache" si déjà présent, "api" si récupéré maintenant.
    Retourne {"statut": "indisponible", ...} si l'API échoue.
    """
    try:
        dt = datetime.fromisoformat(date_heure).astimezone(TZ_UTC)
    except Exception:
        return {"statut": "erreur", "raison": "Format date invalide : {}".format(date_heure)}

    # Normaliser sur 15 min (en UTC)
    dt = dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)
    cle = dt.isoformat()

    donnees = _charger_donnees()
    source_donnee = "cache"

    if cle not in donnees.get("creneaux", {}):
        try:
            donnees = _fetch_et_persister(dt, dt)
            source_donnee = "api"
        except Exception as exc:
            logger.warning("éCO2mix get_co2_creneau indisponible → %s", exc)
            meta = donnees.get("meta", {})
            return {
                "statut": "indisponible",
                "raison": str(exc),
                "derniere_donnee": meta.get("dernier_enregistrement"),
            }

    creneau = donnees.get("creneaux", {}).get(cle)
    if not creneau:
        return {"statut": "indisponible", "raison": "Créneau absent", "derniere_donnee": None}

    return _formater_creneau(cle, creneau, source_donnee)


def get_co2_journee(date: str) -> list:
    """
    Retourne les 96 créneaux de la journée.
    Un seul appel API couvrant la plage complète des créneaux manquants.
    """
    try:
        # La date est en heure de Paris : on couvre minuit→23h45 Paris, converti en UTC
        dt_jour  = datetime.strptime(date[:10], "%Y-%m-%d").replace(tzinfo=TZ_PARIS)
        dt_debut = dt_jour.replace(hour=0,  minute=0 ).astimezone(TZ_UTC)
        dt_fin   = dt_jour.replace(hour=23, minute=45).astimezone(TZ_UTC)
    except Exception:
        return []

    manquants = _creneaux_manquants(dt_debut, dt_fin)
    if manquants:
        try:
            _fetch_et_persister(dt_debut, dt_fin)
        except Exception as exc:
            logger.warning("éCO2mix get_co2_journee API indisponible → %s", exc)

    donnees = _charger_donnees()
    creneaux = donnees.get("creneaux", {})
    resultats = []
    dt = dt_debut
    while dt <= dt_fin:
        cle = dt.isoformat()
        c = creneaux.get(cle)
        if c:
            resultats.append(_formater_creneau(cle, c, "cache"))
        dt = dt + timedelta(minutes=15)
    return resultats


def get_stats_base() -> dict:
    """Retourne des statistiques sur la base de données locale."""
    donnees = _charger_donnees()
    meta = donnees.get("meta", {})
    chemin = _fichier_donnees()
    taille_ko = round(chemin.stat().st_size / 1024, 1) if chemin.exists() else 0
    return {
        "total_creneaux":         meta.get("total_creneaux", 0),
        "premier_enregistrement": meta.get("premier_enregistrement"),
        "dernier_enregistrement": meta.get("dernier_enregistrement"),
        "taille_fichier_ko":      taille_ko,
        "dernier_fetch_api":      _dernier_fetch_api,
    }


def _formater_creneau(cle: str, creneau: dict, source_donnee: str) -> dict:
    """Formate un créneau brut en réponse complète avec CO2 moyen et marginal."""
    mix = creneau.get("mix", {})
    production_totale = sum(
        v for k, v in mix.items()
        if k != "pompage" and isinstance(v, (int, float)) and v > 0
    )
    co2_marginal = calcul_co2_marginal(creneau)
    return {
        "date_heure":           cle,
        "consommation_mw":      creneau.get("consommation"),
        "production_totale_mw": round(production_totale, 1),
        "co2_moyen": {
            "taux_gco2_kwh": creneau.get("taux_co2_rte"),
            "source":        "RTE officiel",
            "methode":       "moyenne",
        },
        "co2_marginal":  co2_marginal,
        "mix":           mix,
        "echanges":      creneau.get("echanges", {}),
        "source_donnee": source_donnee,
    }


# ─── Gestion des paramètres ───────────────────────────────────────────────────


def _lire_param_actuel() -> dict:
    """Retourne les valeurs actuelles de parametres.py sous forme de dict."""
    return {
        "URL_API_RTE":              _parametres.URL_API_RTE,
        "INTERVALLE_MIN_MINUTES":   _parametres.INTERVALLE_MIN_MINUTES,
        "TIMEOUT_SECONDES":         _parametres.TIMEOUT_SECONDES,
        "LIMITE_ENREGISTREMENTS":   _parametres.LIMITE_ENREGISTREMENTS,
        "FICHIER_DONNEES":          _parametres.FICHIER_DONNEES,
        "FACTEURS_EMISSION":        _parametres.FACTEURS_EMISSION,
        "SEUIL_MARGINAL":           _parametres.SEUIL_MARGINAL,
        "INFOBULLE_DELAI_MS":       _parametres.INFOBULLE_DELAI_MS,
        "INFOBULLE_FILIERES_MIN_PCT": _parametres.INFOBULLE_FILIERES_MIN_PCT,
    }


def _ecrire_parametres_py(p: dict):
    """Régénère parametres.py depuis le dict p."""
    facteurs = p.get("FACTEURS_EMISSION", _parametres.FACTEURS_EMISSION)
    lignes_facteurs = "\n".join(
        '    "{}": {},'.format(k, v) for k, v in facteurs.items()
    )
    contenu = (
        '"""\n'
        'parametres.py \u2014 Configuration du module \u00e9CO2mix.\n'
        'Ce fichier peut \u00eatre modifi\u00e9 via l\u2019interface de param\u00e9trage d\u2019AmperSage.\n'
        '"""\n\n'
        'URL_API_RTE = "{}"\n'
        'URL_API_RTE_HISTORIQUE = "{}"\n'
        'INTERVALLE_MIN_MINUTES = {}\n'
        'TIMEOUT_SECONDES = {}\n'
        'LIMITE_ENREGISTREMENTS = {}\n'
        'FICHIER_DONNEES = "{}"\n\n'
        '# Facteurs d\u2019\u00e9mission par fili\u00e8re en gCO2eq/kWh (source\u00a0: IPCC/ADEME)\n'
        '# Utilis\u00e9s UNIQUEMENT pour le calcul du CO2 marginal AmperSage.\n'
        'FACTEURS_EMISSION = {{\n'
        '{}\n'
        '}}\n\n'
        '# Fraction de la production totale retenue pour le calcul marginal\n'
        'SEUIL_MARGINAL = {}\n\n'
        '# D\u00e9lai avant apparition de l\u2019infobulle (ms)\n'
        'INFOBULLE_DELAI_MS = {}\n\n'
        '# Seuil minimum d\u2019affichage d\u2019une fili\u00e8re dans l\u2019infobulle (%)\n'
        'INFOBULLE_FILIERES_MIN_PCT = {}\n'
    ).format(
        p.get("URL_API_RTE",            _parametres.URL_API_RTE),
        p.get("URL_API_RTE_HISTORIQUE", getattr(_parametres, "URL_API_RTE_HISTORIQUE",
              "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/eco2mix-national-cons-def/records")),
        p.get("INTERVALLE_MIN_MINUTES", _parametres.INTERVALLE_MIN_MINUTES),
        p.get("TIMEOUT_SECONDES",       _parametres.TIMEOUT_SECONDES),
        p.get("LIMITE_ENREGISTREMENTS", _parametres.LIMITE_ENREGISTREMENTS),
        p.get("FICHIER_DONNEES",        _parametres.FICHIER_DONNEES),
        lignes_facteurs,
        p.get("SEUIL_MARGINAL",         _parametres.SEUIL_MARGINAL),
        p.get("INFOBULLE_DELAI_MS",     _parametres.INFOBULLE_DELAI_MS),
        p.get("INFOBULLE_FILIERES_MIN_PCT", _parametres.INFOBULLE_FILIERES_MIN_PCT),
    )
    param_file = BASE_DIR / "parametres.py"
    with open(param_file, "w", encoding="utf-8") as f:
        f.write(contenu)


# ─── Commandes (contrat run()) ────────────────────────────────────────────────


def cmd_get(params: dict, global_param: dict) -> dict:
    """Retourne le créneau le plus récent disponible en base locale."""
    donnees = _charger_donnees()
    creneaux = donnees.get("creneaux", {})
    if not creneaux:
        return _reponse(False, "GET", error="Base locale vide")
    cle = max(creneaux.keys())
    return _reponse(True, "GET", data=_formater_creneau(cle, creneaux[cle], "cache"))


def cmd_update(params: dict, global_param: dict) -> dict:
    """
    Récupère les créneaux manquants sur la plage demandée.
    Modes :
      - params["date_debut"] + params["date_fin"] : plage ISO explicite
      - params["nb_heures"] (défaut 2) : dernières N heures
    """
    now = datetime.now(TZ_PARIS)

    if params.get("date_debut") and params.get("date_fin"):
        try:
            date_debut = datetime.fromisoformat(params["date_debut"]).astimezone(TZ_PARIS)
            date_fin   = datetime.fromisoformat(params["date_fin"]).astimezone(TZ_PARIS)
        except Exception as exc:
            return _reponse(False, "UPDATE", error="date_debut/date_fin invalides : {}".format(exc))
        # Plafonner date_fin à maintenant
        date_fin = min(date_fin, now)
    else:
        nb_heures = min(max(int(params.get("nb_heures", 2)), 1), 24 * 365)
        date_debut = now - timedelta(hours=nb_heures)
        date_fin   = now

    # Normaliser sur 15 min
    date_debut = date_debut.replace(minute=(date_debut.minute // 15) * 15, second=0, microsecond=0)
    date_fin   = date_fin.replace(minute=(date_fin.minute // 15) * 15, second=0, microsecond=0)

    manquants = _creneaux_manquants(date_debut, date_fin)
    if not manquants:
        logger.info("éCO2mix UPDATE : tous les créneaux déjà en cache")
        return cmd_get(params, global_param)

    # Grouper les manquants en plages contiguës pour minimiser les appels API
    groupes = _grouper_contigus(manquants)
    logger.info("éCO2mix UPDATE : %d créneaux manquants en %d groupe(s) (%s → %s)",
                len(manquants), len(groupes),
                manquants[0].isoformat()[:10], manquants[-1].isoformat()[:10])

    try:
        for groupe in groupes:
            _fetch_et_persister(groupe[0], groupe[-1])
    except urllib.error.URLError as exc:
        msg = "Erreur réseau : {}".format(exc.reason)
        logger.error("éCO2mix UPDATE : %s", msg)
        donnees = _charger_donnees()
        meta = donnees.get("meta", {})
        return _reponse(False, "UPDATE", error=msg, data={
            "statut": "indisponible",
            "raison": msg,
            "derniere_donnee": meta.get("dernier_enregistrement"),
        })
    except Exception as exc:
        msg = "Erreur inattendue : {}".format(exc)
        logger.error("éCO2mix UPDATE : %s", msg)
        return _reponse(False, "UPDATE", error=msg)

    # Après fetch, marquer les créneaux encore manquants :
    # - slot récent (< 48h) → indisponible temporaire, re-tenté après délai
    # - slot ancien (>= 48h) → indisponible permanent (ex: slot DST-hiver CEST)
    encore_manquants = _creneaux_manquants(date_debut, date_fin)
    if encore_manquants:
        now_utc = datetime.now(TZ_UTC)
        seuil_permanent = now_utc - timedelta(hours=48)
        permanents = [dt for dt in encore_manquants if dt < seuil_permanent]
        temporaires = [dt for dt in encore_manquants if dt >= seuil_permanent]
        with _lock:
            donnees = _charger_donnees()
            for dt in permanents:
                donnees["creneaux"][dt.isoformat()] = {"indisponible": True}
            for dt in temporaires:
                donnees["creneaux"][dt.isoformat()] = {
                    "indisponible_tmp": True,
                    "depuis": now_utc.isoformat(),
                }
            _sauvegarder_donnees(donnees)
        if permanents:
            logger.info("éCO2mix : %d créneaux marqués indisponibles (structurel, ex: DST-hiver)",
                        len(permanents))
        if temporaires:
            logger.info("éCO2mix : %d créneaux marqués indisponibles temporairement (re-tentés dans %dh)",
                        len(temporaires), _parametres.DELAI_RETRY_INDISPONIBLE_HEURES)

    return cmd_get(params, global_param)


def cmd_get_param(params: dict, global_param: dict) -> dict:
    """Retourne les paramètres actuels (noms en minuscules pour le panel)."""
    p = _lire_param_actuel()
    return _reponse(True, "GET_PARAM", data={
        "url_api_rte":              p["URL_API_RTE"],
        "intervalle_min_minutes":   p["INTERVALLE_MIN_MINUTES"],
        "timeout_secondes":         p["TIMEOUT_SECONDES"],
        "limite_enregistrements":   p["LIMITE_ENREGISTREMENTS"],
        "fichier_donnees":          p["FICHIER_DONNEES"],
        "facteurs_emission":        p["FACTEURS_EMISSION"],
        "seuil_marginal":           p["SEUIL_MARGINAL"],
        "infobulle_delai_ms":       p["INFOBULLE_DELAI_MS"],
        "infobulle_filieres_min_pct": p["INFOBULLE_FILIERES_MIN_PCT"],
    })


def cmd_set_param(params: dict, global_param: dict) -> dict:
    """Met à jour parametres.py, recharge le module, recalcule le CO2 marginal."""
    actuel = _lire_param_actuel()

    # Conversion seuil marginal : l'IHM envoie un pourcentage (10 = 10 %)
    seuil_brut = params.get("seuil_marginal", actuel["SEUIL_MARGINAL"])
    if isinstance(seuil_brut, (int, float)):
        seuil_dec = seuil_brut / 100.0 if seuil_brut > 1 else float(seuil_brut)
        seuil_dec = max(0.01, min(0.50, seuil_dec))
    else:
        seuil_dec = actuel["SEUIL_MARGINAL"]

    facteurs = params.get("facteurs_emission", actuel["FACTEURS_EMISSION"])
    if not isinstance(facteurs, dict):
        facteurs = actuel["FACTEURS_EMISSION"]

    nouveau = {
        "URL_API_RTE":            params.get("url_api_rte", actuel["URL_API_RTE"]),
        "INTERVALLE_MIN_MINUTES": int(params.get("intervalle_min_minutes", actuel["INTERVALLE_MIN_MINUTES"])),
        "TIMEOUT_SECONDES":       int(params.get("timeout_secondes", actuel["TIMEOUT_SECONDES"])),
        "LIMITE_ENREGISTREMENTS": int(params.get("limite_enregistrements", actuel["LIMITE_ENREGISTREMENTS"])),
        "FICHIER_DONNEES":        params.get("fichier_donnees", actuel["FICHIER_DONNEES"]),
        "FACTEURS_EMISSION":      {k: int(v) for k, v in facteurs.items()},
        "SEUIL_MARGINAL":         seuil_dec,
        "INFOBULLE_DELAI_MS":     int(params.get("infobulle_delai_ms", actuel["INFOBULLE_DELAI_MS"])),
        "INFOBULLE_FILIERES_MIN_PCT": float(params.get("infobulle_filieres_min_pct", actuel["INFOBULLE_FILIERES_MIN_PCT"])),
    }

    _ecrire_parametres_py(nouveau)
    recharger_parametres()
    logger.info("éCO2mix SET_PARAM : parametres.py mis à jour, CO2 marginal recalculé")

    data = {k.lower(): v for k, v in nouveau.items()}
    data["message"] = "Paramètres sauvegardés — CO2 marginal recalculé"
    return _reponse(True, "SET_PARAM", data=data)


def cmd_init_param(params: dict, global_param: dict) -> dict:
    """Réinitialise parametres.py aux valeurs par défaut IPCC/ADEME."""
    defaut = {
        "URL_API_RTE":            "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/eco2mix-national-tr/records",
        "INTERVALLE_MIN_MINUTES": 15,
        "TIMEOUT_SECONDES":       10,
        "LIMITE_ENREGISTREMENTS": 100,
        "FICHIER_DONNEES":        "data/eco2mix_donnees.json",
        "FACTEURS_EMISSION": {
            "nucleaire": 12, "hydraulique": 6, "eolien_terrestre": 11,
            "eolien_offshore": 11, "solaire": 45, "bioenergies": 230,
            "gaz": 490, "fioul": 650, "charbon": 820,
        },
        "SEUIL_MARGINAL":           0.10,
        "INFOBULLE_DELAI_MS":       200,
        "INFOBULLE_FILIERES_MIN_PCT": 1,
    }
    _ecrire_parametres_py(defaut)
    recharger_parametres()
    logger.info("éCO2mix INIT_PARAM : réinitialisé aux valeurs IPCC/ADEME")

    # Récupérer immédiatement les données des dernières 24h
    rep_update = cmd_update({"nb_heures": 24}, global_param)
    if not rep_update.get("status"):
        logger.warning("éCO2mix INIT_PARAM : update initial échoué → %s", rep_update.get("error"))

    data = {k.lower(): v for k, v in defaut.items()}
    data["update_status"] = rep_update.get("status", False)
    data["update_error"]  = rep_update.get("error")
    return _reponse(True, "INIT_PARAM", data=data)


def cmd_get_stats(params: dict, global_param: dict) -> dict:
    """Retourne les statistiques de la base locale."""
    return _reponse(True, "GET_STATS", data=get_stats_base())


def cmd_get_creneau(params: dict, global_param: dict) -> dict:
    """Retourne le CO2 pour un créneau donné (params["date_heure"] requis)."""
    date_heure = params.get("date_heure")
    if not date_heure:
        now = datetime.now(TZ_PARIS)
        date_heure = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0).isoformat()
    resultat = get_co2_creneau(date_heure)
    if resultat.get("statut") in ("erreur", "indisponible"):
        return _reponse(False, "GET_CRENEAU", error=resultat.get("raison"), data=resultat)
    return _reponse(True, "GET_CRENEAU", data=resultat)


def cmd_get_journee(params: dict, global_param: dict) -> dict:
    """Retourne les 96 créneaux d'une journée (params["date"] ou aujourd'hui)."""
    date = params.get("date", datetime.now(TZ_PARIS).strftime("%Y-%m-%d"))
    creneaux = get_co2_journee(date)
    return _reponse(True, "GET_JOURNEE", data={"date": date, "creneaux": creneaux, "total": len(creneaux)})


def _cls_co2(valeur):
    """Classe CSS selon le taux de CO2 (g/kWh)."""
    if valeur is None:
        return ""
    if valeur < 100:
        return "co2-bas"
    if valeur < 300:
        return "co2-med"
    return "co2-haut"


def get_co2_slots(ts_list: list) -> dict:
    """
    Pour une liste de timestamps UTC (créneaux 30 min d'api_conso), retourne les données CO2.
    Agrège les 2 créneaux éCO2mix de 15 min correspondants par moyenne.
    Retourne dict : { ts_utc_str → creneau_json | None }
    """
    donnees  = _charger_donnees()
    creneaux = donnees.get("creneaux", {})
    resultats = {}

    for ts in ts_list:
        try:
            dt       = datetime.fromisoformat(ts).astimezone(TZ_UTC)
            dt15_a   = dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)
            dt15_b   = dt15_a + timedelta(minutes=15)

            cr_a = creneaux.get(dt15_a.isoformat())
            cr_b = creneaux.get(dt15_b.isoformat())

            # Exclure les créneaux marqués indisponibles
            candidats = [
                c for c in (cr_a, cr_b)
                if c is not None
                and not c.get("indisponible")
                and not c.get("indisponible_tmp")
            ]
            if not candidats:
                resultats[ts] = None
                continue

            # CO2 moyen RTE : moyenne des deux créneaux
            vals_rte   = [c.get("taux_co2_rte") for c in candidats if c.get("taux_co2_rte") is not None]
            co2_moyen_val = sum(vals_rte) / len(vals_rte) if vals_rte else None

            # Mix moyen des deux créneaux
            cles_mix  = set()
            for c in candidats:
                cles_mix.update(c.get("mix", {}).keys())
            mix_moyen = {
                k: sum((c.get("mix", {}).get(k) or 0) for c in candidats) / len(candidats)
                for k in cles_mix
            }

            # CO2 marginal calculé sur le mix moyen
            cr_moyen = dict(candidats[0])
            cr_moyen["mix"] = mix_moyen
            co2_marg_data = calcul_co2_marginal(cr_moyen)
            # Alléger : filieres_retenues recalculées côté JS
            co2_marg_lean = {k: v for k, v in co2_marg_data.items()
                             if k != "filieres_retenues"} if co2_marg_data else None

            resultats[ts] = {
                "date_heure":   dt15_a.isoformat(),
                "co2_moyen":    {"taux_gco2_kwh": co2_moyen_val},
                "co2_marginal": co2_marg_lean,
                "mix":          mix_moyen,
                "echanges":     candidats[0].get("echanges", {}),
                "source_donnee": "cache",
            }
        except Exception:
            resultats[ts] = None

    return resultats


def cmd_get_data(params: dict, global_param: dict) -> dict:
    """
    Retourne les données CO2 pré-calculées par créneau 30 min + les masques HTML.
    Modèle identique aux tarifs : un seul JSON avec data + masque.
    params["ts_list"] : liste de timestamps UTC (records api_conso).
    Chaque entrée creneaux combine :
      - les champs d'affichage (affichage_co2m, cls_co2m, …) pour main.py
      - les champs infobulle (date_heure, co2_moyen, co2_marginal, mix, echanges)
        pour le store JS (/api/eco2mix_creneaux).
    """
    ts_list = params.get("ts_list", [])
    creneaux_slots = get_co2_slots(ts_list)

    creneaux_enrichis = {}
    for ts, slot in creneaux_slots.items():
        if slot is None:
            creneaux_enrichis[ts] = None
            continue
        co2m_val  = (slot.get("co2_moyen") or {}).get("taux_gco2_kwh")
        co2mg_val = (slot.get("co2_marginal") or {}).get("taux_gco2_kwh")
        enrichi   = dict(slot)
        enrichi["affichage_co2m"]  = "{:.0f}".format(co2m_val)  if co2m_val  is not None else "\u2014"
        enrichi["affichage_co2mg"] = "{:.0f}".format(co2mg_val) if co2mg_val is not None else "\u2014"
        enrichi["cls_co2m"]        = _cls_co2(co2m_val)
        enrichi["cls_co2mg"]       = _cls_co2(co2mg_val)
        creneaux_enrichis[ts]      = enrichi

    return _reponse(True, "GET_DATA", data={"creneaux": creneaux_enrichis})


def cmd_get_masks(params: dict, global_param: dict) -> dict:
    """Retourne les masques HTML du module (tableau_titre, tableau_ligne, params)."""
    tpl_dir = BASE_DIR / "templates"
    panel   = BASE_DIR / "eco2mix_panel.html"

    def _lire(chemin: Path) -> str:
        return chemin.read_text(encoding="utf-8") if chemin.exists() else ""

    return _reponse(True, "GET_MASKS", data={
        "tableau_titre": _lire(tpl_dir / "tableau_titre.html"),
        "tableau_ligne": _lire(tpl_dir / "tableau_ligne.html"),
        "params":        _lire(panel),
    })


# ─── Point d'entrée ───────────────────────────────────────────────────────────

COMMANDES = {
    "GET":          cmd_get,
    "UPDATE":       cmd_update,
    "GET_PARAM":    cmd_get_param,
    "SET_PARAM":    cmd_set_param,
    "INIT_PARAM":   cmd_init_param,
    "GET_STATS":    cmd_get_stats,
    "GET_CRENEAU":  cmd_get_creneau,
    "GET_JOURNEE":  cmd_get_journee,
    "GET_DATA":     cmd_get_data,
    "GET_MASKS":    cmd_get_masks,
}


def run(mode: str, params: dict = None, global_param: dict = None) -> dict:
    """Point d'entrée appelé par server.py ou en mode autonome."""
    params = params or {}
    global_param = global_param if global_param is not None else {}

    if mode not in COMMANDES:
        return _reponse(False, mode, error="Mode inconnu : {}".format(mode))

    return COMMANDES[mode](params, global_param)


if __name__ == "__main__":
    import sys as _sys
    import json as _json
    logging.basicConfig(level=logging.WARNING)
    _mode = _sys.argv[1] if len(_sys.argv) > 1 else "GET"
    print(_json.dumps(run(_mode), indent=2, ensure_ascii=False))
