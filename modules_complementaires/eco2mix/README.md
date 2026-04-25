# Module complémentaire — éCO2mix

Récupère et expose les données de mix électrique national et de CO2 en temps réel
depuis l'API publique RTE (ODRE — jeu de données `eco2mix-national-tr`).

## Méthodes CO2

| Méthode | Source | Calcul |
|---------|--------|--------|
| **CO2 moyen** | `taux_co2` fourni par RTE | Stocké tel quel, sans transformation |
| **CO2 marginal** | Calculé par AmperSage | MW par filière × FACTEURS_EMISSION, filières triées par facteur décroissant, cumul jusqu'à SEUIL_MARGINAL × production totale |

> Modifier un facteur d'émission dans les paramètres n'affecte **que** le CO2 marginal.
> Le CO2 moyen reste le taux officiel transmis par RTE.

## Persistance locale

- Fichier : `data/eco2mix_donnees.json`
- L'API RTE n'est appelée **que si** le créneau demandé est absent du fichier local
- Toute donnée reçue est immédiatement persistée, **sans purge**
- Plusieurs créneaux manquants → **un seul appel** couvrant la plage complète
- Clé du dict `creneaux` = ISO 8601 avec timezone (ex : `2026-04-17T14:00:00+02:00`)

## Structure du fichier de données

```json
{
  "meta": {
    "module": "eco2mix",
    "version": "1.0",
    "source": "RTE éCO2mix",
    "premier_enregistrement": "2026-04-16T00:00:00+02:00",
    "dernier_enregistrement":  "2026-04-17T14:00:00+02:00",
    "total_creneaux": 100
  },
  "creneaux": {
    "2026-04-17T14:00:00+02:00": {
      "consommation": 38747,
      "mix": {
        "nucleaire": 41554, "hydraulique": 5907, "gaz": 1447,
        "eolien_terrestre": 2612, "eolien_offshore": 700, "solaire": 0,
        "bioenergies": 1176, "fioul": 36, "charbon": 0, "pompage": -1925
      },
      "echanges": {
        "angleterre": -1554, "espagne": -2500, "italie": -4138,
        "suisse": -2385, "allemagne_belgique": -2837
      },
      "taux_co2_rte": 23,
      "fetch_timestamp": "2026-04-17T14:05:12+02:00"
    }
  }
}
```

## Modes exposés (contrat `run()`)

| Mode | Description |
|------|-------------|
| `GET` | Dernier créneau disponible en base locale |
| `UPDATE` | Récupère la dernière heure de données (API si manquant) |
| `GET_PARAM` | Paramètres actuels (depuis `parametres.py`) |
| `SET_PARAM` | Met à jour `parametres.py` + reload + recalcul marginal |
| `INIT_PARAM` | Réinitialise `parametres.py` aux valeurs IPCC/ADEME |
| `GET_STATS` | Statistiques de la base locale |
| `GET_CRENEAU` | CO2 pour un créneau donné (`params["date_heure"]`) |
| `GET_JOURNEE` | 96 créneaux d'une journée (`params["date"]`) |

## Exécution autonome

```bash
# Créneau courant (appel API si absent du cache)
python -m modules_complementaires.eco2mix

# Créneau spécifique
python -m modules_complementaires.eco2mix GET_CRENEAU 2026-04-17T14:00:00+02:00

# Journée complète
python -m modules_complementaires.eco2mix GET_JOURNEE 2026-04-17

# Statistiques base locale
python -m modules_complementaires.eco2mix GET_STATS
```

## Infobulle page principale

Le panel injecte un composant JS autonome qui se lie automatiquement à tout élément
portant l'attribut `data-eco2mix-creneau` contenant les données JSON du créneau.

Exemple d'intégration dans `main.py` :

```html
<span
  data-eco2mix-creneau='{"date_heure":"...","co2_moyen":{"taux_gco2_kwh":23},...}'
  data-eco2mix-delai="200"
  data-eco2mix-min-pct="1"
>
  23 g/kWh
</span>
```

Couleurs des filières :
- Nucléaire `#534AB7` · Hydraulique `#1D9E75` · Éolien terrestre `#378ADD`
- Éolien offshore `#185FA5` · Solaire `#EF9F27` · Bioénergies `#639922`
- Gaz `#BA7517` · Fioul `#D85A30` · Charbon `#888780`

## Intégration `/api/expose`

```json
{
  "modules_complementaires": {
    "eco2mix": {
      "statut": "actif",
      "description": "Mix électrique national et CO2 en temps réel (RTE éCO2mix)",
      "note_methodes": "CO2 moyen = taux officiel RTE / CO2 marginal = calcul AmperSage",
      "parametres": { ... },
      "base_locale": { ... },
      "taux_co2_moyen_actuel": 23,
      "taux_co2_marginal_actuel": 85.3
    }
  }
}
```
