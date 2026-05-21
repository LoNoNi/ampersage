"""
generique.py - Module tarif générique multi-fournisseurs.

Scanne les fichiers tarif_*.json dans scripts/tarif/generique/tarif/ et
les répertoires fournisseur_*/ du même dossier.

Modes :
  GET         → retourne generique_data.json (legacy)
  UPDATE      → scanne les tarifs, calcule, écrit les fichiers séparés
  GET_PARAM   → retourne generique_param.json + index des offres
  SET_PARAM   → sauvegarde les paramètres utilisateur
  INIT_PARAM  → recrée generique_param.json avec valeurs par défaut
  GET_MASKS   → retourne generique_masques.json (masques HTML par offre)
  GET_CONFIG  → retourne generique_config.json (params + structure tarifaire partielle)
  GET_TARIF   → retourne generique_tarif_<slug>.json pour une offre (params: offre_id)

Fichiers écrits par UPDATE :
  generique_masques.json          → masques HTML par offre_id
  generique_config.json           → params + abonnements/prix kWh (sans répartition)
  generique_tarif_<slug>.json     → créneaux calculés par offre

Les offres de type 'custom' sont ignorées pour l'instant.

Unités dans les fichiers tarif_*.json :
  abonnements.ht/ttc  : €/mois
  kwh.ht/ttc          : €/kWh  (différent des modules trv_* qui utilisent c€/kWh)
"""

import importlib.util
import json
import logging
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple  # Tuple conservé pour _parser_plage_hc
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from _base_tarif import BaseTarif, _reponse

BASE_DIR      = Path(__file__).parent
TARIF_DIR     = BASE_DIR / "tarif"       # scripts/tarif/generique/tarif/
TEMPLATES_DIR = BASE_DIR / "templates"   # masques génériques (fallback)
TZ_PARIS      = ZoneInfo("Europe/Paris")

MASQUES_FILE = BASE_DIR / "generique_masques.json"
CONFIG_FILE  = BASE_DIR / "generique_config.json"

logger = logging.getLogger("generique")

_PARAM_DEFAUT = {
    "puissance_souscrite": None,   # None → déduction automatique depuis pmax
    "mode_prix": "ttc",
    "plage_hc_globale": "",        # vide → popup demandé à l'utilisateur
    "offres_plage_hc": {},
}


# ─── Parseurs plage HC ────────────────────────────────────────────────────────

def _parser_plage_hc(plage_hc):
    # type: (str) -> List[Tuple[int, int]]
    """Parse "22h00→6h00 + 12h00→14h00" → liste de tuples (debut_min, fin_min)."""
    segments = []
    for segment in plage_hc.split("+"):
        segment = segment.strip()
        if "\u2192" not in segment:
            continue
        debut_str, fin_str = segment.split("\u2192")

        def _hm(s):
            s = s.strip().replace("h", ":")
            h, m = s.split(":")
            return int(h) * 60 + int(m)

        segments.append((_hm(debut_str), _hm(fin_str)))
    return segments


def _est_hc(dt, creneaux_hc):
    # type: (datetime, List[Tuple[int, int]]) -> bool
    """Retourne True si l'heure dt tombe dans un créneau HC."""
    t = dt.hour * 60 + dt.minute
    for debut, fin in creneaux_hc:
        if debut <= fin:
            if debut <= t < fin:
                return True
        else:
            # Créneau traversant minuit (ex: 22h00→6h00)
            if t >= debut or t < fin:
                return True
    return False


# ─── Déduction de la puissance souscrite ─────────────────────────────────────

def _deduire_kva(pmax_kw, abonnements):
    # type: (float, List[Dict]) -> int
    """
    Retourne le plus petit kVA disponible dans la grille d'abonnements
    qui couvre la puissance observée (pmax_kw).
    Si pmax_kw dépasse tous les paliers, retourne le plus grand disponible.
    """
    kvas = sorted(a["kva"] for a in abonnements if "kva" in a)
    if not kvas:
        return 6
    for kva in kvas:
        if kva >= pmax_kw:
            return kva
    return kvas[-1]


# ─── Chargement dynamique de modules custom ──────────────────────────────────

def _charger_module_custom(rep_module, nom_module):
    # type: (str, str) -> Optional[object]
    """Charge dynamiquement un module Python custom depuis son répertoire."""
    chemin = Path(rep_module) / "{}.py".format(nom_module)
    if not chemin.exists():
        logger.warning("Module custom introuvable : %s", chemin)
        return None
    try:
        spec   = importlib.util.spec_from_file_location(nom_module, chemin)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        logger.warning("Erreur chargement module custom %s : %s", nom_module, exc)
        return None


# ─── Scanner les fichiers tarif ───────────────────────────────────────────────

def _scanner_tarifs(tarif_dir):
    # type: (Path) -> List[Dict]
    """
    Scanne tarif_dir/tarif_*.json       (source=generique)
    et    tarif_dir/fournisseur_*/tarif_*.json  (source=dédié).
    Retourne une liste de dicts {source, fichier, data}.
    """
    resultats = []

    if not tarif_dir.exists():
        return resultats

    # Fichiers génériques plats
    for f in sorted(tarif_dir.glob("tarif_*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            resultats.append({"source": "generique", "fichier": f.name, "data": data})
        except Exception as e:
            logger.warning("Erreur lecture %s : %s", f, e)

    # Répertoires dédiés fournisseur_*
    for rep in sorted(tarif_dir.iterdir()):
        if rep.is_dir() and rep.name.startswith("fournisseur_"):
            for f in sorted(rep.glob("tarif_*.json")):
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    resultats.append({
                        "source":  "dédié",
                        "fichier": str(f.relative_to(tarif_dir)),
                        "data":    data,
                    })
                except Exception as e:
                    logger.warning("Erreur lecture %s : %s", f, e)

    # Répertoires frères de generique (scripts/tarif/*/tarif_*.json)
    # Contiennent les modules custom : chaque répertoire est un module autonome.
    scripts_tarif_dir = tarif_dir.parent.parent  # scripts/tarif/
    generique_dir     = tarif_dir.parent          # scripts/tarif/generique/ — déjà traité
    if scripts_tarif_dir.exists():
        for module_dir in sorted(scripts_tarif_dir.iterdir()):
            if not module_dir.is_dir():
                continue
            if module_dir.name.startswith("_") or module_dir == generique_dir:
                continue
            for f in sorted(module_dir.glob("tarif_*.json")):
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    resultats.append({
                        "source":     "custom",
                        "fichier":    str(f),        # chemin absolu (module hors generique)
                        "rep_module": str(module_dir),
                        "data":       data,
                    })
                except Exception as e:
                    logger.warning("Erreur lecture %s : %s", f, e)

    return resultats


# ─── Index persistant ─────────────────────────────────────────────────────────

INDEX_FILE = BASE_DIR / "generique_index.json"


def _lire_index():
    # type: () -> Dict
    if not INDEX_FILE.exists():
        return {"next_id": 0, "offres": {}}
    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _ecrire_index(index):
    # type: (Dict) -> None
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _mettre_a_jour_index(tarifs):
    # type: (List[Dict]) -> Dict
    """
    Ajoute les nouvelles offres à l'index sans jamais modifier les entrées existantes.
    L'identifiant global est stable : un ajout de fournisseur ne réindexe pas l'existant.
    """
    index   = _lire_index()
    offres  = index["offres"]
    next_id = index["next_id"]
    modifie = False

    for tarif in tarifs:
        data        = tarif["data"]
        fournisseur = data.get("fournisseur", "")

        for i, offre in enumerate(data.get("offres", [])):
            nom        = offre.get("nom", "")
            type_offre = offre.get("type", "")

            existant = next(
                (k for k, v in offres.items()
                 if v["fournisseur"] == fournisseur
                 and v["nom"]        == nom
                 and v["type"]       == type_offre),
                None,
            )

            if existant is None:
                cle = "{}__{}__{}__{:d}".format(fournisseur, nom, type_offre, next_id)
                entree = {
                    "fournisseur":    fournisseur,
                    "nom":            nom,
                    "type":           type_offre,
                    "source":         tarif["source"],
                    "fichier":        tarif["fichier"],
                    "offre_index":    i,
                    "plage_hc_tarif": offre.get("plage_hc"),
                }
                if type_offre == "custom":
                    entree["module"]     = offre.get("module", "")
                    entree["rep_module"] = tarif.get("rep_module", "")
                    # Champs de configuration spécifiques à l'offre (ex: produit pour Sobry)
                    entree["offre_config"] = {
                        k: v for k, v in offre.items()
                        if k not in ("nom", "type", "module")
                    }
                offres[cle] = entree
                next_id += 1
                modifie  = True

    if modifie:
        index["next_id"] = next_id
        index["offres"]  = offres
        _ecrire_index(index)
        logger.info("generique : index mis à jour (%d offres au total)", len(offres))

    return index


# ─── Helpers lookup grille ────────────────────────────────────────────────────

def _trouver_abonnement(offre, kva, mode_prix):
    # type: (Dict, int, str) -> Optional[float]
    """Retourne l'abonnement €/mois pour le kVA exact, ou None."""
    for a in offre.get("abonnements", []):
        if a.get("kva") == kva:
            return a.get(mode_prix)
    return None


def _trouver_prix_base(offre, kva, mode_prix):
    # type: (Dict, int, str) -> Optional[float]
    """Retourne le prix kWh (€/kWh) pour une offre base au kVA donné."""
    for t in offre.get("kwh", []):
        if t.get("puissance_min_kva", 0) <= kva <= t.get("puissance_max_kva", 0):
            return t.get(mode_prix)
    return None


def _trouver_prix_hphc(offre, kva, mode_prix):
    # type: (Dict, int, str) -> Optional[Dict]
    """Retourne {"hp": float, "hc": float} en €/kWh pour une offre hphc au kVA donné."""
    for t in offre.get("kwh", []):
        if t.get("puissance_min_kva", 0) <= kva <= t.get("puissance_max_kva", 0):
            hp_key = "hp_{}".format(mode_prix)
            hc_key = "hc_{}".format(mode_prix)
            if hp_key in t and hc_key in t:
                return {"hp": t[hp_key], "hc": t[hc_key]}
    return None


# ─── Calcul des créneaux horaires ─────────────────────────────────────────────

def _creneaux_base(offre, records, kva, mode_prix):
    # type: (Dict, List[Dict], int, str) -> Dict
    """
    Agrège les records par heure et calcule le prix pour une offre base.
    Retourne {"creneaux": [...]} ou {"erreur": "..."}.
    """
    prix_kwh = _trouver_prix_base(offre, kva, mode_prix)
    if prix_kwh is None:
        return {"erreur": "Prix kWh introuvable pour {} kVA".format(kva)}

    heures = {}
    for r in records:
        dt  = datetime.fromisoformat(r["ts"]).astimezone(TZ_PARIS)
        cle = dt.replace(minute=0, second=0, microsecond=0)
        heures[cle] = heures.get(cle, 0.0) + r["kwh"]

    creneaux = []
    for dt_heure, kwh in sorted(heures.items()):
        kwh = round(kwh, 3)
        creneaux.append({
            "ts":       dt_heure.isoformat(),
            "kwh":      kwh,
            "prix_kwh": prix_kwh,
            "prix_eur": "{:.4f}".format(kwh * prix_kwh),
        })

    return {"creneaux": creneaux}


def _creneaux_hphc(offre, records, kva, mode_prix, plage_hc):
    # type: (Dict, List[Dict], int, str, str) -> Dict
    """
    Agrège les records par heure, affecte HC/HP et calcule le prix.
    Retourne {"creneaux": [...], "plage_hc_retenue": str} ou {"erreur": "..."}.
    """
    prix = _trouver_prix_hphc(offre, kva, mode_prix)
    if prix is None:
        return {"erreur": "Prix HP/HC introuvable pour {} kVA".format(kva)}

    seg_hc = _parser_plage_hc(plage_hc)
    heures = {}
    for r in records:
        dt  = datetime.fromisoformat(r["ts"]).astimezone(TZ_PARIS)
        cle = dt.replace(minute=0, second=0, microsecond=0)
        if cle not in heures:
            hc = _est_hc(cle, seg_hc)
            heures[cle] = {
                "kwh":      0.0,
                "type":     "HC" if hc else "HP",
                "prix_kwh": prix["hc"] if hc else prix["hp"],
            }
        heures[cle]["kwh"] += r["kwh"]

    creneaux = []
    for dt_heure, info in sorted(heures.items()):
        kwh = round(info["kwh"], 3)
        creneaux.append({
            "ts":       dt_heure.isoformat(),
            "kwh":      kwh,
            "type":     info["type"],
            "prix_kwh": info["prix_kwh"],
            "prix_eur": "{:.4f}".format(kwh * info["prix_kwh"]),
        })

    return {"creneaux": creneaux}


# ─── Masques d'affichage ─────────────────────────────────────────────────────


def _slugifier(s):
    # type: (str) -> str
    """Convertit une chaîne en slug ASCII minuscule (pour nommage de fichiers)."""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _resoudre_masque(nom_masque, fournisseur_slug=None, offre_slug=None):
    # type: (str, Optional[str], Optional[str]) -> str
    """
    Résout un masque HTML par ordre de priorité :
      1. tarif/fournisseur_{slug}/{nom}_{offre_slug}.html  (offre précise)
      2. tarif/fournisseur_{slug}/{nom}.html               (commun au fournisseur)
      3. templates/{nom}.html                              (générique fallback)
    Retourne le contenu HTML ou chaîne vide si aucun fichier trouvé.
    Les placeholders Python {offre_id} et {offres} sont remplacés par l'assembleur.
    Les placeholders JS {{varname}} sont laissés intacts pour le client.
    """
    candidats = []
    if fournisseur_slug and offre_slug:
        candidats.append(
            TARIF_DIR / "fournisseur_{}".format(fournisseur_slug)
            / "{}_{}.html".format(nom_masque, offre_slug)
        )
    if fournisseur_slug:
        candidats.append(
            TARIF_DIR / "fournisseur_{}".format(fournisseur_slug)
            / "{}.html".format(nom_masque)
        )
    candidats.append(TEMPLATES_DIR / "{}.html".format(nom_masque))

    for c in candidats:
        if c.exists():
            return c.read_text(encoding="utf-8")
    return ""


def _construire_lignes_params(offre, type_offre, mode_prix, fournisseur_slug, offre_slug):
    # type: (Dict, str, str, str, str) -> str
    """Génère le HTML des lignes params : une ligne par kVA disponible à la souscription."""
    ligne_tpl = (
        _resoudre_masque("params_ligne_{}".format(type_offre), fournisseur_slug, offre_slug)
        or _resoudre_masque("params_ligne", fournisseur_slug, offre_slug)
    )
    if not ligne_tpl:
        return ""

    lignes = []
    for a in sorted(offre.get("abonnements", []), key=lambda x: x.get("kva", 0)):
        kva        = a.get("kva", 0)
        abonnement = a.get(mode_prix, "")

        if type_offre == "base":
            prix_kwh = _trouver_prix_base(offre, kva, mode_prix)
            if prix_kwh is None:
                continue
            lignes.append(
                ligne_tpl
                .replace("{kva}",        str(kva))
                .replace("{abonnement}", str(abonnement))
                .replace("{prix_kwh}",   str(prix_kwh))
            )
        elif type_offre == "hphc":
            prix = _trouver_prix_hphc(offre, kva, mode_prix)
            if prix is None:
                continue
            lignes.append(
                ligne_tpl
                .replace("{kva}",        str(kva))
                .replace("{abonnement}", str(abonnement))
                .replace("{prix_hp}",    str(prix["hp"]))
                .replace("{prix_hc}",    str(prix["hc"]))
            )

    return "\n".join(lignes)


def _assembler_outer_inner(outer_tpl, offre_id, inner_tpl):
    # type: (str, str, str) -> str
    """
    Assemble un masque outer + inner pour une seule offre.
    Substitue {offre_id} dans l'inner, puis {offres} dans l'outer.
    Les placeholders JS {{varname}} sont laissés intacts.
    """
    inner = inner_tpl.replace("{offre_id}", offre_id)
    return outer_tpl.replace("{offres}", inner)


def _resoudre_masques(resultat, mode_prix, kva_user=6):
    # type: (Dict, str, int) -> Dict
    """
    Résout les 4 masques pour un résultat d'offre.
    rapport et params sont construits à partir de leur outer + inner respectifs.
    Toutes les variables connues au pipeline ({fournisseur}, {offre}, {type},
    {puissance_souscrite}, {date_validite}) sont substituées ici côté Python.
    Retourne un dict {nom_masque: contenu_html}.
    """
    fournisseur_slug = _slugifier(resultat.get("fournisseur", ""))
    offre_slug = _slugifier("{}_{}".format(
        resultat.get("offre", ""), resultat.get("type", "")
    ))
    offre_id   = resultat["id"]
    type_offre = resultat.get("type", "")
    offre      = resultat.get("detail_tarif", {})

    # Variables statiques substituées côté Python — les agrégats sont calculés côté client
    vars_offre = {
        "fournisseur":         resultat.get("fournisseur", ""),
        "offre":               resultat.get("offre", ""),
        "type":                type_offre,
        "puissance_souscrite": str(kva_user),
        "date_validite":       offre.get("grille_en_vigueur_depuis", ""),
    }

    def _subst(html):
        for k, v in vars_offre.items():
            html = html.replace("{" + k + "}", v)
        return html

    # ── Rapport : card wrapper + sous-rapport template (sections avec data-slot) ─
    couleur_fond = _resoudre_masque("rapport_fond",  fournisseur_slug, offre_slug).strip()
    style_css    = _resoudre_masque("rapport_style", fournisseur_slug, offre_slug)

    rapport_tpl = (
        _resoudre_masque("rapport", fournisseur_slug, offre_slug)
        .replace("{couleur_fond}", couleur_fond or "#f4f4f8")
        .replace("{style_css}",    style_css)
        .replace("{offre_id}",     offre_id)
    )

    _SECTIONS_RAPPORT = [
        "kva", "abonnement_mois", "conso_kwh_an",
        "cout_elec_an", "abonnement_an", "cout_mois_moyen",
    ]
    ss_tpl = _resoudre_masque("rapport_sous_rapport", fournisseur_slug, offre_slug)
    for nom_section in _SECTIONS_RAPPORT:
        section_html = _resoudre_masque(
            "rapport_section_{}".format(nom_section), fournisseur_slug, offre_slug
        )
        ss_tpl = ss_tpl.replace("{section_" + nom_section + "}", section_html)

    params_outer     = _resoudre_masque("params_outer")
    params_inner_tpl = (
        _resoudre_masque("params_inner_{}".format(type_offre), fournisseur_slug, offre_slug)
        or _resoudre_masque("params_inner", fournisseur_slug, offre_slug)
    )
    lignes       = _construire_lignes_params(offre, type_offre, mode_prix, fournisseur_slug, offre_slug)
    params_inner = params_inner_tpl.replace("{lignes}", lignes)

    return {
        "rapport":             _subst(rapport_tpl),
        "rapport_sous_rapport": _subst(ss_tpl),
        "params":              _subst(_assembler_outer_inner(params_outer, offre_id, params_inner)),
        "detail_bandeau": _subst(
            _resoudre_masque("detail_bandeau_{}".format(type_offre), fournisseur_slug, offre_slug)
            or _resoudre_masque("detail_bandeau")
        ),
        "detail_ligne": _subst(
            _resoudre_masque("detail_ligne_{}".format(type_offre), fournisseur_slug, offre_slug)
            or _resoudre_masque("detail_ligne")
        ),
    }


# ─── Résolution plage HC ──────────────────────────────────────────────────────

def _resoudre_plage_hc(cle_offre, plage_hc_tarif, param):
    # type: (str, Optional[str], Dict) -> Tuple[Optional[str], str]
    """
    Résout la plage HC effective pour une offre hphc.
    Priorité :
      1. param du tarif JSON (plage_hc_tarif)
      2. param globale de l'appli (plage_hc_globale)
      3. None → le frontend doit ouvrir un popup pour demander à l'utilisateur

    Modes de l'offre (configurables via le panel) :
      "globale" → plage_hc_globale (défaut)
      "tarif"   → plage_hc_tarif en priorité, fallback globale
      "perso"   → plage_perso saisie par l'utilisateur, fallback globale

    Retourne (plage_retenue_ou_None, mode_effectif).
    """
    plage_globale    = (param.get("plage_hc_globale") or "").strip()
    plage_tarif      = (plage_hc_tarif or "").strip()
    config           = param.get("offres_plage_hc", {}).get(cle_offre, {})
    mode             = config.get("mode", "globale")

    if mode == "tarif":
        plage = plage_tarif or plage_globale or None
    elif mode == "perso":
        plage = (config.get("plage_perso") or "").strip() or plage_globale or plage_tarif or None
    else:  # globale
        plage = plage_globale or plage_tarif or None

    return plage, mode


# ─── Helpers config partielle ────────────────────────────────────────────────

def _extraire_config_offre(offre, type_offre):
    # type: (Dict, str) -> Dict
    """
    Extrait la structure tarifaire partielle d'une offre :
    abonnements + prix kWh par kVA, sans répartition détaillée.
    """
    config = {
        "abonnements": offre.get("abonnements", []),
    }
    if type_offre == "base":
        config["kwh"] = [
            {k: v for k, v in t.items()
             if k in ("puissance_min_kva", "puissance_max_kva", "ht", "ttc")}
            for t in offre.get("kwh", [])
        ]
    elif type_offre == "hphc":
        config["kwh"] = [
            {k: v for k, v in t.items()
             if k in ("puissance_min_kva", "puissance_max_kva",
                      "hp_ht", "hp_ttc", "hc_ht", "hc_ttc")}
            for t in offre.get("kwh", [])
        ]
    return config


def _lire_json(chemin):
    # type: (Path) -> Optional[Dict]
    if not chemin.exists():
        return None
    with open(chemin, "r", encoding="utf-8") as f:
        return json.load(f)


# Répertoire data/ de l'application (ampersage/data/)
_APP_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"


def _ecrire_ndjson_tarif(offre_id, creneaux, type_offre):
    # type: (str, List[Dict], str) -> None
    """
    Écrit les créneaux en NDJSON compact dans data/ pour le client JS.
    Format par créneau :
      base : {"t":"2024-04-19T00:00","pk":0.1877}
      hphc : {"t":"2024-04-19T00:00","h":"C","pk":0.1434}   (h=C→HC, h=H→HP)
    Le timestamp est tronqué à la minute (ex "2024-04-19T00:00"),
    correspondant aux clés construites côté JS avec l'heure Paris.
    """
    _APP_DATA_DIR.mkdir(exist_ok=True)
    slug    = _slugifier(offre_id)
    chemin  = _APP_DATA_DIR / "tarif_{}.ndjson".format(slug)
    lignes  = []
    for c in creneaux:
        ts_court = c["ts"][:16]          # "2024-04-19T00:00"
        pk       = c.get("prix_kwh", 0)
        if type_offre == "hphc":
            h = "C" if c.get("type") == "HC" else "H"
            lignes.append('{{"t":"{}","h":"{}","pk":{}}}'.format(ts_court, h, pk))
        else:
            lignes.append('{{"t":"{}","pk":{}}}'.format(ts_court, pk))
    contenu = "\n".join(lignes).encode("utf-8")
    with open(chemin, "wb") as f:
        f.write(contenu)
    logger.info("generique : %s écrit (%d créneaux)", chemin.name, len(creneaux))


def _ecrire_json(chemin, data):
    # type: (Path, Dict) -> None
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _chemin_tarif(cle):
    # type: (str) -> Path
    """Retourne le chemin du fichier JSON pour une offre donnée."""
    return BASE_DIR / "generique_tarif_{}.json".format(_slugifier(cle))


# ─── Classe principale ────────────────────────────────────────────────────────

class ScriptGenerique(BaseTarif):
    NOM          = "generique"
    PARAM_DEFAUT = _PARAM_DEFAUT

    def cmd_init_param(self, params, global_param):
        """Recrée generique_param.json avec les valeurs par défaut (sans vérifier api_conso)."""
        self._ecrire_param(_PARAM_DEFAUT)
        logger.info("generique INIT_PARAM : paramètres réinitialisés")
        return _reponse(True, "INIT_PARAM", data=_PARAM_DEFAUT)

    def cmd_get_param(self, params, global_param):
        """Retourne le param utilisateur enrichi de l'index des offres connues."""
        param = self._lire_param()
        if param is None:
            return _reponse(False, "GET_PARAM", error="Fichier param absent — lancez INIT_PARAM")
        index = _lire_index()
        data  = dict(param)
        data["_index"] = index["offres"]   # injecté pour le panel, non sauvegardé
        return _reponse(True, "GET_PARAM", data=data)

    def cmd_update(self, params, global_param):
        """
        Calcule les coûts pour toutes les offres découvertes, ou une sélection.

        params attendus :
          records    (optionnel) : liste [{ts, kwh}] — fallback sur global_param
          offres_ids (optionnel) : liste d'IDs à calculer — absent = tout calculer
        """
        if not global_param.get("api_conso_statut", False):
            return _reponse(False, "UPDATE", error="API conso non connectée")

        param = self._lire_param()
        if param is None:
            return _reponse(False, "UPDATE", error="Fichier param absent — lancez INIT_PARAM")

        records = (params.get("records")
                   or global_param.get("DATA", {}).get("api_conso", {}).get("records", []))
        if not records:
            return _reponse(False, "UPDATE", error="Aucune donnée de consommation disponible")

        mode_prix  = param.get("mode_prix", "ttc")
        filtre_ids = params.get("offres_ids")

        # Puissance max observée sur toute la période (en kW)
        pmax_periode = max((r.get("pmax", 0) for r in records), default=0)
        logger.info("generique UPDATE : pmax période = %.2f kW", pmax_periode)

        # Scan + mise à jour de l'index
        tarifs         = _scanner_tarifs(TARIF_DIR)
        index          = _mettre_a_jour_index(tarifs)
        cache_fichiers = {t["fichier"]: t["data"] for t in tarifs}

        resultats           = []
        hc_requises         = []   # offres HPHC sans plage HC configurée
        offres_plage_hc_maj = dict(param.get("offres_plage_hc", {}))
        custom_masques      = {}   # masques pour les offres custom (hors resultats)
        custom_config       = []   # config pour les offres custom

        for cle, meta in index["offres"].items():

            if filtre_ids is not None and cle not in filtre_ids:
                continue

            data_fournisseur = cache_fichiers.get(meta["fichier"])
            if data_fournisseur is None:
                logger.warning("Fichier introuvable pour l'offre %s : %s", cle, meta["fichier"])
                continue

            offres_json = data_fournisseur.get("offres", [])
            offre_idx   = meta["offre_index"]
            if offre_idx >= len(offres_json):
                logger.warning("Index offre hors limites pour %s", cle)
                continue

            offre      = offres_json[offre_idx]
            type_offre = meta["type"]

            # Offres custom : masques via GET_MASKS du module, pas de ndjson calculé
            if type_offre == "custom":
                module_nom     = meta.get("module", "")
                rep_module_str = meta.get("rep_module", "")
                if module_nom and rep_module_str:
                    m_custom = _charger_module_custom(rep_module_str, module_nom)
                    if m_custom:
                        try:
                            offre_config = meta.get("offre_config", {})
                            rep_masks = m_custom.run("GET_MASKS", offre_config, global_param)
                            if rep_masks.get("status"):
                                tpls = rep_masks.get("data", {})
                                vars_offre = {
                                    "fournisseur":         meta["fournisseur"],
                                    "offre":               meta["nom"],
                                    "type":                "custom",
                                    "puissance_souscrite": str(int(round(pmax_periode))),
                                    "offre_id":            cle,
                                }
                                def _s(html, _v=vars_offre):
                                    for k, v in _v.items():
                                        html = html.replace("{" + k + "}", v)
                                    return html

                                # Rapport : templates génériques avec couleur custom
                                couleur_fond = "#e8f0fe"
                                rapport_tpl  = _resoudre_masque("rapport")
                                rapport_html = _s(
                                    rapport_tpl
                                    .replace("{couleur_fond}", couleur_fond)
                                    .replace("{style_css}",    "")
                                    .replace("{date_validite}", "")
                                )
                                # Sous-rapport avec sections
                                ss_tpl = _resoudre_masque("rapport_sous_rapport")
                                _SECTIONS = [
                                    "kva", "abonnement_mois", "conso_kwh_an",
                                    "cout_elec_an", "abonnement_an", "cout_mois_moyen",
                                ]
                                for _sec in _SECTIONS:
                                    ss_tpl = ss_tpl.replace(
                                        "{section_" + _sec + "}",
                                        _resoudre_masque("rapport_section_{}".format(_sec)),
                                    )
                                ss_html = _s(ss_tpl)

                                custom_masques[cle] = {
                                    "params":              _s(tpls.get("params", "")),
                                    "detail_bandeau":      _s(tpls.get("detail_bandeau", "")),
                                    "detail_ligne":        tpls.get("detail_ligne", ""),
                                    "custom_script":       tpls.get("custom_script", ""),
                                    "rapport":             rapport_html,
                                    "rapport_sous_rapport": ss_html,
                                    "custom":              True,
                                    "save_groupe":         tpls.get("save_groupe", ""),
                                }
                        except Exception as exc:
                            logger.warning("generique GET_MASKS custom %s : %s", cle, exc)
                custom_config.append({
                    "id":          cle,
                    "fournisseur": meta["fournisseur"],
                    "nom":         meta["nom"],
                    "type":        "custom",
                    "kva":         None,
                    "source":      "custom",
                    "config":      {},
                    "module":      module_nom,
                })
                continue

            # Tranche kVA applicable : plus petit palier de l'offre couvrant le pmax observé
            kva_applicable = _deduire_kva(pmax_periode, offre.get("abonnements", []))

            resultat = {
                "id":          cle,
                "fournisseur": meta["fournisseur"],
                "source":      meta["source"],
                "offre":       meta["nom"],
                "type":        type_offre,
                "kva":         kva_applicable,
                "detail_tarif": offre,
            }

            if type_offre == "base":
                calc = _creneaux_base(offre, records, kva_applicable, mode_prix)
                if "erreur" in calc:
                    resultat["erreur"] = calc["erreur"]
                    resultats.append(resultat)
                    continue
                creneaux = calc["creneaux"]

            elif type_offre == "hphc":
                plage_hc_tarif          = meta.get("plage_hc_tarif")
                plage_retenue, mode_eff = _resoudre_plage_hc(cle, plage_hc_tarif, param)

                if plage_retenue is None:
                    # Pas de plage HC connue : le frontend doit demander à l'utilisateur
                    hc_requises.append(cle)
                    logger.info("generique : plage HC manquante pour %s", cle)
                    continue

                calc = _creneaux_hphc(offre, records, kva_applicable, mode_prix, plage_retenue)
                if "erreur" in calc:
                    resultat["erreur"] = calc["erreur"]
                    resultats.append(resultat)
                    continue
                creneaux = calc["creneaux"]

                # Mémorisation de la plage retenue dans le param
                config_offre = offres_plage_hc_maj.setdefault(cle, {
                    "mode": "globale", "plage_perso": "",
                })
                config_offre["plage_retenue"] = plage_retenue

            else:
                resultat["erreur"] = "Type d'offre non supporté : {}".format(type_offre)
                resultats.append(resultat)
                continue

            resultat["creneaux"] = creneaux
            resultats.append(resultat)

        # Persistance des plages retenues
        param["offres_plage_hc"] = offres_plage_hc_maj
        self._ecrire_param(param)

        # ── Écriture des fichiers séparés ─────────────────────────────────────

        masques = {}
        config_offres = []

        for r in resultats:
            cle        = r["id"]
            type_offre = r.get("type", "")
            offre      = r.get("detail_tarif", {})
            kva_r      = r.get("kva", pmax_periode)

            # 1. Masques HTML
            if "erreur" not in r:
                masques[cle] = _resoudre_masques(r, mode_prix, kva_r)

            # 2. Config tarifaire partielle
            config_offres.append({
                "id":          cle,
                "fournisseur": r["fournisseur"],
                "nom":         r["offre"],
                "type":        type_offre,
                "kva":         kva_r,
                "source":      r["source"],
                "config":      _extraire_config_offre(offre, type_offre),
            })

            # 3. Créneaux par offre (JSON complet + NDJSON compact pour le client)
            if "erreur" not in r:
                _ecrire_json(_chemin_tarif(cle), {
                    "id":          cle,
                    "fournisseur": r["fournisseur"],
                    "offre":       r["offre"],
                    "type":        type_offre,
                    "kva":         kva_r,
                    "creneaux":    r["creneaux"],
                })
                _ecrire_ndjson_tarif(cle, r["creneaux"], type_offre)

        config_data = {
            "params":      {k: v for k, v in param.items() if not k.startswith("_")},
            "offres":      config_offres,
            "pmax_periode": round(pmax_periode, 2),
        }
        if hc_requises:
            config_data["hc_requises"] = hc_requises

        # Fusion des offres custom (masques + config)
        masques.update(custom_masques)
        config_offres.extend(custom_config)

        _ecrire_json(MASQUES_FILE, masques)
        _ecrire_json(CONFIG_FILE, config_data)

        # Legacy : generique_data.json (résumé sans créneaux)
        data_legacy = {"nb_offres": len(resultats), "offres": [r["id"] for r in resultats]}
        self._ecrire_data(data_legacy)

        logger.info("generique UPDATE : %d offres calculées (pmax %.2f kW, mode %s)%s",
                    len(resultats), pmax_periode, mode_prix,
                    ", {} HC manquantes".format(len(hc_requises)) if hc_requises else "")

        data_retour = {"nb_offres": len(resultats), "pmax_periode": round(pmax_periode, 2)}
        if hc_requises:
            data_retour["hc_requises"] = hc_requises
        return _reponse(True, "UPDATE", data=data_retour)


    def cmd_get_masks(self, params, global_param):
        """Retourne generique_masques.json (masques HTML par offre_id)."""
        data = _lire_json(MASQUES_FILE)
        if data is None:
            return _reponse(False, "GET_MASKS", error="Fichier masques absent — lancez UPDATE")
        return _reponse(True, "GET_MASKS", data=data)

    def cmd_get_config(self, params, global_param):
        """Retourne generique_config.json (params + structure tarifaire partielle)."""
        data = _lire_json(CONFIG_FILE)
        if data is None:
            return _reponse(False, "GET_CONFIG", error="Fichier config absent — lancez UPDATE")
        # Injecter rapport_config depuis global_param (non stocké dans generique_config.json)
        rc = (global_param or {}).get("rapport_config")
        if rc is not None:
            data = dict(data)
            data["params"] = dict(data.get("params", {}))
            data["params"]["rapport_config"] = rc
        return _reponse(True, "GET_CONFIG", data=data)

    def cmd_get_tarif(self, params, global_param):
        """Retourne les créneaux calculés pour une offre (params: offre_id)."""
        offre_id = params.get("offre_id", "")
        if not offre_id:
            return _reponse(False, "GET_TARIF", error="Paramètre offre_id manquant")
        chemin = _chemin_tarif(offre_id)
        data = _lire_json(chemin)
        if data is None:
            return _reponse(False, "GET_TARIF",
                            error="Fichier absent pour l'offre '{}' — lancez UPDATE".format(offre_id))
        return _reponse(True, "GET_TARIF", data=data)

    def run(self, mode, params=None, global_param=None):
        """Point d'entrée — étend BaseTarif avec les modes spécifiques à generique."""
        params       = params or {}
        global_param = global_param if global_param is not None else {}
        extra = {
            "GET_MASKS":  self.cmd_get_masks,
            "GET_CONFIG": self.cmd_get_config,
            "GET_TARIF":  self.cmd_get_tarif,
        }
        if mode in extra:
            return extra[mode](params, global_param)
        return super().run(mode=mode, params=params, global_param=global_param)


_instance = ScriptGenerique(BASE_DIR)


def run(mode, params=None, global_param=None):
    # type: (str, Optional[dict], Optional[dict]) -> dict
    """Point d'entrée du script."""
    return _instance.run(mode=mode, params=params, global_param=global_param)


if __name__ == "__main__":
    mode_cli = sys.argv[1] if len(sys.argv) > 1 else "GET"
    print(json.dumps(run(mode_cli), indent=2, ensure_ascii=False))
