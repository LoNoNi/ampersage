# CLAUDE.md - Contexte du projet pour Claude Code

## Localisation du projet

Le projet est développé dans `/home/alex/ampersage/`.
Le répertoire `/home/ampersage/` existe mais appartient à `root` (permissions insuffisantes pour l'utilisateur `alex`).

## Description

Outil Python modulaire pour simuler et comparer des factures d'électricité.
Phase actuelle : validation de l'architecture avec données mock avant intégration des vraies APIs.

## Stack technique

- Python 3.9.2 (attention : la syntaxe `dict | None` de Python 3.10 n'est pas supportée → utiliser `Optional[dict]` de `typing`, `List[str]` au lieu de `list[str]`)
- Flask pour le serveur web
- pytest pour les tests
- zoneinfo pour les timestamps Europe/Paris
- Pas de base de données : persistence via fichiers JSON

## Architecture

```
orchestrator.py      → Point d'entrée. Charge parametres.json → Global_param,
                       appelle enedis puis les scripts tarif, lance server.py.
                       Démarre le serveur en état dégradé si Enedis échoue.
main.py              → Génère le HTML de #main-content
server.py            → Flask : routes API + fichiers statiques web/
                       _relancer_pipeline() appelé après chaque save_params
                       GET /api/panel/<script> retourne le HTML brut du panel
                       _charger_script() cherche dans scripts/<nom>/<nom>.py
                       ET scripts/tarif/<nom>/<nom>.py
languages.json       → Traductions FR/EN dans web/ (servi via /static/)
parametres.json      → Créé au runtime, jamais committé
scripts/enedis/      → enedis.py (6 appels), enedis_data.json, enedis_param.json
scripts/tarif/       → _base_tarif.py (classe de base) + sobry, trv_base, trv_hchp, trv_tempo
                       Chaque tarif est dans son propre sous-répertoire
scripts/voiture/     → Vide, réservé pour plus tard
tests/               → test_enedis.py (pytest)
web/                 → index.html, app.js, style.css, languages.json
```

## Contrat commun des scripts

Chaque script expose `run(mode, params, global_param) -> dict` avec la structure :
```json
{ "status": true/false, "mode": "...", "error": null, "data": {} }
```
Modes : `GET`, `UPDATE`, `GET_PARAM`, `SET_PARAM`, `INIT_PARAM` (+ `GET_HISTORY_START` pour Enedis).

Les scripts tarif héritent de `_base_tarif.BaseTarif` et ne peuvent être initialisés que si `global_param["enedis_statut"] == True`.

`_decouvrir_scripts()` ignore les fichiers dont le nom commence par `_` (ex: `_base_tarif.py`).

## Panels HTML dynamiques

Chaque script tarif dispose d'un fichier `NOM_SCRIPT_panel.html` dans son répertoire.
Ce fichier contient le HTML + CSS + `<script>` du formulaire de paramétrage.

**Contrat du panel :**
- Les `<script>` injectés via `innerHTML` ne s'exécutent pas automatiquement → `chargerPanel()` dans `app.js` les recrée manuellement.
- Collecteur personnalisé : `window.getParams_NOM()` → retourne un objet avec tous les paramètres.
- Setter personnalisé : `window.setParams_NOM(data)` → peuple les champs depuis les paramètres sauvegardés.
- Fallback générique : éléments `[data-param]` si pas de collecteur/setter personnalisé.

## Scripts tarif — paramètres clés

### trv_base
- 9 tranches (3→36 kVA) : `puissance`, `abonnement` (€/mois), `prix_kwh` (c€/kWh), `extinction`
- Tableau éditable avec toggle extinction (orange)

### trv_hchp
- 9 tranches (3→36 kVA) : `puissance`, `abonnement` (€/mois HT), `prix_hp`, `prix_hc` (c€/kWh HT), `extinction`
- Champ `plage_hc` : plage horaire heures creuses, défaut `"22h00→6h00 + 12h00→14h00"`

### trv_tempo
- 7 tranches (6→36 kVA, pas de 3/24) : `puissance`, `abonnement` (€/mois TTC), 6 colonnes prix (bleu_hc/hp, blanc_hc/hp, rouge_hc/hp en c€/kWh TTC), `extinction`
- Champ `plage_hc` : plage horaire heures creuses, défaut `"22h00→6h00"`

## Enedis mock

- 6 appels : GET, UPDATE, GET_PARAM, SET_PARAM, INIT_PARAM, GET_HISTORY_START
- Valide le token avant UPDATE et GET_HISTORY_START
- Génère 3 jours de données glissantes (J-3 à J-1), pas de 30 min, profil réaliste avec coefficients par heure
- Adresse par défaut : `https://conso.boris.sh/api/`
- 144 records attendus (3j × 48 demi-heures)

## Global_param

Dictionnaire en mémoire partagé entre orchestrator et server.
**Jamais écrit automatiquement** dans `parametres.json` — uniquement sur action explicite de l'utilisateur (bouton Sauvegarder dans l'IHM → `POST /api/save_params`).

## Lancer les tests

```bash
/home/alex/.local/bin/pytest tests/ -v
```

## Lancer l'application

```bash
cd /home/alex/ampersage
python orchestrator.py
# Accessible sur http://localhost:8080
```

## Conventions

- Commentaires en français
- Logging Python standard (pas de `print`), niveau INFO par défaut
- Aucune donnée personnelle loggée (token, PDL)
- Timestamps toujours timezone-aware (Europe/Paris via `zoneinfo`)
