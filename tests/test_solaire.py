"""
test_solaire.py — Tests unitaires pour modules_complementaires/solaire/solaire.py.
Couvre : slugify, parsing CSV, fusion de points, import (création/mise à jour),
         ajout manuel, exécution de script, fusion de sources, modes GET/SET_PARAM.
"""

import json
import textwrap
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

# Chargement dynamique du module (même pattern que les autres tests)
import importlib.util, sys

SOLAIRE_PATH = Path(__file__).parent.parent / "modules_complementaires" / "solaire" / "solaire.py"


def _charger_module():
    spec   = importlib.util.spec_from_file_location("solaire", SOLAIRE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


solaire = _charger_module()


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolation(tmp_path, monkeypatch):
    """Redirige les fichiers de données vers un répertoire temporaire."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(solaire._parametres, "FICHIER_SOURCES", str(data_dir / "solaire_sources.json"))
    monkeypatch.setattr(solaire._parametres, "FICHIER_DONNEES", str(data_dir / "solaire_donnees.json"))
    # Forcer _fichier_sources / _fichier_donnees à relire les attributs patchés
    monkeypatch.setattr(solaire, "_fichier_sources", lambda: Path(solaire._parametres.FICHIER_SOURCES))
    monkeypatch.setattr(solaire, "_fichier_donnees", lambda: Path(solaire._parametres.FICHIER_DONNEES))


CSV_EST = textwrap.dedent("""\
    datetime,Luminosité est (lx)
    "2024-10-12 11:06:40","5238.125"
    "2024-10-12 12:05:06","5985.280"
    "2024-10-13 09:00:00","1234.000"
""")

CSV_OUEST = textwrap.dedent("""\
    datetime,Luminosité ouest (lx)
    "2024-10-12 11:06:40","4000.000"
    "2024-10-12 13:00:00","3500.000"
""")

CSV_PUISSANCE = textwrap.dedent("""\
    datetime,Puissance panneau (W)
    "2024-10-12 11:00:00","1200.000"
    "2024-10-12 12:00:00","1500.000"
""")


# ─── Slugify ─────────────────────────────────────────────────────────────────

def test_slugify_accents():
    assert solaire._slugify("Luminosité est (lx)") == "luminosite_est_lx"

def test_slugify_parentheses():
    assert solaire._slugify("Puissance panneau (W)") == "puissance_panneau_w"

def test_slugify_espaces_multiples():
    assert solaire._slugify("  foo   bar  ") == "foo_bar"

def test_slugify_vide():
    assert solaire._slugify("") == ""

def test_slugify_caracteres_speciaux():
    # Le symbole ₂ (subscript) n'est pas converti en chiffre par NFD
    assert solaire._slugify("CO₂ (g/kWh)") == "co_g_kwh"


# ─── Parsing CSV ─────────────────────────────────────────────────────────────

def test_parser_csv_nom_colonne():
    nom, pts, _ = solaire._parser_csv(CSV_EST)
    assert nom == "Luminosité est (lx)"

def test_parser_csv_points():
    _, pts, _ = solaire._parser_csv(CSV_EST)
    assert len(pts) == 3
    assert pts[0]["valeur"] == pytest.approx(5238.125)

def test_parser_csv_trie_croissant():
    csv = textwrap.dedent("""\
        datetime,test
        "2024-10-13 09:00:00","3.0"
        "2024-10-12 11:00:00","1.0"
        "2024-10-12 12:00:00","2.0"
    """)
    _, pts, _ = solaire._parser_csv(csv)
    ts_list = [p["ts"] for p in pts]
    assert ts_list == sorted(ts_list)

def test_parser_csv_entete_manquant():
    with pytest.raises(ValueError, match="CSV invalide"):
        solaire._parser_csv("valeur\n1.0\n")

def test_parser_csv_ignore_lignes_invalides():
    csv = "datetime,test\n\"2024-10-12 11:00:00\",\"1.0\"\nmauvaise_ligne\n\"2024-10-12 12:00:00\",\"2.0\"\n"
    _, pts, ignores = solaire._parser_csv(csv)
    assert len(pts) == 2
    assert ignores == 1

def test_parser_csv_compte_ignores():
    csv = textwrap.dedent("""\
        datetime,test
        "2024-10-12 11:00:00","1.0"
        "pas_une_date","2.0"
        "2024-10-12 13:00:00","pas_un_nombre"
        "2024-10-12 14:00:00","3.0"
    """)
    _, pts, ignores = solaire._parser_csv(csv)
    assert len(pts) == 2
    assert ignores == 2


# ─── Détection d'unité ───────────────────────────────────────────────────────

def test_detecter_unite_lux_parentheses():
    assert solaire._detecter_unite("Luminosité est (lx)") == "lux"

def test_detecter_unite_lux_mot():
    assert solaire._detecter_unite("Irradiance lux capteur") == "lux"

def test_detecter_unite_W():
    assert solaire._detecter_unite("Puissance panneau (W)") == "W"

def test_detecter_unite_kW():
    assert solaire._detecter_unite("Puissance crête (kW)") == "kW"

def test_detecter_unite_kWh():
    assert solaire._detecter_unite("Énergie produite (kWh)") == "kWh"

def test_detecter_unite_kWh_priorite_sur_kW():
    # kWh doit être détecté avant kW
    assert solaire._detecter_unite("Production (kWh)") == "kWh"

def test_detecter_unite_kW_priorite_sur_W():
    # kW ne doit pas matcher W seul
    assert solaire._detecter_unite("Puissance (kW)") == "kW"

def test_detecter_unite_inconnu():
    assert solaire._detecter_unite("Température ambiante") is None

def test_detecter_unite_inconnu_sans_symbole():
    assert solaire._detecter_unite("Capteur nord") is None


# ─── Import CSV avec gestion de l'unité ──────────────────────────────────────

def test_import_csv_unite_lux_detectee():
    r = solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    assert r["status"] is True
    assert r["data"].get("unite_requise") is not True
    assert r["data"]["unite"] == "lux"

def test_import_csv_unite_W_detectee():
    r = solaire.run("IMPORT_CSV", params={"contenu": CSV_PUISSANCE})
    assert r["status"] is True
    assert r["data"]["unite"] == "W"

def test_import_csv_unite_requise_si_inconnue():
    csv = "datetime,Température ambiante\n\"2024-10-12 11:00:00\",\"22.5\"\n"
    r = solaire.run("IMPORT_CSV", params={"contenu": csv})
    assert r["status"] is True
    assert r["data"]["unite_requise"] is True
    assert r["data"]["importes"] == 0   # Aucun point importé avant confirmation

def test_import_csv_unite_requise_contient_source_nom():
    csv = "datetime,Capteur mystère\n\"2024-10-12 11:00:00\",\"1.0\"\n"
    r = solaire.run("IMPORT_CSV", params={"contenu": csv})
    assert r["data"]["source_nom"] == "Capteur mystère"

def test_import_csv_unite_param_override():
    """Fournir `unite` dans les params force le type même si l'en-tête est ambigu."""
    csv = "datetime,Capteur mystère\n\"2024-10-12 11:00:00\",\"1.0\"\n"
    r = solaire.run("IMPORT_CSV", params={"contenu": csv, "unite": "kW"})
    assert r["status"] is True
    assert r["data"].get("unite_requise") is not True
    assert r["data"]["unite"] == "kW"
    assert r["data"]["importes"] == 1

def test_import_csv_unite_autre():
    """Une unité libre (autre) est acceptée."""
    csv = "datetime,Capteur mystère\n\"2024-10-12 11:00:00\",\"1.0\"\n"
    r = solaire.run("IMPORT_CSV", params={"contenu": csv, "unite": "°C"})
    assert r["status"] is True
    assert r["data"]["unite"] == "°C"

def test_import_csv_coeff_defaut_lux():
    r = solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    sources = solaire._charger_sources()
    assert sources[0]["coeff"] == 128000

def test_import_csv_coeff_defaut_W():
    r = solaire.run("IMPORT_CSV", params={"contenu": CSV_PUISSANCE})
    sources = solaire._charger_sources()
    assert sources[0]["coeff"] == 1000

def test_import_csv_ignores_dans_reponse():
    csv = textwrap.dedent("""\
        datetime,Luminosité (lx)
        "2024-10-12 11:00:00","100.0"
        "pas_une_date","200.0"
        "2024-10-12 13:00:00","300.0"
    """)
    r = solaire.run("IMPORT_CSV", params={"contenu": csv})
    assert r["data"]["importes"] == 2
    assert r["data"]["ignores"] == 1


# ─── Fusion de points ─────────────────────────────────────────────────────────

def test_fusionner_points_ajout():
    a = [{"ts": "2024-01-01T10:00:00", "valeur": 1.0}]
    b = [{"ts": "2024-01-01T11:00:00", "valeur": 2.0}]
    result = solaire._fusionner_points(a, b)
    assert len(result) == 2

def test_fusionner_points_remplacement():
    a = [{"ts": "2024-01-01T10:00:00", "valeur": 1.0}]
    b = [{"ts": "2024-01-01T10:00:00", "valeur": 9.9}]
    result = solaire._fusionner_points(a, b)
    assert len(result) == 1
    assert result[0]["valeur"] == pytest.approx(9.9)

def test_fusionner_points_ordre_croissant():
    a = [{"ts": "2024-01-03T00:00:00", "valeur": 3.0}]
    b = [{"ts": "2024-01-01T00:00:00", "valeur": 1.0},
         {"ts": "2024-01-02T00:00:00", "valeur": 2.0}]
    result = solaire._fusionner_points(a, b)
    ts_list = [p["ts"] for p in result]
    assert ts_list == sorted(ts_list)


# ─── Import CSV (mode IMPORT_CSV) ─────────────────────────────────────────────

def test_import_csv_cree_source():
    r = solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    assert r["status"] is True
    assert r["data"]["creee"] is True
    assert r["data"]["source_id"] == "luminosite_est_lx"
    assert r["data"]["importes"] == 3

def test_import_csv_source_nom_depuis_entete():
    r = solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    assert r["data"]["source_nom"] == "Luminosité est (lx)"

def test_import_csv_reimport_non_creee():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    r = solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    assert r["data"]["creee"] is False

def test_import_csv_reimport_fusion():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    # CSV avec 1 point existant modifié + 1 nouveau
    csv_update = textwrap.dedent("""\
        datetime,Luminosité est (lx)
        "2024-10-12 11:06:40","9999.999"
        "2024-11-01 08:00:00","100.000"
    """)
    r = solaire.run("IMPORT_CSV", params={"contenu": csv_update})
    assert r["status"] is True
    r_data = solaire.run("GET_DATA", params={"source_id": "luminosite_est_lx", "limit": 100})
    pts = r_data["data"]["points"]
    # Total : 3 initiaux + 1 nouveau = 4 (le ts existant est remplacé)
    assert r_data["data"]["total"] == 4

def test_import_csv_contenu_vide():
    r = solaire.run("IMPORT_CSV", params={"contenu": ""})
    assert r["status"] is False
    assert "requis" in r["error"].lower()

def test_import_csv_contenu_espaces():
    r = solaire.run("IMPORT_CSV", params={"contenu": "   \n  \n"})
    assert r["status"] is False
    assert "invalide" in r["error"].lower()

def test_import_csv_une_seule_colonne():
    r = solaire.run("IMPORT_CSV", params={"contenu": "datetime\n2024-01-01,1.0\n"})
    assert r["status"] is False
    assert "invalide" in r["error"].lower()

def test_import_csv_entete_seul_sans_donnees():
    r = solaire.run("IMPORT_CSV", params={"contenu": "datetime,Luminosité (lx)\n"})
    assert r["status"] is False
    assert "aucun point" in r["error"].lower()

def test_import_csv_sans_points_valides():
    csv = "datetime,test\nmauvais,data\n"
    r = solaire.run("IMPORT_CSV", params={"contenu": csv})
    assert r["status"] is False


# ─── Ajout manuel (mode ADD_POINT) ────────────────────────────────────────────

def test_add_point_base():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    r = solaire.run("ADD_POINT", params={
        "source_id": "luminosite_est_lx",
        "ts":        "2025-01-15 10:30:00",
        "valeur":    42.0,
    })
    assert r["status"] is True
    assert r["data"]["valeur"] == pytest.approx(42.0)

def test_add_point_params_manquants():
    r = solaire.run("ADD_POINT", params={"source_id": "x"})
    assert r["status"] is False

def test_add_point_ts_invalide():
    r = solaire.run("ADD_POINT", params={"source_id": "x", "ts": "pas-une-date", "valeur": 1.0})
    assert r["status"] is False


# ─── Script utilisateur (mode RUN_SCRIPT) ────────────────────────────────────

def test_run_script_base():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    # Ajouter un script à la source
    sources = solaire._charger_sources()
    sources[0]["script"] = textwrap.dedent("""\
        resultats = [
            {"ts": "2025-06-01T08:00:00", "valeur": 500.0},
            {"ts": "2025-06-01T09:00:00", "valeur": 700.0},
        ]
    """)
    solaire._sauvegarder_sources(sources)
    r = solaire.run("RUN_SCRIPT", params={"source_id": "luminosite_est_lx"})
    assert r["status"] is True
    assert r["data"]["importes"] == 2

def test_run_script_source_inconnue():
    r = solaire.run("RUN_SCRIPT", params={"source_id": "inconnue"})
    assert r["status"] is False

def test_run_script_vide():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    sources = solaire._charger_sources()
    sources[0]["script"] = ""
    solaire._sauvegarder_sources(sources)
    r = solaire.run("RUN_SCRIPT", params={"source_id": "luminosite_est_lx"})
    assert r["status"] is False

def test_run_script_sans_resultats():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    sources = solaire._charger_sources()
    sources[0]["script"] = "x = 42"   # Pas de variable `resultats`
    solaire._sauvegarder_sources(sources)
    r = solaire.run("RUN_SCRIPT", params={"source_id": "luminosite_est_lx"})
    assert r["status"] is False

def test_run_script_erreur_syntaxe():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    sources = solaire._charger_sources()
    sources[0]["script"] = "def f(:"   # Syntaxe invalide
    solaire._sauvegarder_sources(sources)
    r = solaire.run("RUN_SCRIPT", params={"source_id": "luminosite_est_lx"})
    assert r["status"] is False


# ─── Fusion de sources (mode MERGE_SOURCES) ───────────────────────────────────

def test_merge_sources_base():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})    # 3 pts
    solaire.run("IMPORT_CSV", params={"contenu": CSV_OUEST})  # 2 pts
    r = solaire.run("MERGE_SOURCES", params={
        "source_id_cible":     "luminosite_est_lx",
        "source_id_fusionner": "luminosite_ouest_lx",
    })
    assert r["status"] is True
    # ts "2024-10-12 11:06:40" existe dans les deux — dédupliqué
    # 3 + 2 - 1 doublon = 4 points uniques
    assert r["data"]["total_points"] == 4

def test_merge_sources_supprime_fusionnee():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    solaire.run("IMPORT_CSV", params={"contenu": CSV_OUEST})
    solaire.run("MERGE_SOURCES", params={
        "source_id_cible":     "luminosite_est_lx",
        "source_id_fusionner": "luminosite_ouest_lx",
    })
    r = solaire.run("GET_PARAM")
    ids = [s["id"] for s in r["data"]["sources"]]
    assert "luminosite_est_lx" in ids
    assert "luminosite_ouest_lx" not in ids

def test_merge_sources_conflit_cible_gagne():
    """En cas de même timestamp, les données de la cible sont conservées."""
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})    # 11:06:40 → 5238.125
    solaire.run("IMPORT_CSV", params={"contenu": CSV_OUEST})  # 11:06:40 → 4000.000
    solaire.run("MERGE_SOURCES", params={
        "source_id_cible":     "luminosite_est_lx",
        "source_id_fusionner": "luminosite_ouest_lx",
    })
    r = solaire.run("GET_DATA", params={"source_id": "luminosite_est_lx", "limit": 100})
    pts_by_ts = {p["ts"][:19]: p["valeur"] for p in r["data"]["points"]}
    assert pts_by_ts["2024-10-12T11:06:40"] == pytest.approx(5238.125)

def test_merge_sources_meme_id():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    r = solaire.run("MERGE_SOURCES", params={
        "source_id_cible":     "luminosite_est_lx",
        "source_id_fusionner": "luminosite_est_lx",
    })
    assert r["status"] is False

def test_merge_sources_inconnue():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    r = solaire.run("MERGE_SOURCES", params={
        "source_id_cible":     "luminosite_est_lx",
        "source_id_fusionner": "source_inexistante",
    })
    assert r["status"] is False


# ─── GET / GET_PARAM / SET_PARAM / INIT_PARAM ────────────────────────────────

def test_get_vide():
    r = solaire.run("GET")
    assert r["status"] is True
    assert r["data"]["sources"] == []

def test_get_avec_sources():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    r = solaire.run("GET")
    assert len(r["data"]["sources"]) == 1
    src = r["data"]["sources"][0]
    assert src["total_points"] == 3
    assert src["premier_ts"] is not None

def test_get_param():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    r = solaire.run("GET_PARAM")
    assert r["status"] is True
    assert len(r["data"]["sources"]) == 1

def test_set_param_renomme():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    sources = solaire.run("GET_PARAM")["data"]["sources"]
    sources[0]["nom"] = "Mon capteur Est"
    r = solaire.run("SET_PARAM", params={"sources": sources})
    assert r["status"] is True
    r2 = solaire.run("GET_PARAM")
    assert r2["data"]["sources"][0]["nom"] == "Mon capteur Est"

def test_set_param_preserve_id():
    """Renommer ne doit pas changer l'id."""
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    sources = solaire.run("GET_PARAM")["data"]["sources"]
    sources[0]["nom"] = "Nouveau nom"
    solaire.run("SET_PARAM", params={"sources": sources})
    r = solaire.run("GET_PARAM")
    assert r["data"]["sources"][0]["id"] == "luminosite_est_lx"

def test_set_param_sans_sources():
    r = solaire.run("SET_PARAM", params={})
    assert r["status"] is False

def test_set_param_supprime_champs_runtime():
    """SET_PARAM ne doit pas persister total_points / premier_ts / dernier_ts."""
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    sources = solaire.run("GET")["data"]["sources"]  # Inclut les champs runtime
    solaire.run("SET_PARAM", params={"sources": sources})
    sources_sauvees = solaire._charger_sources()
    assert "total_points" not in sources_sauvees[0]
    assert "premier_ts"   not in sources_sauvees[0]

def test_init_param_cree_fichier():
    r = solaire.run("INIT_PARAM")
    assert r["status"] is True
    assert solaire._fichier_sources().exists()

def test_mode_inconnu():
    r = solaire.run("MODE_INEXISTANT")
    assert r["status"] is False


# ─── GET_DATA ─────────────────────────────────────────────────────────────────

def test_get_data_pagination():
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    r = solaire.run("GET_DATA", params={"source_id": "luminosite_est_lx", "limit": 2, "offset": 0})
    assert r["status"] is True
    assert len(r["data"]["points"]) == 2
    assert r["data"]["total"] == 3

def test_get_data_ordre_decroissant():
    """GET_DATA retourne du plus récent au plus ancien."""
    solaire.run("IMPORT_CSV", params={"contenu": CSV_EST})
    r = solaire.run("GET_DATA", params={"source_id": "luminosite_est_lx", "limit": 10})
    pts = r["data"]["points"]
    ts_list = [p["ts"] for p in pts]
    assert ts_list == sorted(ts_list, reverse=True)

def test_get_data_source_inconnue():
    r = solaire.run("GET_DATA", params={"source_id": "inconnue", "limit": 10})
    assert r["status"] is True
    assert r["data"]["total"] == 0
