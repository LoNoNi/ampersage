# Changelog — Ampersage

## [Unreleased] — 2026-05-21

### Nouveau — Module Sobry (tarif dynamique EPEX Spot)

- Deux offres distinctes : **SoCap** (plafond mensuel bas) et **SoFlex** (plafond élevé)
- Calcul du prix basé sur l'API Sobry (`/api/prices/raw`) : spot EPEX + composante variable TURPE + marge + prime de couverture
- Deux grilles d'abonnement officielles intégrées (source : sobry.co/grille-tarifaire) :
  - **CU4** : de 9,76 €/mois HT (3 kVA) à 56,89 €/mois HT (36 kVA)
  - **MU4** : de 10,26 €/mois HT (3 kVA) à 62,92 €/mois HT (36 kVA)
- Puissance souscrite déduite automatiquement du pmax observé (plus de saisie manuelle)
- Choix CU4 / MU4 dans la tuile paramètres avec bascule immédiate des tableaux
- Bouton Sauvegarder sur la tuile : enregistre le TURPE et relance les calculs
- Cache custom vidé à chaque sauvegarde de paramètres (recalcul avec le bon TURPE)
- Plafond mensuel appliqué par saison (été / hiver) selon la moyenne des prix du mois

### Nouveau — Module Generique : support des offres custom

- Nouveau type d'offre `"custom"` avec champ `"module"` pour déléguer le calcul
- Templates HTML de rapport et de détail pour les offres custom
- Régénération systématique des fichiers de tarifs à chaque pipeline (suppression de la logique skip-if-exists)
- Intégration des offres **Engie** (Référence 3 ans, Tranquillité 1 an, C.A.R.) et **Happ-e by Engie**

### Nouveau — Onglet Détails : tooltips de décomposition

- **Colonne Prix Sobry** : popup au survol affichant Spot EPEX, Base TTC, Marge, Prime, Prix final (c€/kWh) avec indicateur de plafond activé (⚡)
- **Colonne CO₂ moyen** : popup affichant le mix de production par filière (%, MW) et le solde des échanges
- **Colonne CO₂ marginal** : popup affichant la méthode de calcul, le seuil et les filières retenues
- Classe CSS partagée `.detail-tip` pour tous les tooltips (dark, monospace, `white-space: pre`)

### Améliorations — Internationalisation (FR / EN)

- Remplacement de toutes les chaînes hardcodées par `t()` / `_tpl()`
- 12 nouvelles clés de traduction dans `languages.json` : tooltips prix, tooltips CO₂, labels période, sections paramètres, erreur plage HC

### Améliorations — TRV Tempo

- Refonte du module `trv_tempo.py` avec templates de détail et bandeau
- Panel paramètres mis à jour

### Améliorations — API Conso

- Mises à jour mineures de `api_conso.py` et de son panel HTML

### Corrections

- Grilles d'abonnement Sobry corrigées avec les valeurs officielles (l'ancien fichier contenait des valeurs incorrectes)
- Cache `_cache_custom` correctement vidé lors de tout `save_params` ou `trigger_update`
- Template `{{prix_spot}}` (non remplacé en JS) remplacé par `{{prix_kwh_tip}}` fonctionnel
- Fichiers de tarifs générés exclus du dépôt git (`.gitignore` mis à jour)

### Technique

- `.gitignore` : exclusion des fichiers `generique_tarif_*.json` et `tarif_*.json` générés au runtime
