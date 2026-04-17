"""
test_trv_base.py - Tests unitaires pour scripts/tarif/trv_base/trv_base.py.
Couvre cmd_update (calcul réel) et les 5 appels de base.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

TRV_BASE_PATH = Path(__file__).parent.parent / "scripts" / "tarif" / "trv_base" / "trv_base.py"
TZ_PARIS = ZoneInfo("Europe/Paris")

TRANCHE_6KVA = {"puissance": 6, "abonnement": 15.65, "prix_kwh": 19.4, "extinction": False}
PARAM_TEST = {"puissance_souscrite": 6, "tranches": [TRANCHE_6KVA]}


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def module(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("trv_base", TRV_BASE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod._instance, "data_file",  tmp_path / "trv_base_data.json")
    monkeypatch.setattr(mod._instance, "param_file", tmp_path / "trv_base_param.json")
    return mod


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _ecrire_param(tmp_path, contenu):
    (tmp_path / "trv_base_param.json").write_text(json.dumps(contenu), encoding="utf-8")


def _generer_records(n_jours=1, kwh=0.5):
    """Génère des records mock (48 slots de 30 min par jour)."""
    records = []
    base = datetime(2026, 3, 25, 0, 0, tzinfo=TZ_PARIS)
    for j in range(n_jours):
        for i in range(48):
            ts = base + timedelta(days=j, minutes=30 * i)
            records.append({"ts": ts.isoformat(), "kwh": kwh, "pmax": 1.0, "type": "reel"})
    return records


def _global_param(records=None):
    return {
        "api_conso_statut": True,
        "DATA": {"api_conso": {"records": records or []}},
    }


# ─── UPDATE ──────────────────────────────────────────────────────────────────


class TestUpdate:
    def test_nominal_nb_heures(self, module, tmp_path):
        """1 jour de records → 24 buckets horaires."""
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("UPDATE", global_param=_global_param(_generer_records(1)))
        assert rep["status"] is True
        assert len(rep["data"]["heures"]) == 24

    def test_nominal_prix_calcule(self, module, tmp_path):
        """kwh=0.5 par slot × 2 slots = 1.0 kWh × 19.4 / 100 = 0.1940 €."""
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("UPDATE", global_param=_global_param(_generer_records(kwh=0.5)))
        assert rep["status"] is True
        h = rep["data"]["heures"][0]
        assert h["trv_base.prix"] == "0.1940"
        assert h["kwh"] == 1.0

    def test_nominal_fichier_ecrit(self, module, tmp_path):
        """Le fichier data doit être créé après UPDATE."""
        _ecrire_param(tmp_path, PARAM_TEST)
        module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert (tmp_path / "trv_base_data.json").exists()

    def test_api_conso_inactive(self, module):
        """API conso inactive → status false."""
        rep = module.run("UPDATE", global_param={"api_conso_statut": False})
        assert rep["status"] is False
        assert "api conso" in rep["error"].lower()

    def test_param_absent(self, module):
        """Fichier param absent → status false."""
        rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is False
        assert "param" in rep["error"].lower()

    def test_sans_records(self, module, tmp_path):
        """Aucun record → status false."""
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("UPDATE", global_param=_global_param([]))
        assert rep["status"] is False

    def test_tranche_introuvable(self, module, tmp_path):
        """Puissance souscrite absente des tranches → status false."""
        _ecrire_param(tmp_path, {
            "puissance_souscrite": 99,
            "tranches": [TRANCHE_6KVA],
        })
        rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is False
        assert "tranche" in rep["error"].lower()

    def test_tranche_fallback_premiere_active(self, module, tmp_path):
        """Puissance souscrite non configurée → prend la première tranche active."""
        _ecrire_param(tmp_path, {
            "puissance_souscrite": None,
            "tranches": [TRANCHE_6KVA],
        })
        rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is True

    def test_plusieurs_jours(self, module, tmp_path):
        """3 jours de records → 72 buckets horaires."""
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("UPDATE", global_param=_global_param(_generer_records(3)))
        assert rep["status"] is True
        assert len(rep["data"]["heures"]) == 72


# ─── GET ─────────────────────────────────────────────────────────────────────


class TestGet:
    def test_nominal(self, module, tmp_path):
        data = {"tarif": "trv_base", "heures": [{"ts": "2026-03-25T00:00:00+01:00", "kwh": 1.0}]}
        (tmp_path / "trv_base_data.json").write_text(json.dumps(data), encoding="utf-8")
        rep = module.run("GET")
        assert rep["status"] is True
        assert rep["data"]["tarif"] == "trv_base"

    def test_fichier_absent(self, module):
        rep = module.run("GET")
        assert rep["status"] is False
        assert "absent" in rep["error"].lower()


# ─── GET_PARAM ───────────────────────────────────────────────────────────────


class TestGetParam:
    def test_nominal(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("GET_PARAM")
        assert rep["status"] is True
        assert rep["data"]["puissance_souscrite"] == 6

    def test_absent(self, module):
        rep = module.run("GET_PARAM")
        assert rep["status"] is False


# ─── SET_PARAM ───────────────────────────────────────────────────────────────


class TestSetParam:
    def test_merge_partiel(self, module, tmp_path):
        """Seul le champ modifié change, les autres sont préservés."""
        _ecrire_param(tmp_path, {"puissance_souscrite": 6, "commentaire": "initial"})
        rep = module.run("SET_PARAM", params={"puissance_souscrite": 9})
        assert rep["status"] is True
        assert rep["data"]["puissance_souscrite"] == 9
        assert rep["data"]["commentaire"] == "initial"


# ─── INIT_PARAM ──────────────────────────────────────────────────────────────


class TestInitParam:
    def test_cree_fichier_defaut(self, module, tmp_path):
        rep = module.run("INIT_PARAM", global_param={"api_conso_statut": True})
        assert rep["status"] is True
        assert (tmp_path / "trv_base_param.json").exists()
        assert rep["data"]["puissance_souscrite"] == 6

    def test_api_conso_inactive(self, module):
        rep = module.run("INIT_PARAM", global_param={"api_conso_statut": False})
        assert rep["status"] is False
