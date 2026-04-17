"""
test_trv_tempo.py - Tests unitaires pour scripts/tarif/trv_tempo/trv_tempo.py.
Couvre : date_tempo, cache couleurs, cache résultats, appels API mockés.
"""

import json
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

TRV_TEMPO_PATH = Path(__file__).parent.parent / "scripts" / "tarif" / "trv_tempo" / "trv_tempo.py"
TZ_PARIS = ZoneInfo("Europe/Paris")

TRANCHE_TEST = {
    "puissance": 6, "abonnement": 15.59, "extinction": False,
    "bleu_hc": 13.25, "bleu_hp": 16.12,
    "blanc_hc": 14.99, "blanc_hp": 18.71,
    "rouge_hc": 15.75, "rouge_hp": 70.60,
}
PARAM_TEST = {
    "adresse":               "https://api-couleur-tempo.fr/api",
    "puissance_souscrite":   6,
    "plage_hc":              "22h00\u21926h00",
    "heure_changement_jour": "6h00",
    "tranches":              [TRANCHE_TEST],
}


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def module(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("trv_tempo", TRV_TEMPO_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod._instance, "data_file",  tmp_path / "trv_tempo_data.json")
    monkeypatch.setattr(mod._instance, "param_file", tmp_path / "trv_tempo_param.json")
    monkeypatch.setattr(mod, "BASE_DIR", tmp_path)
    return mod


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _ecrire_param(tmp_path, contenu):
    (tmp_path / "trv_tempo_param.json").write_text(json.dumps(contenu), encoding="utf-8")


def _generer_records(date="2026-03-25", n=48, kwh=0.5):
    """Génère n slots de 30 min à partir de date 00h00."""
    base = datetime.fromisoformat("{}T00:00:00+01:00".format(date))
    records = []
    for i in range(n):
        ts = base + timedelta(minutes=30 * i)
        records.append({"ts": ts.isoformat(), "kwh": kwh, "pmax": 1.0, "type": "reel"})
    return records


def _global_param(records=None):
    return {
        "api_conso_statut": True,
        "DATA": {"api_conso": {"records": records or []}},
    }


def _mock_urlopen(code_jour=1):
    """Retourne un faux contexte urllib avec codeJour."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"codeJour": code_jour}).encode("utf-8")
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ─── Helpers date Tempo ───────────────────────────────────────────────────────


class TestDateTempo:
    def test_avant_changement(self, module):
        """4h → date J-1 (avant 6h00)."""
        dt   = datetime(2026, 3, 25, 4, 0, tzinfo=TZ_PARIS)
        date = module._date_tempo(dt, 6 * 60)
        assert date.isoformat() == "2026-03-24"

    def test_apres_changement(self, module):
        """8h → date J."""
        dt   = datetime(2026, 3, 25, 8, 0, tzinfo=TZ_PARIS)
        date = module._date_tempo(dt, 6 * 60)
        assert date.isoformat() == "2026-03-25"

    def test_exactement_au_changement(self, module):
        """6h00 pile → date J (borne inclusive)."""
        dt   = datetime(2026, 3, 25, 6, 0, tzinfo=TZ_PARIS)
        date = module._date_tempo(dt, 6 * 60)
        assert date.isoformat() == "2026-03-25"

    def test_parametre_changement_different(self, module):
        """Changement à 7h00 → 6h reste J-1."""
        dt   = datetime(2026, 3, 25, 6, 0, tzinfo=TZ_PARIS)
        date = module._date_tempo(dt, 7 * 60)
        assert date.isoformat() == "2026-03-24"


# ─── UPDATE nominal ───────────────────────────────────────────────────────────


class TestUpdate:
    def test_nominal_bleu_hp(self, module, tmp_path):
        """10h BLEU → HP → prix 16.12 c€/kWh."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(1)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is True
        h = next(h for h in rep["data"]["heures"] if "T10:00:" in h["ts"])
        assert h["trv_tempo.couleur"] == "BLEU"
        assert h["trv_tempo.type"] == "HP"
        assert h["trv_tempo.prix_kwh"] == "16.12"

    def test_nominal_blanc(self, module, tmp_path):
        """BLANC HP → prix 18.71."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(2)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is True
        h = next(h for h in rep["data"]["heures"] if "T10:00:" in h["ts"])
        assert h["trv_tempo.couleur"] == "BLANC"
        assert h["trv_tempo.prix_kwh"] == "18.71"

    def test_nominal_rouge_hc(self, module, tmp_path):
        """23h ROUGE → HC → prix 15.75."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(3)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is True
        h = next(h for h in rep["data"]["heures"] if "T23:00:" in h["ts"])
        assert h["trv_tempo.couleur"] == "ROUGE"
        assert h["trv_tempo.type"] == "HC"
        assert h["trv_tempo.prix_kwh"] == "15.75"

    def test_prix_eur_calcule(self, module, tmp_path):
        """kwh=1.0 × bleu_hp=16.12 / 100 = 0.1612 €."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(1)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records(kwh=0.5)))
        h = next(h for h in rep["data"]["heures"] if "T10:00:" in h["ts"])
        # 2 slots × 0.5 = 1.0 kWh × 16.12 / 100 = 0.1612
        assert h["trv_tempo.prix_eur"] == "0.1612"

    def test_couleur_class_injectee(self, module, tmp_path):
        """trv_tempo.couleur_class doit être présent dans chaque heure."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(1)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        for h in rep["data"]["heures"]:
            assert "trv_tempo.couleur_class" in h
            assert h["trv_tempo.couleur_class"] == "tempo-bleu"

    def test_date_tempo_heures_avant_6h(self, module, tmp_path):
        """Les heures 00h-05h utilisent la couleur du jour J-1."""
        _ecrire_param(tmp_path, PARAM_TEST)
        # On mock deux couleurs différentes selon la date
        appels = []
        def mock_urlopen_dates(req, timeout=None):
            appels.append(req.full_url)
            return _mock_urlopen(1)
        with patch("urllib.request.urlopen", side_effect=mock_urlopen_dates):
            module.run("UPDATE", global_param=_global_param(_generer_records()))
        # Doit avoir demandé 2 dates : 2026-03-24 (avant 6h) et 2026-03-25 (après 6h)
        dates_demandees = {url.split("/")[-1] for url in appels}
        assert "2026-03-24" in dates_demandees
        assert "2026-03-25" in dates_demandees

    def test_nb_heures(self, module, tmp_path):
        """48 slots → 24 buckets horaires."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(1)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert len(rep["data"]["heures"]) == 24


# ─── Erreurs ─────────────────────────────────────────────────────────────────


class TestErreurs:
    def test_erreur_api_url(self, module, tmp_path):
        """Erreur réseau → status false, pas de données."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is False
        assert "connexion impossible" in rep["error"].lower()

    def test_erreur_api_http(self, module, tmp_path):
        """HTTP 404 → status false."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.HTTPError(None, 404, "Not Found", {}, None)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is False
        assert "404" in rep["error"]

    def test_code_jour_inconnu(self, module, tmp_path):
        """codeJour hors [1,2,3] → status false."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(99)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is False

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


# ─── Cache ───────────────────────────────────────────────────────────────────


class TestCache:
    def test_cache_fichier_cree(self, module, tmp_path):
        """Le fichier cache est créé après UPDATE."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(1)):
            module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert (tmp_path / "trv_tempo_cache.json").exists()

    def test_cache_structure(self, module, tmp_path):
        """Le cache contient les clés 'couleurs' et 'resultats'."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(2)):
            module.run("UPDATE", global_param=_global_param(_generer_records()))
        cache = json.loads((tmp_path / "trv_tempo_cache.json").read_text(encoding="utf-8"))
        assert "couleurs" in cache
        assert "resultats" in cache
        assert "2026-03-24" in cache["couleurs"]   # heures avant 6h
        assert "2026-03-25" in cache["couleurs"]   # heures après 6h
        assert cache["couleurs"]["2026-03-24"] == "BLANC"

    def test_cache_resultats_evite_recalcul(self, module, tmp_path):
        """Deuxième UPDATE avec mêmes records → zéro appel API."""
        _ecrire_param(tmp_path, PARAM_TEST)
        gp = _global_param(_generer_records())

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(1)) as m1:
            module.run("UPDATE", global_param=gp)
            appels_1 = m1.call_count

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(1)) as m2:
            rep = module.run("UPDATE", global_param=gp)
            appels_2 = m2.call_count

        assert appels_1 > 0
        assert appels_2 == 0
        assert rep["status"] is True

    def test_cache_recalcul_si_kwh_change(self, module, tmp_path):
        """kwh différent → recalcul, mais couleur depuis cache (pas d'appel API)."""
        _ecrire_param(tmp_path, PARAM_TEST)

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(1)):
            module.run("UPDATE", global_param=_global_param(_generer_records(kwh=0.5)))

        with patch("urllib.request.urlopen", return_value=_mock_urlopen(1)) as m:
            rep = module.run("UPDATE", global_param=_global_param(_generer_records(kwh=1.0)))
            assert m.call_count == 0   # couleur déjà en cache

        assert rep["status"] is True
        h = next(h for h in rep["data"]["heures"] if "T10:00:" in h["ts"])
        assert float(h["kwh"]) == 2.0  # 2 slots × 1.0

    def test_cache_couleur_evite_appel(self, module, tmp_path):
        """Cache couleur présent, résultat absent → recalcul sans appel API."""
        _ecrire_param(tmp_path, PARAM_TEST)

        # Pré-remplir uniquement le cache couleurs
        cache = {
            "couleurs":  {"2026-03-24": "BLEU", "2026-03-25": "BLEU"},
            "resultats": {},
        }
        (tmp_path / "trv_tempo_cache.json").write_text(
            json.dumps(cache), encoding="utf-8"
        )

        with patch("urllib.request.urlopen") as m:
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
            assert m.call_count == 0

        assert rep["status"] is True


# ─── GET / GET_PARAM / SET_PARAM / INIT_PARAM ────────────────────────────────


class TestGet:
    def test_nominal(self, module, tmp_path):
        data = {"tarif": "trv_tempo", "heures": []}
        (tmp_path / "trv_tempo_data.json").write_text(json.dumps(data), encoding="utf-8")
        assert module.run("GET")["status"] is True

    def test_absent(self, module):
        assert module.run("GET")["status"] is False


class TestGetParam:
    def test_nominal(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("GET_PARAM")
        assert rep["status"] is True
        assert "heure_changement_jour" in rep["data"]

    def test_absent(self, module):
        assert module.run("GET_PARAM")["status"] is False


class TestSetParam:
    def test_merge(self, module, tmp_path):
        _ecrire_param(tmp_path, {"heure_changement_jour": "6h00", "commentaire": "init"})
        rep = module.run("SET_PARAM", params={"heure_changement_jour": "7h00"})
        assert rep["data"]["heure_changement_jour"] == "7h00"
        assert rep["data"]["commentaire"] == "init"


class TestInitParam:
    def test_cree_fichier(self, module, tmp_path):
        rep = module.run("INIT_PARAM", global_param={"api_conso_statut": True})
        assert rep["status"] is True
        assert (tmp_path / "trv_tempo_param.json").exists()
        assert rep["data"]["heure_changement_jour"] == "6h00"

    def test_api_conso_inactive(self, module):
        assert module.run("INIT_PARAM", global_param={"api_conso_statut": False})["status"] is False
