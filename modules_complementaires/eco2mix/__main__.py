"""
__main__.py — Exécution autonome du module éCO2mix.
Usage : python -m modules_complementaires.eco2mix [mode] [date_heure]

Exemples :
  python -m modules_complementaires.eco2mix
  python -m modules_complementaires.eco2mix GET_CRENEAU 2026-04-17T14:00:00+02:00
  python -m modules_complementaires.eco2mix GET_JOURNEE 2026-04-17
  python -m modules_complementaires.eco2mix GET_STATS
"""

import json
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s - %(message)s")

from modules_complementaires.eco2mix.eco2mix import (
    calcul_co2_marginal,
    get_co2_creneau,
    get_stats_base,
    run,
)

TZ_PARIS = ZoneInfo("Europe/Paris")


def _afficher_creneau(c: dict):
    if c.get("statut") in ("erreur", "indisponible"):
        print("  [INDISPONIBLE] {}".format(c.get("raison", "")))
        return
    dt_str = c.get("date_heure", "")
    print("  Date/heure     : {}".format(dt_str))
    print("  Consommation   : {} MW".format(c.get("consommation_mw")))
    print("  Production tot : {} MW".format(c.get("production_totale_mw")))

    co2m = c.get("co2_moyen", {})
    co2mg = c.get("co2_marginal", {})
    print("  CO2 moyen (RTE): {} gCO2/kWh  [{}]".format(co2m.get("taux_gco2_kwh"), co2m.get("source")))
    print("  CO2 marginal   : {} gCO2/kWh  [seuil {}%]".format(
        co2mg.get("taux_gco2_kwh"),
        round((co2mg.get("seuil_pct") or 0) * 100),
    ))

    mix = c.get("mix", {})
    if mix:
        print("  Mix :")
        for filiere, mw in sorted(mix.items(), key=lambda x: -(x[1] or 0)):
            if (mw or 0) != 0:
                print("    {:20s} {:7.0f} MW".format(filiere, mw))

    echanges = c.get("echanges", {})
    export_net = sum(v for v in echanges.values() if isinstance(v, (int, float)))
    print("  Export net     : {:+.0f} MW".format(export_net))
    print("  Source         : {}".format(c.get("source_donnee", "?")))


def main():
    mode = sys.argv[1].upper() if len(sys.argv) > 1 else "GET_CRENEAU"

    if mode == "GET_STATS":
        stats = get_stats_base()
        print("\n=== Base locale éCO2mix ===")
        print("  Créneaux      : {}".format(stats["total_creneaux"]))
        print("  Premier       : {}".format(stats["premier_enregistrement"] or "—"))
        print("  Dernier       : {}".format(stats["dernier_enregistrement"] or "—"))
        print("  Taille        : {} Ko".format(stats["taille_fichier_ko"]))
        print("  Dernier fetch : {}".format(stats["dernier_fetch_api"] or "—"))
        return

    if mode == "GET_JOURNEE":
        date = sys.argv[2] if len(sys.argv) > 2 else datetime.now(TZ_PARIS).strftime("%Y-%m-%d")
        rep = run("GET_JOURNEE", {"date": date})
        data = rep.get("data", {})
        creneaux = data.get("creneaux", [])
        print("\n=== éCO2mix journée {} — {} créneaux ===".format(date, len(creneaux)))
        if creneaux:
            _afficher_creneau(creneaux[-1])
        return

    # Par défaut : créneau courant
    if len(sys.argv) > 2:
        date_heure = sys.argv[2]
    else:
        now = datetime.now(TZ_PARIS)
        date_heure = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0).isoformat()

    print("\n=== éCO2mix créneau courant ===")
    c = get_co2_creneau(date_heure)
    _afficher_creneau(c)

    print("\n=== Base locale ===")
    stats = get_stats_base()
    print("  Créneaux : {}  |  Dernier : {}  |  {} Ko".format(
        stats["total_creneaux"],
        stats["dernier_enregistrement"] or "—",
        stats["taille_fichier_ko"],
    ))


if __name__ == "__main__":
    main()
