"""
main.py - Programme principal d'affichage.
Reçoit global_param en entrée et retourne du HTML à injecter dans #main-content.
"""

import logging
import re
import sys
import json
from datetime import datetime
from itertools import groupby as _groupby
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger("main")

TZ_PARIS = ZoneInfo("Europe/Paris")
BASE_DIR  = Path(__file__).parent

_MOIS = {
    "FR": ["Janvier","Février","Mars","Avril","Mai","Juin",
           "Juillet","Août","Septembre","Octobre","Novembre","Décembre"],
    "EN": ["January","February","March","April","May","June",
           "July","August","September","October","November","December"],
}

_JOURS = {
    "FR": ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"],
    "EN": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"],
}

_I18N = {
    "titre":   {"FR": "Consommation",             "EN": "Consumption"},
    "annee":   {"FR": "Année",                    "EN": "Year"},
    "mois":    {"FR": "Mois",                     "EN": "Month"},
    "semaine": {"FR": "Semaine",                  "EN": "Week"},
    "jour":    {"FR": "Jour",                     "EN": "Day"},
    "heure":   {"FR": "Heure",                    "EN": "Hour"},
    "pmax":    {"FR": "P. max (kW)",              "EN": "Max power (kW)"},
    "vide":    {"FR": "Aucune donnée disponible", "EN": "No data available"},
}

# Colonnes fixes : Année, Mois, Semaine, Jour, Heure, kWh, P.max
_NB_COL_CONSO   = 7
# Nombre de colonnes « période » avant Heure
_NB_COL_PERIODE = 4


# ─── Chargement des tarifs ────────────────────────────────────────────────────


def _trouver_template(nom, suffixe):
    candidats = [
        BASE_DIR / "scripts" / "tarif" / nom / "{}_{}.html".format(nom, suffixe),
        BASE_DIR / "scripts" / nom / "{}_{}.html".format(nom, suffixe),
    ]
    for c in candidats:
        if c.exists():
            return c
    return None


def _lire_css(header_path):
    """Extrait le CSS du fichier header."""
    contenu = header_path.read_text(encoding="utf-8")
    blocs = re.findall(r"<style>(.*?)</style>", contenu, re.DOTALL)
    return "".join(blocs)


def _charger_tarif_statique(nom, data):
    """
    Charge un tarif depuis ses fichiers header.html et row.html statiques.
    Le header.html contient au plus 2 lignes de <th> (après extraction du <style>).
    Retourne un dict tarif ou None.
    """
    header_path = _trouver_template(nom, "header")
    row_path    = _trouver_template(nom, "row")
    if header_path is None or row_path is None:
        return None

    css          = _lire_css(header_path)
    contenu_hdr  = header_path.read_text(encoding="utf-8")
    sans_style   = re.sub(r"<style>.*?</style>", "", contenu_hdr, flags=re.DOTALL)
    lignes_th    = [l.strip() for l in sans_style.splitlines() if l.strip()]

    contenu_row  = row_path.read_text(encoding="utf-8").strip()
    prix_par_heure = {h["ts"]: h for h in data.get("heures", [])}

    match_eur = re.findall(r'\{([^}]*(?:prix_eur|\.prix)[^}]*)\}', contenu_row)
    prix_eur_key = match_eur[-1] if match_eur else None

    match_kwh = re.findall(r'\{([^}]*prix_kwh[^}]*)\}', contenu_row)
    prix_kwh_key = match_kwh[-1] if match_kwh else None

    nb_td = len(re.findall(r"<td", contenu_row))

    subtotal_cols = [{
        "key":          nom,
        "nb_td":        nb_td,
        "prix_eur_key": prix_eur_key,
        "prix_kwh_key": prix_kwh_key,
    }]

    return {
        "nom":              nom,
        "css":              css,
        "header_l1":        lignes_th[0] if len(lignes_th) > 0 else "",
        "header_l2":        lignes_th[1] if len(lignes_th) > 1 else "",
        "header_l3":        "",
        "header_l4":        "",
        "nb_lignes_header": 2,
        "row_template":     contenu_row,
        "prix_par_heure":   prix_par_heure,
        "nb_td":            nb_td,
        "subtotal_cols":    subtotal_cols,
    }


def _charger_tarif_sobry(nom, data):
    """
    Génère un tarif Sobry dynamiquement selon les TURPE actifs et les produits (SoCap/SoFlex).
    En-tête sur 4 lignes :
      L1 : "Sobry" (colspan = 1 + N_produits × N_TURPE × 2)
      L2 : "Spot" (rowspan=3) | "SoCap" (colspan=N_TURPE×2) | "SoFlex" (colspan=N_TURPE×2)
      L3 : TURPE_1 (colspan=2) | TURPE_2 | … (répété par produit)
      L4 : c€/kWh | Prix | … (colonnes feuilles par produit × TURPE)
    """
    turpe_actifs = data.get("turpe_actifs", [])
    produits     = data.get("produits", ["socap", "soflex"])
    n_turpe      = len(turpe_actifs)
    n_produits   = len(produits)
    # 1 colonne spot + 2 colonnes par produit × TURPE
    nb_cols = 1 + n_produits * n_turpe * 2

    # CSS chargé depuis sobry_header.html (si présent)
    header_path = _trouver_template(nom, "header")
    css = _lire_css(header_path) if header_path else ""

    # ── L1 : titre groupe ──
    header_l1 = '<th class="sobry-nom" colspan="{}">Sobry</th>'.format(nb_cols)

    # ── L2 : Spot (rowspan=3) + groupes produit ──
    th_l2 = '<th class="sobry-col-spot-h" rowspan="3">Spot<br/>' \
            '<span style="font-weight:400;font-size:.75rem">c€/kWh</span></th>'
    for p in produits:
        label = "SoCap" if p == "socap" else "SoFlex" if p == "soflex" else p.upper()
        th_l2 += '<th class="sobry-product-h" colspan="{}">{}</th>'.format(n_turpe * 2, label)
    header_l2 = th_l2

    # ── L3 : TURPE par produit ──
    th_l3 = ""
    for _ in produits:
        for t in turpe_actifs:
            th_l3 += '<th class="sobry-turpe-h" colspan="2">{}</th>'.format(t)
    header_l3 = th_l3

    # ── L4 : colonnes feuilles (pas de cellule spot : déjà couverte par rowspan=3) ──
    th_l4 = ""
    for _ in produits:
        for _ in turpe_actifs:
            th_l4 += '<th class="sobry-col-kwh">c€/kWh</th><th class="sobry-col-eur">Prix</th>'
    header_l4 = th_l4

    # ── Template de ligne ──
    row_tpl = '<td class="sobry-spot">{sobry.prix_spot}</td>'
    for p in produits:
        for t in turpe_actifs:
            row_tpl += (
                '<td class="sobry-prix-kwh">{' + 'sobry.{}.{}.prix_kwh'.format(p, t) + '}</td>'
                '<td class="sobry-prix-eur">{' + 'sobry.{}.{}.prix_eur'.format(p, t) + '} €</td>'
            )

    prix_par_heure = {h["ts"]: h for h in data.get("heures", [])}
    nb_td          = nb_cols

    # ── Colonnes de sous-total ──
    subtotal_cols = [{"key": "sobry|spot", "nb_td": 1, "prix_eur_key": None, "prix_kwh_key": None}]
    for p in produits:
        for t in turpe_actifs:
            subtotal_cols.append({
                "key":          "sobry|{}|{}".format(p, t),
                "nb_td":        2,
                "prix_eur_key": "sobry.{}.{}.prix_eur".format(p, t),
                "prix_kwh_key": None,
            })

    return {
        "nom":              nom,
        "css":              css,
        "header_l1":        header_l1,
        "header_l2":        header_l2,
        "header_l3":        header_l3,
        "header_l4":        header_l4,
        "nb_lignes_header": 4,
        "row_template":     row_tpl,
        "prix_par_heure":   prix_par_heure,
        "nb_td":            nb_td,
        "subtotal_cols":    subtotal_cols,
    }


def _charger_tarifs(global_param):
    """
    Charge tous les tarifs présents dans global_param["DATA"].
    Si un tarif possède "turpe_actifs" dans ses données, il est traité comme Sobry
    (génération dynamique). Sinon, chargement depuis les fichiers HTML statiques.

    Post-traitement : si au moins un tarif a 3 lignes d'en-tête, les tarifs à 2 lignes
    voient leur header_l1 enrichi d'un rowspan="2" et leur header_l2 déplacé en header_l3.
    """
    tarifs = []
    for nom, data in global_param.get("DATA", {}).items():
        if nom == "api_conso" or "heures" not in data:
            continue
        if data.get("turpe_actifs") is not None:
            t = _charger_tarif_sobry(nom, data)
        else:
            t = _charger_tarif_statique(nom, data)
        if t:
            tarifs.append(t)

    # Adaptation si mix : aligner tous les tarifs sur le nombre max de lignes d'en-tête
    has_4row = any(t["nb_lignes_header"] >= 4 for t in tarifs)
    has_3row = any(t["nb_lignes_header"] >= 3 for t in tarifs)

    if has_4row:
        for t in tarifs:
            if t["nb_lignes_header"] <= 2:
                # Déplacer les colonnes en L4 (ligne feuilles)
                t["header_l4"] = t["header_l2"]
                t["header_l2"] = ""
                t["header_l3"] = ""
                # rowspan="3" sur le titre (couvre L1, L2, L3)
                t["header_l1"] = re.sub(r"<th\b", '<th rowspan="3"', t["header_l1"], count=1)
    elif has_3row:
        for t in tarifs:
            if t["nb_lignes_header"] == 2:
                # Déplacer les colonnes de header_l2 → header_l3
                t["header_l3"] = t["header_l2"]
                t["header_l2"] = ""
                # rowspan="2" sur le titre (couvre L1, L2)
                t["header_l1"] = re.sub(r"<th\b", '<th rowspan="2"', t["header_l1"], count=1)

    return tarifs


# ─── Génération des cellules tarif ───────────────────────────────────────────


def _cellules_tarif(ts_record, tarifs):
    dt       = datetime.fromisoformat(ts_record).astimezone(TZ_PARIS)
    heure_ts = dt.replace(minute=0, second=0, microsecond=0).isoformat()
    cells    = ""
    for t in tarifs:
        donnees = t["prix_par_heure"].get(heure_ts, {})
        cell    = t["row_template"]
        for m in re.finditer(r"\{([^}]+)\}", t["row_template"]):
            cle  = m.group(1)
            cell = cell.replace(m.group(0), str(donnees.get(cle, "\u2014")))
        cells += cell
    return cells


def _prix_eur_record_col(ts_record, record_kwh, tarif, col):
    """Calcule le coût pro-rata pour une colonne de sous-total donnée."""
    dt       = datetime.fromisoformat(ts_record).astimezone(TZ_PARIS)
    heure_ts = dt.replace(minute=0, second=0, microsecond=0).isoformat()
    donnees  = tarif["prix_par_heure"].get(heure_ts, {})
    try:
        if col.get("prix_kwh_key"):
            prix_kwh = float(donnees.get(col["prix_kwh_key"], 0))
            return record_kwh * prix_kwh / 100
        if col.get("prix_eur_key"):
            kwh_heure  = float(donnees.get("kwh", 0))
            prix_heure = float(donnees.get(col["prix_eur_key"], 0))
            if kwh_heure == 0:
                return 0.0
            return record_kwh * prix_heure / kwh_heure
    except (ValueError, TypeError):
        pass
    return 0.0


# ─── Accumulateurs ───────────────────────────────────────────────────────────


def _acc_vide(tarifs):
    cols = {}
    for t in tarifs:
        for col in t["subtotal_cols"]:
            cols[col["key"]] = 0.0
    return {"kwh": 0.0, "pmax": 0.0, "tarifs": cols}


def _acc_ajouter(acc, r, tarifs):
    acc["kwh"]  += r["kwh"]
    acc["pmax"]  = max(acc["pmax"], r["pmax"])
    for t in tarifs:
        for col in t["subtotal_cols"]:
            if col.get("prix_eur_key") or col.get("prix_kwh_key"):
                acc["tarifs"][col["key"]] += _prix_eur_record_col(r["ts"], r["kwh"], t, col)


# ─── Lignes de sous-total ────────────────────────────────────────────────────


def _cellules_total_tarif(tarifs, acc_tarifs):
    cells = ""
    for t in tarifs:
        for col in t["subtotal_cols"]:
            total = acc_tarifs.get(col["key"], 0.0)
            if col.get("prix_eur_key") or col.get("prix_kwh_key"):
                contenu = "{:.4f} \u20ac".format(total)
            else:
                contenu = "\u2014"
            cells += '<td class="conso-total-tarif" colspan="{}">{}</td>'.format(
                col["nb_td"], contenu
            )
    return cells


def _ligne_total(niveau, label, indent, acc, tarifs, toggle_key, parent_key=""):
    """
    Génère une ligne de sous-total avec bouton d'expansion.
    indent : 0=année (colspan=5), 1=mois (colspan=4),
             2=semaine (colspan=3),  3=jour (colspan=2)
    parent_key : clé du toggle parent (vide pour les totaux annuels).
    """
    colspan_label = _NB_COL_PERIODE + 1 - indent
    cls   = "conso-subtotal conso-subtotal-{}".format(niveau)
    btn   = ('<button class="conso-toggle" data-niveau="{}" '
             'data-key="{}" title="D\u00e9velopper">\u25b8</button> ').format(niveau, toggle_key)
    vides = indent * "<td></td>"
    cells = (
        vides
        + '<td colspan="{}" class="conso-total-label">{}{}</td>'.format(
            colspan_label, btn, label)
        + '<td class="conso-total-kwh">{:.3f} kWh</td>'.format(acc["kwh"])
        + '<td class="conso-total-pmax">{:.3f}</td>'.format(acc["pmax"])
    )
    cells += _cellules_total_tarif(tarifs, acc["tarifs"])
    parent_attr = ' data-parent="{}"'.format(parent_key) if parent_key else ""
    return '<tr class="{}"{}>{}</tr>'.format(cls, parent_attr, cells)


# ─── Point d'entrée ──────────────────────────────────────────────────────────


_BANDEAUX = {
    "mock": {
        "cls": "bandeau-info",
        "FR": "Données de démonstration — renseignez votre token dans le panneau <strong>API Conso</strong> pour afficher votre consommation réelle.",
        "EN": "Demo data — enter your token in the <strong>API Conso</strong> panel to display your real consumption.",
    },
    "erreur": {
        "cls": "bandeau-erreur",
        "FR": "Erreur de connexion à l'API",
        "EN": "API connection error",
    },
}


def _bandeau_html(global_param, langue):
    """Retourne le HTML du bandeau d'état, ou une chaîne vide."""
    mode = global_param.get("api_conso_mode")
    if mode not in _BANDEAUX:
        return ""
    conf = _BANDEAUX[mode]
    texte = conf[langue]
    # Pour le mode erreur, on ajoute le message détaillé
    if mode == "erreur":
        detail = global_param.get("api_conso_message", "")
        if detail:
            texte = "{} : {}".format(texte, detail)
    return (
        '<div class="{cls}">'
        '<span class="bandeau-icone">{icone}</span>'
        '<span>{texte}</span>'
        '</div>'
    ).format(
        cls=conf["cls"],
        icone="ℹ️" if mode == "mock" else "⚠️",
        texte=texte,
    )


def run(global_param):
    """Génère le tableau de consommation HTML avec sous-totaux contractables."""
    langue = global_param.get("langue", "FR")
    if langue not in ("FR", "EN"):
        langue = "FR"

    bandeau = _bandeau_html(global_param, langue)

    records = global_param.get("DATA", {}).get("api_conso", {}).get("records", [])
    if not records:
        return bandeau + '<p class="placeholder">{}</p>'.format(_I18N["vide"][langue])

    col_annee   = _I18N["annee"][langue]
    col_mois    = _I18N["mois"][langue]
    col_semaine = _I18N["semaine"][langue]
    col_jour    = _I18N["jour"][langue]
    col_heure   = _I18N["heure"][langue]
    col_pmax    = _I18N["pmax"][langue]
    titre       = _I18N["titre"][langue]
    mois_labels  = _MOIS[langue]
    jours_labels = _JOURS[langue]

    tarifs     = _charger_tarifs(global_param)
    css_tarifs = "".join(t["css"] for t in tarifs)
    bloc_style = "<style>{}</style>".format(css_tarifs) if css_tarifs else ""

    has_4row = any(t["nb_lignes_header"] >= 4 for t in tarifs)
    has_3row = any(t["nb_lignes_header"] >= 3 for t in tarifs)

    # ── Parsing des records ──────────────────────────────────────────────────
    parsed = []
    for r in records:
        dt  = datetime.fromisoformat(r["ts"]).astimezone(TZ_PARIS)
        iso = dt.isocalendar()
        parsed.append({
            "ts":          r["ts"],
            "year_key":    (dt.year,),
            "month_key":   (dt.year, dt.month),
            "week_key":    (iso[0], iso[1]),
            "day_key":     (dt.year, dt.month, dt.day),
            "year_label":  str(dt.year),
            "month_label": mois_labels[dt.month - 1],
            "day_label":   jours_labels[dt.weekday()] + " " + str(dt.day).zfill(2),
            "week_label":  "S{:02d}".format(iso[1]),
            "heure":       dt.strftime("%H:%M"),
            "kwh":         r["kwh"],
            "pmax":        r.get("pmax", 0.0),
        })

    # Seuil pmax (8ème décile)
    valeurs_pmax = sorted(r["pmax"] for r in parsed)
    seuil_pmax   = valeurs_pmax[int(len(valeurs_pmax) * 0.8)]

    # ── Pré-calcul des totaux ────────────────────────────────────────────────
    day_totals   = {}
    week_totals  = {}
    month_totals = {}
    year_totals  = {}

    for r in parsed:
        for tdict, key in [
            (day_totals,   r["day_key"]),
            (week_totals,  r["week_key"]),
            (month_totals, r["month_key"]),
            (year_totals,  r["year_key"]),
        ]:
            if key not in tdict:
                tdict[key] = _acc_vide(tarifs)
            _acc_ajouter(tdict[key], r, tarifs)

    # ── Construction des lignes (sous-totaux d'abord, détails en dessous) ───────
    lignes = []

    for year_key, year_iter in _groupby(parsed, key=lambda r: r["year_key"]):
        year_records = list(year_iter)
        y_id = str(year_key[0])

        # Sous-total année en tête (pas de parent)
        lignes.append(_ligne_total(
            "annee", year_records[0]["year_label"], 0,
            year_totals[year_key], tarifs, toggle_key=y_id, parent_key=""))

        for month_key, month_iter in _groupby(year_records, key=lambda r: r["month_key"]):
            month_records = list(month_iter)
            m_id = "{}-{:02d}".format(month_key[0], month_key[1])

            # Sous-total mois (parent = année)
            lignes.append(_ligne_total(
                "mois", month_records[0]["month_label"], 1,
                month_totals[month_key], tarifs, toggle_key=m_id, parent_key=y_id))

            for week_key, week_iter in _groupby(month_records, key=lambda r: r["week_key"]):
                week_records = list(week_iter)
                w_id = "{}-W{:02d}".format(week_key[0], week_key[1])

                # Sous-total semaine (parent = mois)
                lignes.append(_ligne_total(
                    "semaine", week_records[0]["week_label"], 2,
                    week_totals[week_key], tarifs, toggle_key=w_id, parent_key=m_id))

                for day_key, day_iter in _groupby(week_records, key=lambda r: r["day_key"]):
                    day_records = list(day_iter)
                    d_id = "{}-{:02d}-{:02d}".format(*day_key)

                    # Sous-total jour (parent = semaine)
                    lignes.append(_ligne_total(
                        "jour", day_records[0]["day_label"], 3,
                        day_totals[day_key], tarifs, toggle_key=d_id, parent_key=w_id))

                    # Lignes détail demi-heure (parent = jour)
                    for r in day_records:
                        cells  = '<td class="conso-annee"></td>'
                        cells += '<td class="conso-mois"></td>'
                        cells += '<td class="conso-semaine"></td>'
                        cells += '<td class="conso-jour"></td>'
                        cells += '<td class="conso-heure">{}</td>'.format(r["heure"])
                        cells += '<td class="conso-kwh">{:.3f}</td>'.format(r["kwh"])
                        cls_p  = ("conso-pmax conso-pmax-pic"
                                  if r["pmax"] >= seuil_pmax else "conso-pmax")
                        cells += '<td class="{}">{:.3f}</td>'.format(cls_p, r["pmax"])
                        cells += _cellules_tarif(r["ts"], tarifs)
                        lignes.append('<tr data-parent="{}">{}</tr>'.format(d_id, cells))

    tbody = "\n".join(lignes)

    # ── En-têtes colonne conso ───────────────────────────────────────────────
    th_conso = (
        '<th class="conso-th">{}</th>'
        '<th class="conso-th">{}</th>'
        '<th class="conso-th">{}</th>'
        '<th class="conso-th">{}</th>'
        '<th class="conso-th">{}</th>'
        '<th class="conso-th">kWh</th>'
        '<th class="conso-th">{}</th>'
    ).format(col_annee, col_mois, col_semaine, col_jour, col_heure, col_pmax)

    # ── Construction du thead ────────────────────────────────────────────────
    if has_4row:
        # 4 lignes : "Consommation" a rowspan=3 (couvre L1+L2+L3), conso headers en L4
        th_l1 = "".join(t["header_l1"] for t in tarifs)
        th_l2 = "".join(t["header_l2"] for t in tarifs)
        th_l3 = "".join(t["header_l3"] for t in tarifs)
        th_l4 = "".join(t.get("header_l4", "") for t in tarifs)
        thead = (
            "  <thead>\n"
            '    <tr>\n'
            '      <th colspan="{}" class="conso-titre" rowspan="3">{}</th>\n'
            '      {}\n'
            '    </tr>\n'
            '    <tr>\n'
            '      {}\n'
            '    </tr>\n'
            '    <tr>\n'
            '      {}\n'
            '    </tr>\n'
            '    <tr>\n'
            '      {}\n'
            '      {}\n'
            '    </tr>\n'
            '  </thead>'
        ).format(_NB_COL_CONSO, titre, th_l1, th_l2, th_l3, th_conso, th_l4)
    elif has_3row:
        # 3 lignes : "Consommation" a rowspan=2 (couvre L1+L2), conso headers en L3
        th_l1 = "".join(t["header_l1"] for t in tarifs)
        th_l2 = "".join(t["header_l2"] for t in tarifs)
        th_l3 = "".join(t["header_l3"] for t in tarifs)
        thead = (
            "  <thead>\n"
            '    <tr>\n'
            '      <th colspan="{}" class="conso-titre" rowspan="2">{}</th>\n'
            '      {}\n'
            '    </tr>\n'
            '    <tr>\n'
            '      {}\n'
            '    </tr>\n'
            '    <tr>\n'
            '      {}\n'
            '      {}\n'
            '    </tr>\n'
            '  </thead>'
        ).format(_NB_COL_CONSO, titre, th_l1, th_l2, th_conso, th_l3)
    else:
        th_l1 = "".join(t["header_l1"] for t in tarifs)
        th_l2 = "".join(t["header_l2"] for t in tarifs)
        thead = (
            "  <thead>\n"
            '    <tr>\n'
            '      <th colspan="{}" class="conso-titre">{}</th>\n'
            '      {}\n'
            '    </tr>\n'
            '    <tr>\n'
            '      {}\n'
            '      {}\n'
            '    </tr>\n'
            '  </thead>'
        ).format(_NB_COL_CONSO, titre, th_l1, th_conso, th_l2)

    return """
{bandeau}
{style}
<table class="conso-table">
{thead}
  <tbody>
    {tbody}
  </tbody>
</table>
""".format(bandeau=bandeau, style=bloc_style, thead=thead, tbody=tbody)


if __name__ == "__main__":
    try:
        data = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
    except json.JSONDecodeError:
        data = {}
    print(run(data))
