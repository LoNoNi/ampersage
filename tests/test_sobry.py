"""
test_sobry.py - Tests unitaires pour scripts/tarif/sobry/sobry.py.
Couvre : cache 2 niveaux (tarifs/resultats), multi-TURPE, produits SoCap/SoFlex,
         cap mensuel, recalcul sans API sur kwh changé, appels API mockés.
"""

import json
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SOBRY_PATH = Path(__file__).parent.parent / "scripts" / "tarif" / "sobry" / "sobry.py"

TRANCHE_TEST = {"puissance": 6, "abonnement": 12.52, "extinction": False}
PARAM_TEST = {
    "adresse":             "https://api.sobry.co/api/prices/raw",
    "puissance_souscrite": 6,
    "granularity":         "hourly",
    "display":             "TTC",
    "turpe_actifs":        ["CU"],
    "marge_ht":            0.0080,   # 0.80 c€/kWh
    "marge_ttc":           0.0096,   # 0.96 c€/kWh
    "socap": {
        "plafond_ete_ht":    0.1125, "plafond_ete_ttc":    0.1350,
        "plafond_hiver_ht":  0.2167, "plafond_hiver_ttc":  0.2600,
        "prime_ht":          0.0070, "prime_ttc":           0.0084,
    },
    "soflex": {
        "plafond_ete_ht":    0.3333, "plafond_ete_ttc":    0.4000,
        "plafond_hiver_ht":  0.3333, "plafond_hiver_ttc":  0.4000,
        "prime_ht":          0.0020, "prime_ttc":           0.0024,
    },
    "tranches": [TRANCHE_TEST],
}

# Valeurs de tarif renvoyées par le mock pour spot_price=10 €/MWh
# spot=0.010 €/kWh (1.0 c€/kWh)
#
# CU : price_ht=0.082, price_ttc=0.09840
#   socap TTC : (0.09840+0.0096+0.0084)×100 = 11.6400
#   socap HT  : (0.082  +0.0080+0.0070)×100 =  9.7000
#   soflex TTC: (0.09840+0.0096+0.0024)×100 = 11.0400
#
# LU : price_ht=0.060, price_ttc=0.07200
#   socap TTC : (0.07200+0.0096+0.0084)×100 =  9.0000
#   soflex TTC: (0.07200+0.0096+0.0024)×100 =  8.4000
_SPOT_EWMH  = 10.0
_SPOT_CEKWH = 1.0   # 10 €/MWh → 1.0 c€/kWh

_TURPE_MOCK_PRICES = {
    "CU":   {"ht": 0.082,   "ttc": 0.09840},
    "CU4":  {"ht": 0.079,   "ttc": 0.09480},
    "MU4":  {"ht": 0.069,   "ttc": 0.08280},
    "MUDT": {"ht": 0.067,   "ttc": 0.08040},
    "LU":   {"ht": 0.060,   "ttc": 0.07200},
}


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def module(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("sobry", SOBRY_PATH)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod._instance, "data_file",  tmp_path / "sobry_data.json")
    monkeypatch.setattr(mod._instance, "param_file", tmp_path / "sobry_param.json")
    monkeypatch.setattr(mod, "BASE_DIR", tmp_path)
    return mod


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _ecrire_param(tmp_path, contenu):
    (tmp_path / "sobry_param.json").write_text(json.dumps(contenu), encoding="utf-8")


def _generer_records(date="2026-03-25", n=48, kwh=0.5):
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


def _mock_urlopen(spot_price=_SPOT_EWMH, granularity="hourly"):
    """
    Retourne une side_effect qui parse le turpe= de l'URL
    et renvoie des entrées avec les 4 champs de tarification.
    """
    def side_effect(req, timeout=None):
        url    = req.full_url
        turpe  = "CU"
        for part in url.split("&"):
            if part.startswith("turpe="):
                turpe = part.split("=", 1)[1]
                break
        prices   = _TURPE_MOCK_PRICES.get(turpe, _TURPE_MOCK_PRICES["CU"])
        ht       = prices["ht"]
        ttc      = prices["ttc"]
        spot_eur = spot_price / 1000.0

        if granularity == "daily":
            entries = [{"timestamp": "2026-03-25T00:00:00+01:00",
                        "spot_price": spot_price, "spot_price_eur_kwh": spot_eur,
                        "price_ht_eur_kwh": ht, "price_ttc_eur_kwh": ttc}]
        elif granularity == "quarter_hourly":
            entries = [
                {"timestamp": "2026-03-25T{:02d}:{:02d}:00+01:00".format(h, m),
                 "spot_price": spot_price, "spot_price_eur_kwh": spot_eur,
                 "price_ht_eur_kwh": ht, "price_ttc_eur_kwh": ttc}
                for h in range(24) for m in (0, 15, 30, 45)
            ]
        else:
            entries = [
                {"timestamp": "2026-03-25T{:02d}:00:00+01:00".format(h),
                 "spot_price": spot_price, "spot_price_eur_kwh": spot_eur,
                 "price_ht_eur_kwh": ht, "price_ttc_eur_kwh": ttc}
                for h in range(24)
            ]

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"success": True, "count": len(entries), "data": entries}
        ).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__  = MagicMock(return_value=False)
        return mock_resp

    return side_effect


# ─── UPDATE nominal ───────────────────────────────────────────────────────────


class TestUpdate:
    def test_nominal_nb_heures(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is True
        assert len(rep["data"]["heures"]) == 24

    def test_turpe_actifs_dans_data(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["data"]["turpe_actifs"] == ["CU"]

    def test_produits_dans_data(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["data"]["produits"] == ["socap", "soflex"]

    def test_prix_spot(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen(_SPOT_EWMH)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["data"]["heures"][0]["sobry.prix_spot"] == "1.0000"

    def test_socap_ttc_cu(self, module, tmp_path):
        """SoCap CU TTC : (0.09840+0.0096+0.0084)×100 = 11.6400."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen(_SPOT_EWMH)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["data"]["heures"][0]["sobry.socap.CU.prix_kwh"] == "11.6400"

    def test_soflex_ttc_cu(self, module, tmp_path):
        """SoFlex CU TTC : (0.09840+0.0096+0.0024)×100 = 11.0400."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen(_SPOT_EWMH)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["data"]["heures"][0]["sobry.soflex.CU.prix_kwh"] == "11.0400"

    def test_socap_eur_cu(self, module, tmp_path):
        """1.0 kWh × 11.6400 / 100 = 0.1164 €."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen(_SPOT_EWMH)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records(kwh=0.5)))
        h = next(h for h in rep["data"]["heures"] if "T10:00:" in h["ts"])
        assert h["sobry.socap.CU.prix_eur"] == "0.1164"

    def test_api_conso_inactive(self, module):
        assert module.run("UPDATE", global_param={"api_conso_statut": False})["status"] is False

    def test_param_absent(self, module):
        assert module.run("UPDATE", global_param=_global_param(_generer_records()))["status"] is False

    def test_sans_records(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        assert module.run("UPDATE", global_param=_global_param([]))["status"] is False

    def test_fichier_data_ecrit(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()):
            module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert (tmp_path / "sobry_data.json").exists()


# ─── Multi-TURPE ─────────────────────────────────────────────────────────────


class TestMultiTurpe:
    def test_deux_turpe_dans_heures(self, module, tmp_path):
        """Avec CU + LU, chaque heure a les clés SoCap/SoFlex pour les deux TURPE."""
        param = dict(PARAM_TEST, turpe_actifs=["CU", "LU"])
        _ecrire_param(tmp_path, param)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen(_SPOT_EWMH)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        h = rep["data"]["heures"][0]
        assert "sobry.socap.CU.prix_kwh"  in h
        assert "sobry.socap.LU.prix_kwh"  in h
        assert h["sobry.socap.CU.prix_kwh"] == "11.6400"   # (0.09840+0.0096+0.0084)×100
        assert h["sobry.socap.LU.prix_kwh"] == "9.0000"    # (0.07200+0.0096+0.0084)×100

    def test_turpe_actifs_liste_dans_data(self, module, tmp_path):
        param = dict(PARAM_TEST, turpe_actifs=["CU", "LU"])
        _ecrire_param(tmp_path, param)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["data"]["turpe_actifs"] == ["CU", "LU"]

    def test_turpe_inconnu_ignore(self, module, tmp_path):
        param = dict(PARAM_TEST, turpe_actifs=["CU", "INCONNU"])
        _ecrire_param(tmp_path, param)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["data"]["turpe_actifs"] == ["CU"]
        assert "sobry.socap.CU.prix_kwh"      in  rep["data"]["heures"][0]
        assert "sobry.socap.INCONNU.prix_kwh" not in rep["data"]["heures"][0]

    def test_turpe_vide(self, module, tmp_path):
        """Aucun TURPE → spot présent mais aucune colonne prix produit."""
        param = dict(PARAM_TEST, turpe_actifs=[])
        _ecrire_param(tmp_path, param)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is True
        h = rep["data"]["heures"][0]
        assert "sobry.prix_spot"           in h
        assert "sobry.socap.CU.prix_kwh"  not in h
        assert "sobry.soflex.CU.prix_kwh" not in h


# ─── Affichage HT / TTC ──────────────────────────────────────────────────────


class TestDisplay:
    def test_socap_ht(self, module, tmp_path):
        """SoCap HT : (0.082+0.0080+0.0070)×100 = 9.7000."""
        _ecrire_param(tmp_path, dict(PARAM_TEST, display="HT"))
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen(_SPOT_EWMH)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["data"]["heures"][0]["sobry.socap.CU.prix_kwh"] == "9.7000"

    def test_socap_eur_ht(self, module, tmp_path):
        """1.0 kWh × 9.7000 / 100 = 0.0970 €."""
        _ecrire_param(tmp_path, dict(PARAM_TEST, display="HT"))
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen(_SPOT_EWMH)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records(kwh=0.5)))
        h = next(h for h in rep["data"]["heures"] if "T10:00:" in h["ts"])
        assert h["sobry.socap.CU.prix_eur"] == "0.0970"


# ─── Granularité ─────────────────────────────────────────────────────────────


class TestGranularite:
    def test_quarter_hourly(self, module, tmp_path):
        _ecrire_param(tmp_path, dict(PARAM_TEST, granularity="quarter_hourly"))
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen(_SPOT_EWMH, "quarter_hourly")):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is True
        assert rep["data"]["heures"][0]["sobry.prix_spot"] == "1.0000"

    def test_daily(self, module, tmp_path):
        _ecrire_param(tmp_path, dict(PARAM_TEST, granularity="daily"))
        with patch("urllib.request.urlopen",
                   side_effect=_mock_urlopen(_SPOT_EWMH, "daily")):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        spots = {h["sobry.prix_spot"] for h in rep["data"]["heures"]}
        assert spots == {"1.0000"}


# ─── Cap mensuel SoCap / SoFlex ──────────────────────────────────────────────


class TestCapMensuel:
    def test_cap_non_applique_si_sous_plafond(self, module, tmp_path):
        """Avg(price_ttc_CU) = 0.09840 < plafond_hiver_ttc=0.2600 → pas de cap, prix par heure."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen(_SPOT_EWMH)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        h = rep["data"]["heures"][0]
        assert h["sobry.socap.CU.prix_kwh"] == "11.6400"

    def test_cap_applique_si_dessus_plafond(self, module, tmp_path):
        """
        Plafond hiver TTC SoCap = 0.2600.
        Si on force price_ttc très élevé (0.30), avg(0.30) > 0.2600 →
        toutes les heures du mois utilisent plafond=0.2600 comme base.
        prix_socap = (0.2600 + 0.0096 + 0.0084) × 100 = 27.8000
        """
        # Modifier le plafond pour le rendre bas
        param = dict(PARAM_TEST)
        param["socap"] = dict(PARAM_TEST["socap"], plafond_hiver_ttc=0.090)
        _ecrire_param(tmp_path, param)
        # price_ttc_CU = 0.09840 > plafond=0.090 → cap déclenché
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen(_SPOT_EWMH)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        h = rep["data"]["heures"][0]
        # base = plafond = 0.090 → (0.090 + 0.0096 + 0.0084) × 100 = 10.8000
        assert h["sobry.socap.CU.prix_kwh"] == "10.8000"

    def test_cap_uniforme_sur_tout_le_mois(self, module, tmp_path):
        """Quand le cap est déclenché, toutes les heures du mois ont le même prix de base."""
        param = dict(PARAM_TEST)
        param["socap"] = dict(PARAM_TEST["socap"], plafond_hiver_ttc=0.090)
        _ecrire_param(tmp_path, param)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen(_SPOT_EWMH)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        heures = rep["data"]["heures"]
        prix_set = {h["sobry.socap.CU.prix_kwh"] for h in heures}
        assert len(prix_set) == 1   # même prix pour toutes les heures du mois capé


# ─── Erreurs ─────────────────────────────────────────────────────────────────


class TestErreurs:
    def test_erreur_api_url(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is False
        assert "connexion impossible" in rep["error"].lower()

    def test_erreur_api_http(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.HTTPError(None, 404, "Not Found", {}, None)):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is False
        assert "404" in rep["error"]

    def test_api_success_false(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"success": False}).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__  = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert rep["status"] is False


# ─── Cache ───────────────────────────────────────────────────────────────────


class TestCache:
    def test_cache_fichier_cree(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()):
            module.run("UPDATE", global_param=_global_param(_generer_records()))
        assert (tmp_path / "sobry_cache.json").exists()

    def test_cache_structure(self, module, tmp_path):
        """Cache : tarifs[CU][date][heure] et resultats[CU][ts] présents, pas de meta."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()):
            module.run("UPDATE", global_param=_global_param(_generer_records()))
        cache = json.loads((tmp_path / "sobry_cache.json").read_text(encoding="utf-8"))
        assert "tarifs"    in cache
        assert "resultats" in cache
        assert "meta"      not in cache
        assert "CU"         in cache["tarifs"]
        assert "CU"         in cache["resultats"]
        assert "2026-03-25" in cache["tarifs"]["CU"]
        # Les 4 champs sont présents dans les tarifs
        heure_0 = cache["tarifs"]["CU"]["2026-03-25"]["0"]
        for champ in ("spot_price", "spot_price_eur_kwh", "price_ht_eur_kwh", "price_ttc_eur_kwh"):
            assert champ in heure_0

    def test_cache_resultats_evite_recalcul(self, module, tmp_path):
        """2e UPDATE avec mêmes records → 0 appel API."""
        _ecrire_param(tmp_path, PARAM_TEST)
        gp = _global_param(_generer_records())
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()) as m1:
            module.run("UPDATE", global_param=gp)
            appels_1 = m1.call_count
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()) as m2:
            rep = module.run("UPDATE", global_param=gp)
            appels_2 = m2.call_count
        assert appels_1 > 0
        assert appels_2 == 0
        assert rep["status"] is True

    def test_cache_recalcul_si_kwh_change(self, module, tmp_path):
        """kwh différent → recalcul sans API (tarifs en cache)."""
        _ecrire_param(tmp_path, PARAM_TEST)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()):
            module.run("UPDATE", global_param=_global_param(_generer_records(kwh=0.5)))
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()) as m:
            rep = module.run("UPDATE", global_param=_global_param(_generer_records(kwh=1.0)))
            assert m.call_count == 0
        assert rep["status"] is True
        h = next(h for h in rep["data"]["heures"] if "T10:00:" in h["ts"])
        assert float(h["kwh"]) == 2.0

    def test_cache_pas_de_purge_sur_marge(self, module, tmp_path):
        """Changement de marge → pas d'appel API (tarifs en cache), prix SoCap recalculés."""
        _ecrire_param(tmp_path, PARAM_TEST)
        gp = _global_param(_generer_records())
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()):
            rep1 = module.run("UPDATE", global_param=gp)

        prix_initial = rep1["data"]["heures"][0]["sobry.socap.CU.prix_kwh"]

        _ecrire_param(tmp_path, dict(PARAM_TEST, marge_ttc=0.0200))
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()) as m:
            rep2 = module.run("UPDATE", global_param=gp)
            assert m.call_count == 0   # tarifs en cache, pas d'appel API

        # marge changée → prix SoCap recalculé depuis tarifs (différent de l'initial)
        prix_nouveau = rep2["data"]["heures"][0]["sobry.socap.CU.prix_kwh"]
        assert float(prix_nouveau) != float(prix_initial)
        # (0.09840 + 0.0200 + 0.0084) × 100 = 12.6800
        assert float(prix_nouveau) == pytest.approx(12.6800, abs=1e-4)

    def test_cache_purge_si_display_change(self, module, tmp_path):
        """TTC→HT avec kwh changé → nouveau calcul depuis tarifs (0 API)."""
        _ecrire_param(tmp_path, PARAM_TEST)
        gp = _global_param(_generer_records())
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()):
            module.run("UPDATE", global_param=gp)

        # Changer display ET kwh pour forcer recalcul des résultats bruts
        _ecrire_param(tmp_path, dict(PARAM_TEST, display="HT"))
        gp2 = _global_param(_generer_records(kwh=1.0))
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()) as m:
            rep = module.run("UPDATE", global_param=gp2)
            assert m.call_count == 0   # tarifs en cache

        assert rep["status"] is True
        # SoCap HT : (0.082 + 0.0080 + 0.0070) × 100 = 9.7000
        assert rep["data"]["heures"][0]["sobry.socap.CU.prix_kwh"] == "9.7000"

    def test_cache_turpe_partiel(self, module, tmp_path):
        """CU en cache, ajout de LU → LU calculé via API, CU réutilisé."""
        _ecrire_param(tmp_path, PARAM_TEST)
        gp = _global_param(_generer_records())
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()) as m1:
            module.run("UPDATE", global_param=gp)
            appels_cu = m1.call_count

        _ecrire_param(tmp_path, dict(PARAM_TEST, turpe_actifs=["CU", "LU"]))
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()) as m2:
            rep = module.run("UPDATE", global_param=gp)
            # CU réutilisé depuis cache résultats, LU nécessite 1 appel API
            assert m2.call_count == 1

        assert rep["status"] is True
        h = rep["data"]["heures"][0]
        assert h["sobry.socap.CU.prix_kwh"] == "11.6400"
        assert h["sobry.socap.LU.prix_kwh"] == "9.0000"

    def test_cache_tarifs_evite_appel(self, module, tmp_path):
        """Tarifs en cache pour le TURPE → recalcul sans API."""
        _ecrire_param(tmp_path, PARAM_TEST)
        heure_0 = {
            "spot_price": 10.0, "spot_price_eur_kwh": 0.010,
            "price_ht_eur_kwh": 0.082, "price_ttc_eur_kwh": 0.09840,
        }
        cache = {
            "tarifs":    {"CU": {"2026-03-25": {str(h): heure_0 for h in range(24)}}},
            "resultats": {},
        }
        (tmp_path / "sobry_cache.json").write_text(json.dumps(cache), encoding="utf-8")
        with patch("urllib.request.urlopen") as m:
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
            assert m.call_count == 0
        assert rep["status"] is True
        # SoCap TTC : (0.09840 + 0.0096 + 0.0084) × 100 = 11.6400
        assert rep["data"]["heures"][0]["sobry.socap.CU.prix_kwh"] == "11.6400"

    def test_max_appels_par_update(self, module, tmp_path):
        """Avec N TURPE actifs, au plus N appels API par UPDATE."""
        param = dict(PARAM_TEST, turpe_actifs=["CU", "LU", "MU4"])
        _ecrire_param(tmp_path, param)
        with patch("urllib.request.urlopen", side_effect=_mock_urlopen()) as m:
            rep = module.run("UPDATE", global_param=_global_param(_generer_records()))
            assert m.call_count <= 3
        assert rep["status"] is True


# ─── GET / GET_PARAM / SET_PARAM / INIT_PARAM ────────────────────────────────


class TestGet:
    def test_nominal(self, module, tmp_path):
        (tmp_path / "sobry_data.json").write_text(
            json.dumps({"tarif": "sobry", "heures": []}), encoding="utf-8"
        )
        assert module.run("GET")["status"] is True

    def test_absent(self, module):
        assert module.run("GET")["status"] is False


class TestGetParam:
    def test_nominal(self, module, tmp_path):
        _ecrire_param(tmp_path, PARAM_TEST)
        rep = module.run("GET_PARAM")
        assert rep["status"] is True
        assert "turpe_actifs" in rep["data"]
        assert "granularity"  in rep["data"]

    def test_absent(self, module):
        assert module.run("GET_PARAM")["status"] is False


class TestSetParam:
    def test_merge(self, module, tmp_path):
        _ecrire_param(tmp_path, {"turpe_actifs": ["CU"], "commentaire": "init"})
        rep = module.run("SET_PARAM", params={"turpe_actifs": ["LU"]})
        assert rep["data"]["turpe_actifs"] == ["LU"]
        assert rep["data"]["commentaire"]  == "init"


class TestInitParam:
    def test_cree_fichier(self, module, tmp_path):
        rep = module.run("INIT_PARAM", global_param={"api_conso_statut": True})
        assert rep["status"] is True
        assert (tmp_path / "sobry_param.json").exists()
        assert rep["data"]["turpe_actifs"] == ["CU"]
        assert rep["data"]["granularity"]  == "hourly"
        assert "socap"  in rep["data"]
        assert "soflex" in rep["data"]

    def test_api_conso_inactive(self, module):
        assert module.run("INIT_PARAM", global_param={"api_conso_statut": False})["status"] is False
