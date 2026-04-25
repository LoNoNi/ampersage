"""
api_conso.py - Script mock API Conso (API conso).
Expose 6 appels : GET, UPDATE, GET_PARAM, SET_PARAM, INIT_PARAM, GET_HISTORY_START.
"""

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("api_conso")

TZ_PARIS = ZoneInfo("Europe/Paris")
TZ_UTC   = ZoneInfo("UTC")
BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "api_conso_data.json"
PARAM_FILE = BASE_DIR / "api_conso_param.json"

# Paramètres stockés dans le script (valeurs par défaut)
PARAM_DEFAUT = {
    "adresse": "https://conso.boris.sh/api/",
    "commentaire": "Script API conso mock",
}

# Profil de consommation type foyer français (kWh par demi-heure).
# 48 valeurs couvrant 00h00 → 23h30, avec pics matin (07h-09h) et soir (18h-21h).
_PROFIL_JOURNALIER = [
    # 00h00-05h30 : veille, consommation de base
    0.08, 0.07, 0.07, 0.06, 0.06, 0.06, 0.05, 0.05,
    0.05, 0.05, 0.06, 0.08,
    # 06h00-08h30 : réveil, douche, petit-déjeuner
    0.18, 0.38, 0.72, 0.85, 0.78, 0.60,
    # 09h00-11h30 : matinée calme
    0.30, 0.22, 0.18, 0.16, 0.15, 0.15,
    # 12h00-13h30 : déjeuner
    0.48, 0.62, 0.55, 0.32,
    # 14h00-17h30 : après-midi
    0.14, 0.13, 0.13, 0.14, 0.16, 0.20, 0.26, 0.38,
    # 18h00-21h30 : retour foyer, dîner, TV
    0.65, 0.88, 1.02, 1.10, 0.95, 0.82, 0.70, 0.62,
    # 22h00-23h30 : soirée, coucher
    0.42, 0.28, 0.18, 0.12,
]

# Coefficients journaliers pour simuler une variation réaliste d'un jour à l'autre.
# J-3 : journée froide (+15%), J-2 : journée normale, J-1 : journée douce (-10%)
_COEFF_PAR_JOUR = [1.15, 1.00, 0.90]


def _generer_mock_records(nb_jours: int = 7) -> list:
    """Génère nb_jours jours de données mock (J-nb_jours à J-1), pas 30 min, valeurs réalistes."""
    records = []
    aujourd_hui = datetime.now(TZ_PARIS).replace(hour=0, minute=0, second=0, microsecond=0)
    for j in range(nb_jours):
        date_jour = aujourd_hui - timedelta(days=nb_jours - j)
        # Coefficient cyclique basé sur _COEFF_PAR_JOUR
        coeff = _COEFF_PAR_JOUR[j % len(_COEFF_PAR_JOUR)]
        for slot, valeur_base in enumerate(_PROFIL_JOURNALIER):
            ts = date_jour + timedelta(minutes=30 * slot)
            kwh = round(valeur_base * coeff, 3)
            pmax = round(kwh * 2 * 1.5, 3)
            records.append({
                "ts": ts.astimezone(TZ_UTC).isoformat(),
                "kwh": kwh,
                "pmax": pmax,
                "type": "reel",
            })
    return records


def _reponse(status: bool, mode: str, data=None, error=None) -> dict:
    """Construit la réponse standardisée."""
    return {
        "status": status,
        "mode": mode,
        "error": error,
        "data": data or {},
    }


def _lire_data() -> Optional[dict]:
    """Lit api_conso_data.json. Retourne None si absent."""
    if not DATA_FILE.exists():
        return None
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _ecrire_data(data: dict):
    """Écrit api_conso_data.json."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _lire_param() -> Optional[dict]:
    """Lit api_conso_param.json. Retourne None si absent."""
    if not PARAM_FILE.exists():
        return None
    with open(PARAM_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _ecrire_param(param: dict):
    """Écrit api_conso_param.json."""
    with open(PARAM_FILE, "w", encoding="utf-8") as f:
        json.dump(param, f, ensure_ascii=False, indent=2)


def _extraire_prm_du_token(token: str) -> Optional[str]:
    """Extrait le PRM depuis le payload JWT (champ 'sub'), sans vérifier la signature."""
    try:
        parties = token.split(".")
        if len(parties) < 2:
            return None
        payload_b64 = parties[1]
        # Ajout du padding base64 si nécessaire
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64).decode("utf-8"))
        sub = payload.get("sub")
        if isinstance(sub, list) and sub:
            return str(sub[0])
        if isinstance(sub, str) and sub:
            return sub
    except Exception:
        pass
    return None


def _calculer_prochaine_maj() -> str:
    """
    Retourne une date dans 4 jours, dans la fenêtre 14h00→04h00 (lendemain),
    avec heures/minutes/secondes vraiment aléatoires.
    Exemple : 2026-04-21T17:34:05+02:00
    """
    import random as _random
    base = datetime.now(TZ_PARIS) + timedelta(days=4)
    # Fenêtre de 14h = 50 400 secondes (14h00 → 04h00 le lendemain)
    offset = _random.randint(0, 50399)
    debut_fenetre = base.replace(hour=14, minute=0, second=0, microsecond=0)
    return (debut_fenetre + timedelta(seconds=offset)).isoformat()


def _maj_timestamps_param(statut: str = "actif"):
    """Met à jour statut, derniere_maj et prochaine_maj dans api_conso_param.json."""
    p = _lire_param() or {}
    p["statut"] = statut
    p["derniere_maj"] = datetime.now(TZ_PARIS).isoformat()
    if p.get("auto_refresh"):
        p["prochaine_maj"] = _calculer_prochaine_maj()
    _ecrire_param(p)


def _appel_api_reel(adresse: str, token: str, param: dict) -> list:
    """
    Appelle l'API conso (conso.boris.sh ou Enedis) et retourne les records au format interne.
    Lève urllib.error.URLError / urllib.error.HTTPError en cas d'échec.
    """
    aujourd_hui = datetime.now(TZ_PARIS).replace(hour=0, minute=0, second=0, microsecond=0)
    nb_jours = max(1, min(int(param.get("nb_jours") or 7), 60))
    start = (aujourd_hui - timedelta(days=nb_jours)).strftime("%Y-%m-%d")
    end = (aujourd_hui - timedelta(days=1)).strftime("%Y-%m-%d")

    query_params = {"start": start, "end": end}
    prm = param.get("prm") or _extraire_prm_du_token(token)
    if prm:
        query_params["prm"] = prm
        logger.info("API conso : PRM utilisé → %s", prm[:4] + "**********")
    else:
        logger.warning("API conso : PRM introuvable (ni param ni token JWT)")
    query = urllib.parse.urlencode(query_params)
    base = adresse.rstrip("/") + "/"
    url = base + "consumption_load_curve?" + query

    logger.info("API conso : appel → %s", url)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            corps = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        corps = exc.read().decode("utf-8", errors="replace")
        logger.error("API conso : HTTP %s → %s", exc.code, corps[:300])
        raise

    logger.debug("API conso : réponse brute → %s", corps[:800])
    data = json.loads(corps)
    return _convertir_reponse_api(data)


def _convertir_reponse_api(data: dict) -> list:
    """
    Convertit la réponse de l'API conso au format records interne.
    L'API retourne une puissance moyenne en W sur des pas de 30 min.
    Conversion : kWh = W × 0.5h / 1000
    """
    records = []
    # interval_reading est à la racine (pas sous meter_reading)
    for item in data.get("interval_reading", []):
        try:
            valeur_w = float(item.get("value", 0))
            kwh = round(valeur_w * 0.5 / 1000, 3)       # W → kWh sur 30 min
            pmax = round(valeur_w / 1000, 3)              # W → kW
            date_str = item.get("date", "")
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_PARIS).astimezone(TZ_UTC)
            records.append({
                "ts": dt.isoformat(),
                "kwh": kwh,
                "pmax": pmax,
                "type": "reel",
            })
        except (ValueError, TypeError):
            continue
    return records


# ─── Les 6 appels ────────────────────────────────────────────────────────────

def cmd_get(params: dict, global_param: dict) -> dict:
    """Lecture seule des données depuis api_conso_data.json."""
    data = _lire_data()
    if data is None:
        return _reponse(False, "GET", error="Fichier data absent")
    return _reponse(True, "GET", data=data)


def _donnees_fraiches(param: dict, fraicheur_minutes: int = 60) -> bool:
    """Retourne True si derniere_maj existe et date de moins de fraicheur_minutes."""
    derniere_maj = (param or {}).get("derniere_maj")
    if not derniere_maj:
        return False
    try:
        dt = datetime.fromisoformat(derniere_maj)
        age = (datetime.now(TZ_PARIS) - dt).total_seconds() / 60
        return age < fraicheur_minutes
    except Exception:
        return False


def cmd_update(params: dict, global_param: dict) -> dict:
    """Met à jour api_conso_data.json : mock si pas de token, appel réel sinon."""
    param = _lire_param()
    token = param.get("token") if param else None
    adresse = (param or {}).get("adresse", "https://conso.boris.sh/api/")
    donnees_existantes = _lire_data()
    nb_jours = max(1, min(int((param or {}).get("nb_jours") or 7), 60))
    fraicheur = int((param or {}).get("fraicheur_minutes", 60))
    force = params.get("force", False)

    # ── Mode mock : aucun token configuré ────────────────────────────────────
    if not token:
        records = _generer_mock_records(nb_jours)
        nouvelles_donnees = {"records": records, "mock": True}
        _ecrire_data(nouvelles_donnees)
        _maj_timestamps_param("démo (token absent)")
        logger.info("API conso UPDATE : mode mock (%d records)", len(records))
        return _reponse(True, "UPDATE", data=nouvelles_donnees)

    # ── Données déjà fraîches : pas d'appel HTTP ─────────────────────────────
    if not force and donnees_existantes and _donnees_fraiches(param, fraicheur):
        logger.info("API conso UPDATE : données fraîches (%d min), pas d'appel API", fraicheur)
        return _reponse(True, "UPDATE", data=donnees_existantes)

    # ── Mode réel : appel HTTP ────────────────────────────────────────────────
    mode_erreur = params.get("simulate_error")  # "timeout" | "http500" | None

    if mode_erreur == "timeout":
        logger.warning("API conso UPDATE : simulation timeout réseau")
        return _reponse(False, "UPDATE",
                        data=donnees_existantes or {},
                        error="Erreur réseau : timeout")

    if mode_erreur == "http500":
        logger.warning("API conso UPDATE : simulation erreur serveur HTTP 500")
        return _reponse(False, "UPDATE",
                        data=donnees_existantes or {},
                        error="Erreur serveur : HTTP 500")

    try:
        records = _appel_api_reel(adresse, token, param or {})
        # Fusion : les données API écrasent les doublons, l'historique est conservé
        existants = {r["ts"]: r for r in donnees_existantes.get("records", [])} if donnees_existantes else {}
        for r in records:
            existants[r["ts"]] = r
        records_fusionnes = sorted(existants.values(), key=lambda r: r["ts"])
        nouvelles_donnees = {"records": records_fusionnes, "mock": False}
        _ecrire_data(nouvelles_donnees)
        # Mise à jour des timestamps dans le fichier param
        _maj_timestamps_param("actif")
        logger.info("API conso UPDATE : %d nouveaux records API, total %d", len(records), len(records_fusionnes))
        return _reponse(True, "UPDATE", data=nouvelles_donnees)
    except urllib.error.HTTPError as exc:
        msg = "Erreur HTTP {} : {}".format(exc.code, exc.reason)
        logger.error("API conso UPDATE : %s", msg)
        _maj_timestamps_param(msg)
        return _reponse(False, "UPDATE", data=donnees_existantes or {}, error=msg)
    except urllib.error.URLError as exc:
        msg = "Erreur réseau : {}".format(exc.reason)
        logger.error("API conso UPDATE : %s", msg)
        _maj_timestamps_param(msg)
        return _reponse(False, "UPDATE", data=donnees_existantes or {}, error=msg)
    except Exception as exc:
        msg = "Erreur inattendue : {}".format(exc)
        logger.error("API conso UPDATE : %s", msg)
        _maj_timestamps_param(msg)
        return _reponse(False, "UPDATE", data=donnees_existantes or {}, error=msg)


def cmd_get_param(params: dict, global_param: dict) -> dict:
    """Retourne le contenu de api_conso_param.json."""
    param = _lire_param()
    if param is None:
        return _reponse(False, "GET_PARAM", error="Fichier param absent")
    return _reponse(True, "GET_PARAM", data=param)


def cmd_set_param(params: dict, global_param: dict) -> dict:
    """Merge partiel des paramètres dans api_conso_param.json."""
    param_actuel = _lire_param() or {}
    param_actuel.update(params)
    _ecrire_param(param_actuel)
    logger.info("API conso SET_PARAM : paramètres mis à jour")
    return _reponse(True, "SET_PARAM", data=param_actuel)


def cmd_init_param(params: dict, global_param: dict) -> dict:
    """Recrée api_conso_param.json avec les valeurs par défaut."""
    param_defaut = {
        **PARAM_DEFAUT,
        "token": None,
        "prm": None,
        "nb_jours": 7,
        "fraicheur_minutes": 60,
        "auto_refresh": False,
        "derniere_maj": None,
        "prochaine_maj": None,
        "statut": "non initialisé",
    }
    _ecrire_param(param_defaut)
    logger.info("API conso INIT_PARAM : paramètres réinitialisés")
    return _reponse(True, "INIT_PARAM", data=param_defaut)


def cmd_get_history_start(params: dict, global_param: dict) -> dict:
    """
    Simule un appel API pour récupérer la date la plus ancienne disponible.
    Met à jour max_history_start dans Global_param.
    """
    mode_erreur = params.get("simulate_error")  # "timeout" | None

    param = _lire_param()
    token = param.get("token") if param else None

    if not token:
        logger.warning("API conso GET_HISTORY_START : token non renseigné")
        return _reponse(False, "GET_HISTORY_START", error="Token non renseigné")

    if mode_erreur == "timeout":
        logger.warning("API conso GET_HISTORY_START : simulation timeout réseau")
        global_param["max_history_start"] = None
        return _reponse(False, "GET_HISTORY_START",
                        error="Erreur réseau : timeout")

    # Mock : date statique
    date_historique = "2020-01-01"
    global_param["max_history_start"] = date_historique
    logger.info("API conso GET_HISTORY_START : date historique = %s", date_historique)
    return _reponse(True, "GET_HISTORY_START", data={"max_history_start": date_historique})


def cmd_get_data(params: dict, global_param: dict) -> dict:
    """Retourne les données api_conso (records + mock flag). Les masques sont dans GET_MASKS."""
    data = _lire_data()
    if data is None:
        return _reponse(False, "GET_DATA", error="Fichier data absent")

    return _reponse(True, "GET_DATA", data={
        "records": data.get("records", []),
        "mock":    data.get("mock", False),
    })


def cmd_get_masks(params: dict, global_param: dict) -> dict:
    """Retourne les masques HTML du module (tableau_titre, tableau_ligne, params)."""
    tpl_dir = BASE_DIR / "templates"
    panel   = BASE_DIR / "api_conso_panel.html"

    def _lire(chemin: Path) -> str:
        return chemin.read_text(encoding="utf-8") if chemin.exists() else ""

    return _reponse(True, "GET_MASKS", data={
        "tableau_titre": _lire(tpl_dir / "tableau_titre.html"),
        "tableau_ligne": _lire(tpl_dir / "tableau_ligne.html"),
        "params":        _lire(panel),
    })


def cmd_import_csv(params: dict, global_param: dict) -> dict:
    """
    Importe un CSV Enedis (debut;fin;kW, pas 10 min) et fusionne avec les données existantes.
    Les données importées écrasent les enregistrements existants pour les mêmes timestamps.
    Les données existantes non couvertes par le CSV sont conservées.
    """
    import io
    contenu = params.get("csv_content", "")
    if not contenu:
        return _reponse(False, "IMPORT_CSV", error="Contenu CSV manquant")

    nouveaux = {}
    lignes_ignorees = 0

    reader = io.StringIO(contenu)
    premiere = True
    for ligne in reader:
        ligne = ligne.strip()
        if not ligne:
            continue
        # Ignorer l'en-tête
        if premiere:
            premiere = False
            if "debut" in ligne.lower() or "kw" in ligne.lower():
                continue
        parties = ligne.split(";")
        if len(parties) < 3:
            lignes_ignorees += 1
            continue
        try:
            ts_str = parties[0].strip().strip('"')
            kw_str = parties[2].strip().strip('"').replace(",", ".")
            kw = float(kw_str)
            # Conversion : kWh = kW × (10 min / 60 min)
            kwh = round(kw / 6, 4)
            # Timestamp : ajouter :00 si pas de secondes, puis timezone Paris
            if len(ts_str) == 16:   # "2024-04-19T00:00"
                ts_str += ":00"
            dt = datetime.fromisoformat(ts_str).replace(tzinfo=TZ_PARIS).astimezone(TZ_UTC)
            ts_key = dt.isoformat()
            nouveaux[ts_key] = {
                "ts": ts_key,
                "kwh": kwh,
                "pmax": round(kw, 3),
                "type": "csv",
            }
        except (ValueError, IndexError):
            lignes_ignorees += 1
            continue

    if not nouveaux:
        return _reponse(False, "IMPORT_CSV", error="Aucun enregistrement valide dans le CSV")

    # Agrégation en pas de 30 min (somme kWh, max pmax)
    # Clé = timestamp fin du pas de 30 min (convention API)
    buckets = {}
    for ts_key, r in nouveaux.items():
        dt = datetime.fromisoformat(ts_key)
        # Début du bloc 30 min contenant ce record
        bloc_min = (dt.minute // 30) * 30
        debut_bloc = dt.replace(minute=bloc_min, second=0, microsecond=0)
        fin_bloc = debut_bloc + timedelta(minutes=30)
        cle = fin_bloc.isoformat()
        if cle not in buckets:
            buckets[cle] = {"ts": cle, "kwh": 0.0, "pmax": 0.0, "type": "csv"}
        buckets[cle]["kwh"] = round(buckets[cle]["kwh"] + r["kwh"], 4)
        buckets[cle]["pmax"] = round(max(buckets[cle]["pmax"], r["pmax"]), 3)

    # Fusion : base = données existantes, les nouvelles écrasent les doublons
    donnees_existantes = _lire_data() or {}
    existants = {r["ts"]: r for r in donnees_existantes.get("records", [])}
    existants.update(buckets)

    # Re-tri chronologique
    records_fusionnes = sorted(existants.values(), key=lambda r: r["ts"])
    donnees_fusionnees = {
        "records": records_fusionnes,
        "mock": donnees_existantes.get("mock", False),
    }
    _ecrire_data(donnees_fusionnees)

    logger.info(
        "API conso IMPORT_CSV : %d lignes → %d records 30min, %d ignorés, total %d",
        len(nouveaux), len(buckets), lignes_ignorees, len(records_fusionnes)
    )
    return _reponse(True, "IMPORT_CSV", data={
        "importes": len(buckets),
        "ignores": lignes_ignorees,
        "total": len(records_fusionnes),
        "records": records_fusionnes,
        "mock": donnees_fusionnees["mock"],
    })


# ─── Point d'entrée du script ─────────────────────────────────────────────────

COMMANDES = {
    "GET":               cmd_get,
    "UPDATE":            cmd_update,
    "GET_PARAM":         cmd_get_param,
    "SET_PARAM":         cmd_set_param,
    "INIT_PARAM":        cmd_init_param,
    "GET_HISTORY_START": cmd_get_history_start,
    "IMPORT_CSV":        cmd_import_csv,
    "GET_DATA":          cmd_get_data,
    "GET_MASKS":         cmd_get_masks,
}


def run(mode: str, params: dict = None, global_param: dict = None) -> dict:
    """Point d'entrée appelé par orchestrator.py ou server.py."""
    params = params or {}
    global_param = global_param if global_param is not None else {}

    if mode not in COMMANDES:
        return _reponse(False, mode, error=f"Mode inconnu : {mode}")

    return COMMANDES[mode](params, global_param)


if __name__ == "__main__":
    import sys
    mode_cli = sys.argv[1] if len(sys.argv) > 1 else "GET"
    print(json.dumps(run(mode_cli), indent=2, ensure_ascii=False))
