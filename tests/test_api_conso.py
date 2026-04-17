"""
test_api_conso.py - Tests unitaires pour scripts/api_conso/api_conso.py.
Couvre les 6 appels avec cas nominal et cas d'erreur.
Utilise pytest + tmp_path pour isoler les fichiers.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# Chemin vers le module à tester
API_CONSO_PATH = Path(__file__).parent.parent / "scripts" / "api_conso" / "api_conso.py"

# ─── Fixture : isolation des fichiers ────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolation_fichiers(tmp_path, monkeypatch):
    """
    Redirige DATA_FILE et PARAM_FILE vers un répertoire temporaire
    pour éviter toute pollution entre tests.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("api_conso", API_CONSO_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "DATA_FILE", tmp_path / "api_conso_data.json")
    monkeypatch.setattr(module, "PARAM_FILE", tmp_path / "api_conso_param.json")

    yield module


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _ecrire_data(tmp_path, contenu: dict):
    f = tmp_path / "api_conso_data.json"
    f.write_text(json.dumps(contenu), encoding="utf-8")


def _ecrire_param(tmp_path, contenu: dict):
    f = tmp_path / "api_conso_param.json"
    f.write_text(json.dumps(contenu), encoding="utf-8")


# ─── GET ─────────────────────────────────────────────────────────────────────


class TestGet:
    def test_nominal(self, isolation_fichiers, tmp_path):
        """Fichier data présent → retourne les records."""
        data = {"records": [{"ts": "2024-01-01T00:00:00+01:00", "kwh": 0.25, "type": "reel"}]}
        _ecrire_data(tmp_path, data)

        rep = isolation_fichiers.run("GET")

        assert rep["status"] is True
        assert rep["mode"] == "GET"
        assert "records" in rep["data"]
        assert len(rep["data"]["records"]) == 1

    def test_fichier_absent(self, isolation_fichiers):
        """Fichier data absent → status false + message d'erreur."""
        rep = isolation_fichiers.run("GET")

        assert rep["status"] is False
        assert rep["mode"] == "GET"
        assert "absent" in rep["error"].lower()


# ─── UPDATE ──────────────────────────────────────────────────────────────────


class TestUpdate:
    def test_nominal(self, isolation_fichiers, tmp_path):
        """Simule succès réseau avec token → data mise à jour et retournée."""
        _ecrire_param(tmp_path, {"adresse": "https://conso.boris.sh/api/", "token": "mon_token", "PDL": "12345678901234"})

        rep = isolation_fichiers.run("UPDATE")

        assert rep["status"] is True
        assert rep["mode"] == "UPDATE"
        assert "records" in rep["data"]
        assert len(rep["data"]["records"]) == 144  # 3 jours × 48 demi-heures

        # Vérifie que le fichier a été écrit
        data_file = tmp_path / "api_conso_data.json"
        assert data_file.exists()

    def test_token_absent(self, isolation_fichiers, tmp_path):
        """Token absent → status false."""
        _ecrire_param(tmp_path, {"adresse": "https://conso.boris.sh/api/", "token": None})

        rep = isolation_fichiers.run("UPDATE")

        assert rep["status"] is False
        assert "token" in rep["error"].lower()

    def test_erreur_reseau_timeout(self, isolation_fichiers, tmp_path):
        """Simule timeout → status false + retourne données existantes."""
        _ecrire_param(tmp_path, {"token": "mon_token"})
        donnees_existantes = {"records": [{"ts": "2024-01-01T00:00:00+01:00", "kwh": 0.1, "type": "reel"}]}
        _ecrire_data(tmp_path, donnees_existantes)

        rep = isolation_fichiers.run("UPDATE", params={"simulate_error": "timeout"})

        assert rep["status"] is False
        assert "timeout" in rep["error"].lower()
        assert rep["data"] == donnees_existantes

    def test_erreur_serveur_http500(self, isolation_fichiers, tmp_path):
        """Simule HTTP 500 → status false + retourne données existantes."""
        _ecrire_param(tmp_path, {"token": "mon_token"})
        donnees_existantes = {"records": [{"ts": "2024-01-01T00:00:00+01:00", "kwh": 0.1, "type": "reel"}]}
        _ecrire_data(tmp_path, donnees_existantes)

        rep = isolation_fichiers.run("UPDATE", params={"simulate_error": "http500"})

        assert rep["status"] is False
        assert "500" in rep["error"]
        assert rep["data"] == donnees_existantes


# ─── GET_PARAM ───────────────────────────────────────────────────────────────


class TestGetParam:
    def test_nominal(self, isolation_fichiers, tmp_path):
        """Fichier param présent → retourne les params."""
        param = {"adresse": "https://conso.boris.sh/api/", "PDL": "12345678901234"}
        _ecrire_param(tmp_path, param)

        rep = isolation_fichiers.run("GET_PARAM")

        assert rep["status"] is True
        assert rep["mode"] == "GET_PARAM"
        assert rep["data"]["PDL"] == "12345678901234"

    def test_fichier_absent(self, isolation_fichiers):
        """Fichier param absent → status false + message d'erreur."""
        rep = isolation_fichiers.run("GET_PARAM")

        assert rep["status"] is False
        assert "absent" in rep["error"].lower()


# ─── SET_PARAM ───────────────────────────────────────────────────────────────


class TestSetParam:
    def test_nominal_merge_partiel(self, isolation_fichiers, tmp_path):
        """Merge partiel : seul le champ modifié change."""
        param_initial = {
            "adresse": "https://conso.boris.sh/api/",
            "commentaire": "initial",
            "PDL": None,
            "token": None,
        }
        _ecrire_param(tmp_path, param_initial)

        rep = isolation_fichiers.run("SET_PARAM", params={"PDL": "12345678901234"})

        assert rep["status"] is True
        assert rep["data"]["PDL"] == "12345678901234"
        # Les autres champs sont préservés
        assert rep["data"]["adresse"] == "https://conso.boris.sh/api/"
        assert rep["data"]["commentaire"] == "initial"

    def test_pdl_invalide(self, isolation_fichiers, tmp_path):
        """PDL non numérique → status false."""
        _ecrire_param(tmp_path, {"adresse": "https://conso.boris.sh/api/"})

        rep = isolation_fichiers.run("SET_PARAM", params={"PDL": "INVALID_PDL"})

        assert rep["status"] is False
        assert "PDL" in rep["error"]


# ─── INIT_PARAM ──────────────────────────────────────────────────────────────


class TestInitParam:
    def test_nominal_cree_fichier(self, isolation_fichiers, tmp_path):
        """INIT_PARAM crée le fichier avec les valeurs par défaut."""
        rep = isolation_fichiers.run("INIT_PARAM")

        assert rep["status"] is True
        assert rep["data"]["adresse"] == "https://conso.boris.sh/api/"
        assert rep["data"]["token"] is None
        assert rep["data"]["PDL"] is None
        assert "history_start" in rep["data"]

        param_file = tmp_path / "api_conso_param.json"
        assert param_file.exists()

    def test_ecrasement_fichier_existant(self, isolation_fichiers, tmp_path):
        """Fichier existant avec valeurs custom → les défauts sont restaurés."""
        param_custom = {
            "adresse": "https://custom.example.com",
            "token": "mon_token_secret",
            "PDL": "12345678901234",
        }
        _ecrire_param(tmp_path, param_custom)

        rep = isolation_fichiers.run("INIT_PARAM")

        assert rep["status"] is True
        # Les valeurs par défaut écrasent les custom
        assert rep["data"]["adresse"] == "https://conso.boris.sh/api/"
        assert rep["data"]["token"] is None
        assert rep["data"]["PDL"] is None


# ─── GET_HISTORY_START ───────────────────────────────────────────────────────


class TestGetHistoryStart:
    def test_nominal(self, isolation_fichiers, tmp_path):
        """Simule appel API réussi → retourne 2020-01-01, met à jour global_param."""
        param = {"adresse": "https://conso.boris.sh/api/", "token": "mon_token", "PDL": "12345678901234"}
        _ecrire_param(tmp_path, param)

        global_param = {"api_conso_statut": True}
        rep = isolation_fichiers.run("GET_HISTORY_START", global_param=global_param)

        assert rep["status"] is True
        assert rep["data"]["max_history_start"] == "2020-01-01"
        assert global_param["max_history_start"] == "2020-01-01"

    def test_erreur_reseau_timeout(self, isolation_fichiers, tmp_path):
        """Simule timeout → retourne null dans global_param."""
        param = {"adresse": "https://conso.boris.sh/api/", "token": "mon_token", "PDL": "12345678901234"}
        _ecrire_param(tmp_path, param)

        global_param = {}
        rep = isolation_fichiers.run(
            "GET_HISTORY_START",
            params={"simulate_error": "timeout"},
            global_param=global_param,
        )

        assert rep["status"] is False
        assert "timeout" in rep["error"].lower()
        assert global_param.get("max_history_start") is None

    def test_token_absent(self, isolation_fichiers, tmp_path):
        """Token absent → status false."""
        param = {"adresse": "https://conso.boris.sh/api/", "token": None, "PDL": "12345678901234"}
        _ecrire_param(tmp_path, param)

        rep = isolation_fichiers.run("GET_HISTORY_START")

        assert rep["status"] is False
        assert "token" in rep["error"].lower()

    def test_pdl_non_configure(self, isolation_fichiers, tmp_path):
        """PDL = null → status false + message explicite."""
        param = {"adresse": "https://conso.boris.sh/api/", "token": "mon_token", "PDL": None}
        _ecrire_param(tmp_path, param)

        rep = isolation_fichiers.run("GET_HISTORY_START")

        assert rep["status"] is False
        assert "PDL" in rep["error"]
