"""
solaire.py — Module complémentaire Solaire.
Collecte et stocke des données brutes (luminosité/puissance) depuis des CSV uploadés
ou des scripts Python personnalisés exécutés périodiquement.

Chaque source est indépendante : type (lux/W/autre), coefficient de conversion,
script optionnel avec intervalle configurable.
Les données sont fusionnées à l'import : même timestamp → remplacement, nouveau → ajout.
Les données sont toujours triées du plus ancien au plus récent.
"""

import importlib.util
import json
import logging
import re
import sys as _sys
import threading
import unicodedata
from bisect import bisect_left
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("solaire")
TZ_PARIS = ZoneInfo("Europe/Paris")
BASE_DIR = Path(__file__).parent

_lock = threading.RLock()

# ─── Chargement dynamique de parametres.py ────────────────────────────────────

_param_spec = importlib.util.spec_from_file_location(
    "solaire_parametres", BASE_DIR / "parametres.py"
)
_parametres = importlib.util.module_from_spec(_param_spec)
_sys.modules["solaire_parametres"] = _parametres
_param_spec.loader.exec_module(_parametres)


# ─── Helpers réponse ──────────────────────────────────────────────────────────

def _reponse(status: bool, mode: str, data=None, error=None) -> dict:
    """Construit la réponse standardisée."""
    return {"status": status, "mode": mode, "error": error, "data": data or {}}


# ─── Configuration panneau ────────────────────────────────────────────────────

PANNEAU_DEFAUT = {
    "cout_panneau":       900.0,
    "puissance_wc":       425,
    "performance_ratio":  80.0,
    "degradation_pct_an": 0.5,
    "duree_vie_ans":      25,
    "nb_panneaux_max":    20,
}


def _fichier_panneau() -> Path:
    return BASE_DIR / _parametres.FICHIER_PANNEAU


def _charger_panneau() -> dict:
    """Retourne la configuration panneau. Valeurs par défaut si absent ou corrompu."""
    f = _fichier_panneau()
    if not f.exists():
        return dict(PANNEAU_DEFAUT)
    try:
        with open(f, encoding="utf-8") as fp:
            data = json.load(fp)
        result = dict(PANNEAU_DEFAUT)
        # Ne conserver que les clés connues (ignore les clés supprimées)
        result.update({k: v for k, v in data.items() if k in PANNEAU_DEFAUT})
        return result
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("solaire : fichier panneau corrompu (%s), valeurs par défaut", exc)
        return dict(PANNEAU_DEFAUT)


def _sauvegarder_panneau(panneau: dict):
    """Persiste la configuration panneau."""
    f = _fichier_panneau()
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(panneau, fp, ensure_ascii=False, indent=2)


# ─── Persistance sources (configuration) ──────────────────────────────────────

def _fichier_sources() -> Path:
    return BASE_DIR / _parametres.FICHIER_SOURCES


def _charger_sources() -> List[dict]:
    """Retourne la liste des sources configurées. [] si absent ou corrompu."""
    f = _fichier_sources()
    if not f.exists():
        return []
    try:
        with open(f, encoding="utf-8") as fp:
            return json.load(fp)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("solaire : fichier sources corrompu (%s), réinitialisation", exc)
        backup = f.with_suffix(".json.bak")
        f.rename(backup)
        logger.warning("solaire : sauvegarde dans %s", backup)
        return []


def _sauvegarder_sources(sources: List[dict]):
    """Persiste la liste des sources (sans les champs de runtime)."""
    champs_runtime = {"total_points", "premier_ts", "dernier_ts"}
    sources_clean = [
        {k: v for k, v in src.items() if k not in champs_runtime}
        for src in sources
    ]
    f = _fichier_sources()
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(sources_clean, fp, ensure_ascii=False, indent=2)


# ─── Persistance données (séries temporelles) ─────────────────────────────────

def _fichier_donnees() -> Path:
    return BASE_DIR / _parametres.FICHIER_DONNEES


def _donnees_vides() -> dict:
    return {
        "meta": {
            "module": "solaire",
            "version": "1.0",
            "premier_enregistrement": None,
            "dernier_enregistrement": None,
            "total_enregistrements": 0,
        },
        "sources": {},
    }


def _charger_donnees() -> dict:
    """Lit le fichier JSON local. Retourne une structure vide si absent ou corrompu."""
    f = _fichier_donnees()
    if not f.exists():
        return _donnees_vides()
    try:
        with open(f, encoding="utf-8") as fp:
            return json.load(fp)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("solaire : fichier données corrompu (%s), réinitialisation", exc)
        backup = f.with_suffix(".json.bak")
        f.rename(backup)
        logger.warning("solaire : sauvegarde dans %s", backup)
        return _donnees_vides()


def _sauvegarder_donnees(donnees: dict):
    """Écrit le fichier JSON local. Met à jour les métadonnées globales."""
    f = _fichier_donnees()
    f.parent.mkdir(parents=True, exist_ok=True)
    # Recalculer les métadonnées globales
    all_ts: List[str] = []
    total = 0
    for src_data in donnees.get("sources", {}).values():
        pts = src_data.get("points", [])
        total += len(pts)
        if pts:
            all_ts.append(pts[0]["ts"])
            all_ts.append(pts[-1]["ts"])
    if all_ts:
        all_ts_sorted = sorted(all_ts)
        donnees["meta"]["premier_enregistrement"] = all_ts_sorted[0]
        donnees["meta"]["dernier_enregistrement"] = all_ts_sorted[-1]
    donnees["meta"]["total_enregistrements"] = total
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(donnees, fp, ensure_ascii=False, indent=2)


# ─── Slugification ───────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """
    Convertit un texte quelconque en identifiant ASCII snake_case stable.
    Exemple : "Luminosité est (lx)" → "luminosite_est_lx"
    """
    # Décomposer les caractères accentués et supprimer les diacritiques
    nfd = unicodedata.normalize("NFD", text)
    ascii_text = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    # Minuscule, remplacer tout ce qui n'est pas alphanumérique par underscore
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower())
    return slug.strip("_")


# ─── Fusion des données ───────────────────────────────────────────────────────

def _fusionner_points(existants: List[dict], nouveaux: List[dict]) -> List[dict]:
    """
    Fusionne deux listes de points {"ts": str_iso, "valeur": float}.
    Même ts → le nouveau remplace l'ancien.
    Résultat trié par ts croissant (du plus ancien au plus récent).
    """
    index = {p["ts"]: p for p in existants}
    for p in nouveaux:
        index[p["ts"]] = p
    return sorted(index.values(), key=lambda p: p["ts"])


# ─── Import de points ─────────────────────────────────────────────────────────

def _importer_points(source_id: str, nouveaux_points: List[dict]) -> int:
    """Fusionne les nouveaux points dans les données persistées de la source."""
    with _lock:
        donnees = _charger_donnees()
        src_data = donnees["sources"].setdefault(source_id, {"points": []})
        avant = len(src_data.get("points", []))
        src_data["points"] = _fusionner_points(src_data.get("points", []), nouveaux_points)
        apres = len(src_data["points"])
        _sauvegarder_donnees(donnees)
    logger.info("solaire %s : %d→%d points (%d importés)", source_id, avant, apres, len(nouveaux_points))
    return len(nouveaux_points)


# ─── Parsing CSV ──────────────────────────────────────────────────────────────

def _detecter_unite(nom_colonne: str) -> Optional[str]:
    """
    Détecte l'unité depuis le libellé de la colonne CSV.
    Retourne "lux", "W", "kW", "kWh" ou None si non reconnue.
    L'ordre de test est important : kWh avant kW avant W.
    """
    if re.search(r'kWh', nom_colonne) or re.search(r'kwh', nom_colonne, re.IGNORECASE):
        return "kWh"
    if re.search(r'kW\b', nom_colonne) or re.search(r'\bkw\b', nom_colonne, re.IGNORECASE):
        return "kW"
    if re.search(r'(?i)\b(lux|lx)\b', nom_colonne) or "(lx)" in nom_colonne.lower():
        return "lux"
    # W seul — ne pas matcher si précédé de k
    if re.search(r'(?<![kK])\bW\b', nom_colonne):
        return "W"
    return None


_COEFF_DEFAUT = {
    "lux":  128000,
    "W":    1000,
    "kW":   1,
    "kWh":  1,
    "autre": 1,
}


def _parser_csv(contenu_csv: str) -> Tuple[str, List[dict], int]:
    """
    Parse un CSV avec en-tête datetime,<colonne>.
    Accepte les valeurs entre guillemets doubles. Timezone Europe/Paris si absente.
    Retourne (nom_colonne, points_valides, nb_lignes_ignorees).
    Le nom de colonne (en-tête col 2) sert à identifier la source.
    Lève ValueError si le fichier n'a pas le bon format.
    """
    import csv
    reader = csv.reader(StringIO(contenu_csv))
    header = next(reader, None)
    if not header or len(header) < 2:
        raise ValueError("CSV invalide : en-tête attendu 'datetime,<valeur>'")

    nom_colonne = header[1].strip().strip('"')

    points   = []
    ignores  = 0
    for row in reader:
        # Ignorer les lignes vides
        if not any(c.strip() for c in row):
            continue
        if len(row) < 2:
            ignores += 1
            continue
        ts_brut  = row[0].strip().strip('"')
        val_brut = row[1].strip().strip('"')
        if not ts_brut or not val_brut:
            ignores += 1
            continue
        try:
            dt = datetime.fromisoformat(ts_brut)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ_PARIS)
            points.append({"ts": dt.isoformat(), "valeur": float(val_brut)})
        except Exception:
            ignores += 1

    return nom_colonne, sorted(points, key=lambda p: p["ts"]), ignores


# ─── Exécution script utilisateur ────────────────────────────────────────────

def _executer_script(code_script: str) -> List[dict]:
    """
    Exécute le script utilisateur dans un namespace isolé.
    Le script doit définir une variable `resultats` = liste de dicts avec les clés :
      - "ts" (ou "datetime" / "date_heure") : horodatage ISO
      - "valeur" (ou "value") : valeur numérique
    Timezone Europe/Paris appliquée si absente.
    Retourne une liste de {"ts": str_iso, "valeur": float}.
    """
    if not code_script or not code_script.strip():
        raise ValueError("Script vide")

    namespace: dict = {}
    try:
        exec(compile(code_script, "<solaire_script>", "exec"), namespace)  # nosec
    except Exception as exc:
        raise ValueError("Erreur d'exécution : {}".format(exc))

    resultats = namespace.get("resultats")
    if resultats is None:
        raise ValueError("Le script doit définir une variable `resultats`")
    if not isinstance(resultats, list):
        raise ValueError("`resultats` doit être une liste")

    points = []
    for r in resultats:
        try:
            ts_brut = (
                r.get("ts") or r.get("datetime") or r.get("date_heure") or ""
            )
            valeur = float(r.get("valeur") or r.get("value") or 0)
            dt = datetime.fromisoformat(str(ts_brut))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ_PARIS)
            points.append({"ts": dt.isoformat(), "valeur": valeur})
        except Exception:
            continue

    return sorted(points, key=lambda p: p["ts"])


# ─── Commandes ────────────────────────────────────────────────────────────────

def cmd_get(params: dict, global_param: dict) -> dict:
    """Retourne les sources avec leurs statistiques de données."""
    donnees = _charger_donnees()
    sources = _charger_sources()
    result = []
    for src in sources:
        sid = src["id"]
        pts = donnees.get("sources", {}).get(sid, {}).get("points", [])
        result.append({
            **src,
            "total_points": len(pts),
            "premier_ts": pts[0]["ts"] if pts else None,
            "dernier_ts":  pts[-1]["ts"] if pts else None,
        })
    return _reponse(True, "GET", data={
        "sources": result,
        "meta":    donnees.get("meta", {}),
    })


def cmd_update(params: dict, global_param: dict) -> dict:
    """Lance les scripts dont l'intervalle est écoulé."""
    sources = _charger_sources()
    mises_a_jour: List[str] = []
    erreurs: List[dict] = []
    modifie = False

    for src in sources:
        script = src.get("script", "").strip()
        if not script:
            continue
        intervalle_h = float(src.get("script_intervalle_h", 24))
        derniere = src.get("script_derniere_exec")

        if derniere:
            try:
                dt_derniere = datetime.fromisoformat(derniere)
                if dt_derniere.tzinfo is None:
                    dt_derniere = dt_derniere.replace(tzinfo=TZ_PARIS)
                elapsed_h = (datetime.now(TZ_PARIS) - dt_derniere).total_seconds() / 3600
                if elapsed_h < intervalle_h:
                    continue
            except Exception:
                pass

        try:
            points = _executer_script(script)
            _importer_points(src["id"], points)
            src["script_derniere_exec"] = datetime.now(TZ_PARIS).isoformat()
            mises_a_jour.append(src["id"])
            modifie = True
        except Exception as exc:
            logger.warning("solaire UPDATE script %s : %s", src["id"], exc)
            erreurs.append({"id": src["id"], "erreur": str(exc)})

    if modifie:
        _sauvegarder_sources(sources)

    return _reponse(True, "UPDATE", data={
        "mises_a_jour": mises_a_jour,
        "erreurs":       erreurs,
    })


def cmd_import_csv(params: dict, global_param: dict) -> dict:
    """
    Importe un CSV et détecte automatiquement la source depuis l'en-tête de colonne.
    - L'id est généré par slugification du nom de colonne (stable, ne change pas).
    - Le nom affiché est le libellé exact de l'en-tête (renommable par l'utilisateur).
    - Si la source n'existe pas encore, elle est créée avec des valeurs par défaut.
    - Si elle existe déjà, les données sont fusionnées (même ts → remplacement).
    """
    contenu = params.get("contenu", "")
    if not contenu:
        return _reponse(False, "IMPORT_CSV", error="Contenu CSV requis")
    try:
        nom_colonne, points, ignores = _parser_csv(contenu)
    except Exception as exc:
        return _reponse(False, "IMPORT_CSV", error=str(exc))

    if not points:
        return _reponse(False, "IMPORT_CSV", error="Aucun point valide trouvé dans le CSV")

    source_id = _slugify(nom_colonne)
    if not source_id:
        return _reponse(False, "IMPORT_CSV", error="Impossible de générer un identifiant depuis l'en-tête : '{}'".format(nom_colonne))

    # Détection de l'unité : paramètre explicite > en-tête CSV > demande à l'utilisateur
    unite_param = params.get("unite", "").strip()
    unite_detectee = _detecter_unite(nom_colonne)
    unite = unite_param or unite_detectee

    if not unite:
        # Unité inconnue et non fournie : on interrompt pour demander à l'utilisateur
        return _reponse(True, "IMPORT_CSV", data={
            "unite_requise": True,
            "source_id":     source_id,
            "source_nom":    nom_colonne,
            "importes":      0,
        })

    # Normaliser "autre" + valeur libre
    type_source = unite if unite in _COEFF_DEFAUT else "autre"
    coeff_defaut = _COEFF_DEFAUT.get(type_source, 1)

    # Chercher la source existante ou en créer une nouvelle.
    # Protégé par _lock pour éviter les races lors d'imports parallèles
    # (lecture + écriture atomiques sur solaire_sources.json).
    with _lock:
        sources = _charger_sources()
        src     = next((s for s in sources if s["id"] == source_id), None)
        creee   = src is None
        if creee:
            src = {
                "id":                   source_id,
                "nom":                  nom_colonne,
                "type":                 type_source,
                "unite":                unite,
                "coeff":                coeff_defaut,
                "script":               "",
                "script_intervalle_h":  24,
                "script_derniere_exec": None,
            }
            sources.append(src)
            _sauvegarder_sources(sources)
            logger.info("solaire : nouvelle source créée '%s' (id=%s, unite=%s)", nom_colonne, source_id, unite)

    nb = _importer_points(source_id, points)  # _importer_points acquiert lui-même _lock
    return _reponse(True, "IMPORT_CSV", data={
        "importes":   nb,
        "ignores":    ignores,
        "source_id":  source_id,
        "source_nom": src["nom"],
        "unite":      unite,
        "creee":      creee,
    })


def cmd_add_point(params: dict, global_param: dict) -> dict:
    """Ajoute manuellement un point à une source."""
    source_id = params.get("source_id", "")
    ts        = params.get("ts", "")
    valeur    = params.get("valeur")
    if not source_id or not ts or valeur is None:
        return _reponse(False, "ADD_POINT", error="source_id, ts et valeur requis")
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_PARIS)
        point = {"ts": dt.isoformat(), "valeur": float(valeur)}
        _importer_points(source_id, [point])
        return _reponse(True, "ADD_POINT", data=point)
    except Exception as exc:
        return _reponse(False, "ADD_POINT", error=str(exc))


def cmd_run_script(params: dict, global_param: dict) -> dict:
    """Exécute immédiatement le script d'une source (sans vérification d'intervalle)."""
    source_id = params.get("source_id", "")
    sources   = _charger_sources()
    src = next((s for s in sources if s["id"] == source_id), None)
    if not src:
        return _reponse(False, "RUN_SCRIPT", error="Source '{}' introuvable".format(source_id))
    try:
        points = _executer_script(src.get("script", ""))
        _importer_points(source_id, points)
        src["script_derniere_exec"] = datetime.now(TZ_PARIS).isoformat()
        _sauvegarder_sources(sources)
        return _reponse(True, "RUN_SCRIPT", data={"importes": len(points)})
    except Exception as exc:
        return _reponse(False, "RUN_SCRIPT", error=str(exc))


def cmd_get_data(params: dict, global_param: dict) -> dict:
    """Retourne les points d'une source, paginés, du plus récent au plus ancien."""
    source_id = params.get("source_id", "")
    limit     = int(params.get("limit", 200))
    offset    = int(params.get("offset", 0))
    donnees   = _charger_donnees()
    pts       = donnees.get("sources", {}).get(source_id, {}).get("points", [])
    # Affichage du plus récent au plus ancien pour la visualisation
    pts_desc  = list(reversed(pts))
    page      = pts_desc[offset: offset + limit]
    return _reponse(True, "GET_DATA", data={
        "source_id": source_id,
        "total":     len(pts),
        "offset":    offset,
        "limit":     limit,
        "points":    page,
    })


def cmd_get_param(params: dict, global_param: dict) -> dict:
    sources = _charger_sources()
    panneau = _charger_panneau()
    return _reponse(True, "GET_PARAM", data={"sources": sources, "panneau": panneau})


def cmd_set_param(params: dict, global_param: dict) -> dict:
    sources = params.get("sources")
    panneau = params.get("panneau")
    if sources is None and panneau is None:
        return _reponse(False, "SET_PARAM", error="Champ 'sources' ou 'panneau' requis")
    if sources is not None:
        _sauvegarder_sources(sources)
    if panneau is not None:
        _sauvegarder_panneau(panneau)
    return _reponse(True, "SET_PARAM", data={
        "sources": _charger_sources(),
        "panneau": _charger_panneau(),
    })


def cmd_init_param(params: dict, global_param: dict) -> dict:
    if not _fichier_sources().exists():
        _sauvegarder_sources([])
    if not _fichier_panneau().exists():
        _sauvegarder_panneau(dict(PANNEAU_DEFAUT))
    sources = _charger_sources()
    panneau = _charger_panneau()
    return _reponse(True, "INIT_PARAM", data={"sources": sources, "panneau": panneau})


def _calculer_creneau(
    pts: List[tuple], dts: List[datetime],
    t_debut: datetime, t_fin: datetime, coeff: float
) -> Optional[tuple]:
    """
    Calcule le lux moyen pondéré et le kWh estimé pour un créneau [t_debut, t_fin].

    pts  : liste de (datetime, float) triée par ts (pré-calculée)
    dts  : liste des datetimes extraite de pts (pour bisect)
    Règle 60 min : une valeur mesurée à T est valide jusqu'à T+3600 s ;
    au-delà elle est remplacée par 0 jusqu'au prochain point.
    Retourne (avg_lux, kwh) ou None si aucune donnée disponible (affichage « — »).
    """
    SEUIL_S = 3600.0
    DUREE   = (t_fin - t_debut).total_seconds()   # 1800 s

    idx_debut = bisect_left(dts, t_debut)
    idx_fin   = bisect_left(dts, t_fin)
    idx_avant = idx_debut - 1

    pts_dans = pts[idx_debut:idx_fin]

    if idx_avant < 0 and not pts_dans:
        return None

    # Construction de la timeline : (t_debut_segment, lux, t_mesure)
    entrees: List[tuple] = []
    if idx_avant >= 0:
        dt_av, lux_av = pts[idx_avant]
        entrees.append((t_debut, lux_av, dt_av))
    else:
        # Pas de point avant → valeur 0 déjà expirée depuis longtemps
        entrees.append((t_debut, 0.0, t_debut - timedelta(seconds=SEUIL_S + 1)))

    for dt, lux in pts_dans:
        entrees.append((dt, lux, dt))

    total_lux_s = 0.0
    for i in range(len(entrees)):
        t_seg, lux, t_mesure = entrees[i]
        t_next = entrees[i + 1][0] if i + 1 < len(entrees) else t_fin

        t_s = max(t_seg,  t_debut)
        t_e = min(t_next, t_fin)
        if t_e <= t_s:
            continue

        t_expire = t_mesure + timedelta(seconds=SEUIL_S)

        if t_expire <= t_s:
            pass                                             # expiré → 0
        elif t_expire >= t_e:
            total_lux_s += lux * (t_e - t_s).total_seconds()
        else:
            total_lux_s += lux * (t_expire - t_s).total_seconds()
            # de t_expire à t_e : 0 (pas besoin d'additionner)

    avg_lux = total_lux_s / DUREE
    kwh = (avg_lux / coeff) * 0.5
    return (round(avg_lux, 1), round(kwh, 4))


def cmd_get_creneaux(params: dict, global_param: dict) -> dict:
    """
    Calcule les créneaux 30 min pour chaque source solaire.
    Paramètre ts_list : liste de timestamps ISO correspondant aux créneaux Enedis.
    Retourne {sources: [{id, nom, coeff}], creneaux: {ts: [{lux, kwh}|{lux:null,kwh:null}, …]}}.
    """
    ts_list = params.get("ts_list", [])
    if not ts_list:
        return _reponse(True, "GET_CRENEAUX", data={"sources": [], "creneaux": {}})

    sources = _charger_sources()
    if not sources:
        return _reponse(True, "GET_CRENEAUX", data={"sources": [], "creneaux": {}})

    donnees = _charger_donnees()

    # Pré-calculer les points par source (triés, avec liste de datetimes pour bisect)
    pts_par_source: dict = {}
    for src in sources:
        pts_raw = donnees.get("sources", {}).get(src["id"], {}).get("points", [])
        pts: List[tuple] = []
        for p in pts_raw:
            dt = datetime.fromisoformat(p["ts"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ_PARIS)
            pts.append((dt, float(p["valeur"])))
        pts.sort(key=lambda x: x[0])
        dts = [p[0] for p in pts]
        pts_par_source[src["id"]] = (pts, dts)

    creneaux: dict = {}
    for ts_str in ts_list:
        t_debut = datetime.fromisoformat(ts_str)
        if t_debut.tzinfo is None:
            t_debut = t_debut.replace(tzinfo=TZ_PARIS)
        t_fin = t_debut + timedelta(minutes=30)

        vals = []
        for src in sources:
            coeff = float(src.get("coeff") or 128000)
            pts, dts = pts_par_source.get(src["id"], ([], []))
            result = _calculer_creneau(pts, dts, t_debut, t_fin, coeff)
            if result is None:
                vals.append({"lux": None, "kwh": None})
            else:
                avg_lux, kwh = result
                vals.append({"lux": avg_lux, "kwh": kwh})

        creneaux[ts_str] = vals

    sources_info = [
        {"id": s["id"], "nom": s["nom"], "coeff": s.get("coeff", 128000)}
        for s in sources
    ]

    logger.info(
        "solaire GET_CRENEAUX : %d créneaux × %d sources",
        len(creneaux), len(sources_info),
    )
    return _reponse(True, "GET_CRENEAUX", data={
        "sources":  sources_info,
        "creneaux": creneaux,
    })


def cmd_merge_sources(params: dict, global_param: dict) -> dict:
    """
    Fusionne deux sources en une seule.
    - source_id_cible   : source qui absorbe les données (conservée)
    - source_id_fusionner : source dont les données sont absorbées (supprimée)
    Les points sont fusionnés avec la règle habituelle (même ts → remplacement par cible).
    La configuration (nom, type, coeff, script) de la cible est conservée.
    """
    id_cible     = params.get("source_id_cible", "")
    id_fusionner = params.get("source_id_fusionner", "")

    if not id_cible or not id_fusionner:
        return _reponse(False, "MERGE_SOURCES", error="source_id_cible et source_id_fusionner requis")
    if id_cible == id_fusionner:
        return _reponse(False, "MERGE_SOURCES", error="Les deux sources doivent être différentes")

    sources = _charger_sources()
    ids_connus = {s["id"] for s in sources}
    if id_cible not in ids_connus:
        return _reponse(False, "MERGE_SOURCES", error="Source cible '{}' introuvable".format(id_cible))
    if id_fusionner not in ids_connus:
        return _reponse(False, "MERGE_SOURCES", error="Source à fusionner '{}' introuvable".format(id_fusionner))

    with _lock:
        donnees = _charger_donnees()
        pts_cible     = donnees.get("sources", {}).get(id_cible,     {}).get("points", [])
        pts_fusionner = donnees.get("sources", {}).get(id_fusionner, {}).get("points", [])

        # Fusion : la cible gagne en cas de conflit de timestamp
        pts_merged = _fusionner_points(pts_fusionner, pts_cible)

        donnees["sources"].setdefault(id_cible, {})["points"] = pts_merged
        # Supprimer les données de la source absorbée
        donnees["sources"].pop(id_fusionner, None)
        _sauvegarder_donnees(donnees)

    # Supprimer la source absorbée de la config
    sources = [s for s in sources if s["id"] != id_fusionner]
    _sauvegarder_sources(sources)

    logger.info("solaire : fusion %s ← %s (%d points)", id_cible, id_fusionner, len(pts_merged))
    return _reponse(True, "MERGE_SOURCES", data={
        "source_id":    id_cible,
        "total_points": len(pts_merged),
        "supprimee":    id_fusionner,
    })


def recharger_parametres():
    """Recharge parametres.py sans redémarrer l'application."""
    _param_spec.loader.exec_module(_parametres)
    logger.info("solaire : parametres.py rechargé")


# ─── Point d'entrée ──────────────────────────────────────────────────────────

_COMMANDES = {
    "GET":           cmd_get,
    "UPDATE":        cmd_update,
    "GET_CRENEAUX":  cmd_get_creneaux,
    "MERGE_SOURCES": cmd_merge_sources,
    "GET_PARAM":  cmd_get_param,
    "SET_PARAM":  cmd_set_param,
    "INIT_PARAM": cmd_init_param,
    "IMPORT_CSV": cmd_import_csv,
    "ADD_POINT":  cmd_add_point,
    "RUN_SCRIPT": cmd_run_script,
    "GET_DATA":   cmd_get_data,
}


def run(mode: str, params: Optional[dict] = None, global_param: Optional[dict] = None) -> dict:
    if params is None:
        params = {}
    if global_param is None:
        global_param = {}
    fn = _COMMANDES.get(mode)
    if fn is None:
        return _reponse(False, mode, error="Mode inconnu : {}".format(mode))
    try:
        return fn(params, global_param)
    except Exception as exc:
        logger.error("solaire mode %s : %s", mode, exc, exc_info=True)
        return _reponse(False, mode, error=str(exc))
