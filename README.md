# Ampersage — Comparateur de factures d'électricité

Outil Python modulaire pour visualiser sa consommation électrique et comparer les offres tarifaires (TRV Base, TRV HC/HP, TRV Tempo, Sobry).

Les données de consommation sont récupérées via l'API [conso.boris.sh](https://conso.boris.sh) (proxy Enedis). Un import CSV Enedis est également disponible pour l'historique.

---

## Prérequis

- Python 3.9+
- pip

---

## Installation

```bash
git clone https://github.com/VOTRE_COMPTE/ampersage.git
cd ampersage
pip install -r requirements.txt
```

---

## Lancement

### Mode terminal (développement / test)

```bash
python orchestrator.py
```

L'interface est accessible sur **http://localhost:8080**.  
Les logs s'affichent dans le terminal et sont écrits dans `logs/ampersage.log`.

### Mode démon — service systemd (production)

Le script `ampersage-service.sh` gère l'installation et le cycle de vie du service.

**Première installation (démarre automatiquement au boot) :**

```bash
./ampersage-service.sh install
./ampersage-service.sh start
```

**Commandes courantes :**

```bash
./ampersage-service.sh start      # Démarrer
./ampersage-service.sh stop       # Arrêter
./ampersage-service.sh restart    # Redémarrer
./ampersage-service.sh status     # État du service
./ampersage-service.sh logs       # Suivre les logs en temps réel
./ampersage-service.sh uninstall  # Désinstaller le service
```

> Les logs sont également disponibles dans `logs/ampersage.log` (rotation automatique : 50 Mo max, 7 fichiers conservés).

---

Au premier démarrage, l'application fonctionne en mode démo (données simulées). Pour activer les vraies données :

1. Aller dans **Paramètres → API Conso**
2. Obtenir un token sur [conso.boris.sh](https://conso.boris.sh)
3. Coller le token et sauvegarder — le PRM est extrait automatiquement du token

---

## Configuration

### Fichiers de configuration

| Fichier | Rôle | Committer ? |
|---|---|---|
| `parametres.json` | Paramètres globaux runtime | Non (généré) |
| `scripts/api_conso/api_conso_param.json` | Token + PRM (données personnelles) | **Non** |
| `scripts/tarif/*/xxx_param.json` | Paramètres des tarifs | Oui (données publiques) |

Au premier démarrage sans `api_conso_param.json`, copier le modèle :

```bash
cp scripts/api_conso/api_conso_param.json.example scripts/api_conso/api_conso_param.json
```

---

## Architecture

```
orchestrator.py          Point d'entrée : charge parametres.json, appelle
                         api_conso puis les scripts tarif, lance server.py
main.py                  Génère le HTML du tableau de consommation
server.py                Serveur Flask : routes API + fichiers statiques

scripts/
  api_conso/
    api_conso.py         Connexion à l'API conso (conso.boris.sh / Enedis)
                         Modes : GET, UPDATE, GET_PARAM, SET_PARAM,
                                 INIT_PARAM, GET_HISTORY_START, IMPORT_CSV
    api_conso_param.json.example   Modèle de configuration (sans données perso)

  tarif/
    _base_tarif.py       Classe de base partagée par les scripts tarif
    sobry/               Tarif Sobry (prix spot EPEX)
    trv_base/            TRV option Base
    trv_hchp/            TRV option Heures Creuses / Heures Pleines
    trv_tempo/           TRV option Tempo (jours Bleu/Blanc/Rouge)

  voiture/               Réservé — calcul coût de recharge (à venir)

web/
  index.html             Interface principale
  app.js                 Logique cliente
  style.css              Feuille de style
  languages.json         Traductions FR / EN

tests/                   Tests pytest
```

### Contrat des scripts

Chaque script expose `run(mode, params, global_param) -> dict` :

```json
{ "status": true, "mode": "UPDATE", "error": null, "data": {} }
```

### Flux d'exécution

1. `orchestrator.py` charge (ou initialise) `parametres.json` → `Global_param`
2. `api_conso.py UPDATE` — appel API ou mode démo si pas de token
3. Scripts tarif `UPDATE` (indépendants, en parallèle)
4. `server.py` démarre sur le port configuré (défaut : 8080)

---

## Import historique CSV

Dans **Paramètres → API Conso**, une zone de dépôt permet d'importer un fichier CSV Enedis (`debut;fin;kW`, pas 10 min). Les données sont agrégées en pas de 30 min et fusionnées avec l'historique existant.

---

## Mise à jour des données

- **Manuel** : bouton *Mettre à jour* dans le panel API Conso (disponible après 24h)
- **Automatique** : option *Actualisation automatique* — déclenche une mise à jour tous les 4 jours à une heure aléatoire entre 14h et 4h du matin

---

## Tests

```bash
python -m pytest tests/ -v
```

---

## Avertissement / Disclaimer

> **Ampersage est un outil personnel d'estimation sans valeur contractuelle.**
>
> Les calculs et comparaisons affichés sont fournis à titre **indicatif uniquement**. Ils ne constituent en aucun cas un document contractuel, une offre commerciale, ni un conseil financier ou énergétique.
>
> Les montants affichés sont des **estimations** basées sur vos données de consommation et les tarifs publics disponibles. Ils peuvent différer de vos factures réelles en raison des taxes, ajustements tarifaires, arrondis ou données incomplètes.
>
> **Avant toute décision de changement d'offre ou de fournisseur**, vérifiez les conditions tarifaires directement auprès des fournisseurs concernés.
>
> L'auteur d'Ampersage décline toute responsabilité quant aux décisions prises sur la base des informations affichées par cet outil.

Ce disclaimer est affiché au lancement de l'application. L'utilisateur doit cliquer sur **Accepter** pour accéder à l'outil. Il réapparaît automatiquement après 7 jours.

---

## Roadmap

- [x] Connexion à l'API conso réelle (conso.boris.sh)
- [x] Import historique CSV Enedis
- [x] Mise à jour automatique (tous les 4 jours)
- [x] Comparaison TRV Base / HC-HP / Tempo / Sobry
- [ ] Graphiques de consommation
- [ ] Export PDF / CSV
- [ ] Module Voiture électrique (coût de recharge)
- [ ] Connexion à l'API Enedis officielle
