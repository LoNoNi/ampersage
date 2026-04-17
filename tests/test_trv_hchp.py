"""
test_trv_hchp.py - Tests unitaires pour scripts/tarif/trv_hchp/trv_hchp.py.
Couvre le parseur de plage HC, la détection HC/HP et cmd_update.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

TRV_HCHP_PATH = Path(__file__).parent.parent / "scripts" / "tarif" / "trv_hchp" / "trv_hchp.py"
TZ_PARIS = ZoneInfo("Europe/Paris")

TRANCHE_TEST = {
    "puissance": 6, "abonnement": 16.73,
    "prix_hp": 20.65, "prix_hc": 15.79,
    "extinction": False,
}
PARAM_TEST = {
    "puissance_souscrite": 6,
    "plage_hc": "22h00\u21926h00 + 12h00\u219214h00",
    "tranches": [TRANCHE_TEST],
}


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def module(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("trv_hchp", TRV_HCHP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod._instance, "data_file",  tmp_path / "trv_hchp_data.json")
    monkeypatch.setattr(mod._instance, "param_file", tmp_path / "trv_hchp_param.json")
    return mod


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _ecrire_param(tmp_path, contenu):
    (tmp_path / "trv_hchp_param.json").write_text(json.dumps(contenu), encoding="utf-8")


def _generer_records(n=48, kwh=0.5):
    """Génère 48 slots de 30 min à partir du 2026-03-25 00h00."""
    records = []
    base = datetime(2026, 3, 25, 0, 0, tzinfo=TZ_PARIS)
    for i in range(n):
        ts = base + timedelta(minutes=30 * i)
        records.append({"ts": ts.isoformat(), "kwh": kwh, "pmax": 1.0, "type": "reel"})
    return records


def _global_param(records=None):
    return {
        "api_conso_statut": True,
        "DATA": {"api_conso": {"records": records or []}},
    }


def _heure(h, m=0):
    return datetime(2026, 3, 25, h, m, tzinfo=TZ_PARIS)


# ─── Parseur plage HC ─────────────────────────────────────────────────────────


class TestParseurPlageHc:
    def test_creneau_unique(self, module):
        creneaux = module._parser_plage_hc("22h00\u21926h00")
        assert len(creneaux) == 1
        assert creneaux[0] == (22 * 60, 6 * 60)

    def test_deux_creneaux(self, module):
        creneaux = module._parser_plage_hc("22h00\u21926h00 + 12h00\u219214h00")
        assert len(creneaux) == 2
        assert creneaux[1] == (12 * 60, 14 * 60)

    def test_trois_creneaux(self, module):
        creneaux = module._parser_plage_hc("1h00\u21922h00 + 10h00\u219211h00 + 22h00\u219223h00")
        assert len(creneaux) == 3

    def test_avec_minutes(self, module):
        creneaux = module._parser_plage_hc("22h30\u21926h30")
        assert creneaux[0] == (22 * 60 + 30, 6 * 60 + 30)


# ─── Détection HC/HP ─────────────────────────────────────────────────────────


class TestEstHc:
    def test_creneau_traversant_minuit_nuit(self, module):
        """23h → HC (dans 22h00→6h00)."""
        creneaux = module._parser_plage_hc("22h00\u21926h00")
        assert module._est_hc(_heure(23), creneaux) is True

    def test_creneau_traversant_minuit_matin(self, module):
        """3h → HC (dans 22h00→6h00, après minuit)."""
        creneaux = module._parser_plage_hc("22h00\u21926h00")
        assert module._est_hc(_heure(3), creneaux) is True

    def test_hp_en_journee(self, module):
        """10h → HP (hors créneaux HC)."""
        creneaux = module._parser_plage_hc("22h00\u21926h00 + 12h00\u219214h00")
        assert module._est_hc(_heure(10), creneaux) is False

    def test_creneau_normal_dans_plage(self, module):
        """13h → HC (dans 12h00→14h00)."""
        creneaux = module._parser_plage_hc("12h00\u219214h00")
        assert module._est_hc(_heure(13), creneaux) is True

    def test_creneau_normal_hors_plage(self, module):
        """15h → HP (hors 12h00→14h00)."""
        creneaux = module._parser_plage_hc("12h00\u219214h00")
        assert module._est_hc(_heure(15), creneaux) is False

    def test_frontiere_debut_hc(self, module):
        """22h00 pile → HC."""
        creneaux = module._parser_plage_hc("22h00\u21926h00")
        assert module._est_hc(_heure(22, 0), creneaux) is True

    def test_frontiere_fin_hc(self, module):
        """6h00 pile → HP (fin exclusive)."""
        creneaux = module._parser_plage_hc("22h00\u21926h00")
        assert module._est_hc(_heure(6, 0), creneaux) is False


# ─── UPDATE ──────────────────────────────────────────────────────────────────


class TestUpdate:
    def test_nominal_nb_heures(self, module, tmp_path):
        """48 slots → 24 buckets horaires."""
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is True
        assert len(rep["data"]["heures"]) == 24

    def test_type_hc_a_minuit(self, module, tmp_path):
        """00h → HC (dans 22h00→6h00)."""
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        h = next(h for h in rep["data"]["heures"] if "T00:00:" in h["ts"])
        assert h["trv_hchp.type"] == "HC"
        assert h["trv_hchp.prix_kwh"] == "15.79"

    def test_type_hp_en_journee(self, module, tmp_path):
        """10h → HP."""
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        h = next(h for h in rep["data"]["heures"] if "T10:00:" in h["ts"])
        assert h["trv_hchp.type"] == "HP"
        assert h["trv_hchp.prix_kwh"] == "20.65"

    def test_type_hc_midi(self, module, tmp_path):
        """13h → HC (créneau 12h00→14h00)."""
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        h = next(h for h in rep["data"]["heures"] if "T13:00:" in h["ts"])
        assert h["trv_hchp.type"] == "HC"

    def test_prix_eur_hc(self, module, tmp_path):
        """kwh=0.5 × 2 slots = 1.0 kWh × 15.79 / 100 = 0.1579 €."""
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("UPDATE", global_param=_global_param(_generer_records(kwh=0.5)))
        h = next(h for h in rep["data"]["heures"] if "T00:00:" in h["ts"])
        assert h["trv_hchp.prix_eur"] == "0.1579"

    def test_api_conso_inactive(self, module):
        rep = module.run("UPDATE", global_param={"api_conso_statut": False})
        assert rep["status"] is False

    def test_param_absent(self, module):
        rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is False

    def test_sans_records(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("UPDATE", global_param=_global_param([]))
        assert rep["status"] is False

    def test_fichier_data_ecrit(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert (tmp_path / "trv_hchp_data.json").exists()


# ─── GET / GET_PARAM / SET_PARAM / INIT_PARAM ────────────────────────────────


class TestGet:
    def test_nominal(self, module, tmp_path):
        data = {"tarif": "trv_hchp", "heures": []}
        (tmp_path / "trv_hchp_data.json").write_text(json.dumps(data), encoding="utf-8")
        rep = module.run("GET")
        assert rep["status"] is True

    def test_absent(self, module):
        rep = module.run("GET")
        assert rep["status"] is False


class TestGetParam:
    def test_nominal(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("GET_PARAM")
        assert rep["status"] is True
        assert "plage_hc" in rep["data"]

    def test_absent(self, module):
        assert module.run("GET_PARAM")["status"] is False


class TestSetParam:
    def test_merge(self, module, tmp_path):
        _ecrire_param(tmp_path, {"puissance_souscrite": 6, "commentaire": "init"})
        rep = module.run("SET_PARAM", params={"puissance_souscrite": 9})
        assert rep["data"]["puissance_souscrite"] == 9
        assert rep["data"]["commentaire"] == "init"


class TestInitParam:
    def test_cree_fichier(self, module, tmp_path):
        rep = module.run("INIT_PARAM", global_param={"api_conso_statut": True})
        assert rep["status"] is True
        assert (tmp_path / "trv_hchp_param.json").exists()

    def test_api_conso_inactive(self, module):
        assert module.run("INIT_PARAM", global_param={"api_conso_statut": False})["status"] is False
