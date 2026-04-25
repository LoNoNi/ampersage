"""
parametres.py — Configuration du module éCO2mix.
Ce fichier peut être modifié via l’interface de paramétrage d’AmperSage.
"""

URL_API_RTE = "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/eco2mix-national-tr/records"
URL_API_RTE_HISTORIQUE = "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/eco2mix-national-cons-def/records"  # noqa: E501
INTERVALLE_MIN_MINUTES = 15
TIMEOUT_SECONDES = 10
LIMITE_ENREGISTREMENTS = 100
FICHIER_DONNEES = "data/eco2mix_donnees.json"

# Facteurs d’émission par filière en gCO2eq/kWh (source : IPCC/ADEME)
# Utilisés UNIQUEMENT pour le calcul du CO2 marginal AmperSage.
FACTEURS_EMISSION = {
    "nucleaire": 12,
    "hydraulique": 6,
    "eolien_terrestre": 11,
    "eolien_offshore": 11,
    "solaire": 45,
    "bioenergies": 230,
    "gaz": 490,
    "fioul": 650,
    "charbon": 820,
}

# Fraction de la production totale retenue pour le calcul marginal
SEUIL_MARGINAL = 0.1

# Délai avant de re-tenter un créneau temporairement indisponible (heures)
DELAI_RETRY_INDISPONIBLE_HEURES = 12

# Délai avant apparition de l’infobulle (ms)
INFOBULLE_DELAI_MS = 200

# Seuil minimum d’affichage d’une filière dans l’infobulle (%)
INFOBULLE_FILIERES_MIN_PCT = 1
