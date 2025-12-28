# Polymarket Frontrun Bot

Bot de trading automatisé pour Polymarket CLOB avec interface graphique moderne.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![UI](https://img.shields.io/badge/UI-CustomTkinter-green)
![License](https://img.shields.io/badge/License-MIT-green)

## Fonctionnalités

- **Interface graphique moderne** - CustomTkinter avec thème sombre
- **Graphique P&L temps réel** - Visualisation des profits/pertes
- **Scanner parallèle** - 25 requêtes simultanées (2.5x plus rapide)
- **Gestion du risque** - Circuit breaker, limites de perte journalière
- **Hotkeys** - Ctrl+S (Start/Stop), Ctrl+R (Refresh), Esc (Emergency Stop)
- **Filtres marchés** - Recherche et filtrage par opportunités

## Démarrage Rapide

```bash
# Cloner le repo
git clone https://github.com/minculusofia-wq/frontrun-polymarket.git
cd frontrun-polymarket

# Lancer (crée venv automatiquement)
python start.py
```

## Configuration

Créer un fichier `.env` à la racine:

```env
PRIVATE_KEY=votre_clé_privée_polygon_sans_0x
BANKROLL=100
POLLING_INTERVAL=0.2
```

### Paramètres disponibles

| Variable | Description | Défaut |
|----------|-------------|--------|
| `PRIVATE_KEY` | Clé privée Polygon (sans 0x) | - |
| `BANKROLL` | Capital total (USD) | 100 |
| `MAX_TRADE_PERCENT` | % max par trade | 1.0 |
| `MICRO_ORDER_SIZE` | Taille des ordres appâts | 3 |
| `SPREAD_THRESHOLD` | Spread minimum (USD) | 0.10 |
| `POLLING_INTERVAL` | Intervalle polling (sec) | 0.2 |

## Architecture

```
├── bot.py              # Orchestrateur principal
├── start.py            # Launcher avec gestion venv
├── config/
│   └── settings.py     # Configuration Pydantic
├── core/
│   ├── scanner.py      # Scanner de marchés (parallèle x25)
│   ├── strategy.py     # Logique de trading
│   ├── executor.py     # Exécution d'ordres (retry + timeout)
│   └── risk.py         # Gestion des risques
└── ui/
    └── app.py          # Interface CustomTkinter
```

## Optimisations Implémentées

| Composant | Optimisation | Impact |
|-----------|-------------|--------|
| Scanner | Parallel fetch x25 | 2.5x plus rapide |
| Delta detection | O(n) vs O(n²) | 10x plus rapide |
| API calls | Timeout 10s + Retry x3 | Stabilité |
| Risk stats | Running counters | O(1) access |
| P&L Chart | deque(maxlen=50) | O(1) operations |
| Market sort | Cache 5s TTL | Évite sort répété |
| Polling | 0.2s (vs 0.5s) | 2.5x plus réactif |

## Stratégie

1. **Scan** - Détection des marchés avec spread > $0.10
2. **Appât** - Placement d'un micro-ordre (1-5 parts)
3. **Surveillance** - Monitoring de l'order book (~0.2s)
4. **Détection** - Identification d'un contre-ordre ≥50 parts en <1s
5. **Exécution** - Ordre immédiat pour capturer le spread

## WebSocket (Préparé)

Support WebSocket disponible via:
- `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- `wss://ws-subscriptions-clob.polymarket.com/ws/user`

## Dépendances

- Python 3.11+
- py-clob-client >= 0.5.0
- customtkinter >= 5.2.0
- pydantic >= 2.0.0
- websockets >= 12.0

## Avertissements

- **Mode LIVE** - Ce bot trade avec de vrais fonds
- **Risque financier** - Les pertes sont possibles
- **Aucune garantie** - Performance passée ≠ résultats futurs

## License

MIT License - Utilisez à vos propres risques.
