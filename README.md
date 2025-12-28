# Polymarket Frontrun Bot

Bot de trading automatisé pour Polymarket CLOB avec interface graphique moderne et optimisations maximales.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![UI](https://img.shields.io/badge/UI-CustomTkinter-green)
![WebSocket](https://img.shields.io/badge/WebSocket-Real--time-orange)
![License](https://img.shields.io/badge/License-MIT-green)

## Fonctionnalités

- **Interface graphique moderne** - CustomTkinter avec thème sombre
- **WebSocket temps réel** - Latence <50ms (vs 200ms REST)
- **Graphique P&L temps réel** - Visualisation des profits/pertes
- **Scanner parallèle** - 25 requêtes simultanées (2.5x plus rapide)
- **Persistence SQLite** - Historique des trades sauvegardé
- **Pricing dynamique** - Adapte les offsets au spread détecté
- **LRU Cache** - Mémoire stable (max 500 marchés)
- **Gestion du risque** - Circuit breaker, limites de perte journalière
- **Hotkeys** - Ctrl+S (Start/Stop), Ctrl+R (Refresh), Esc (Emergency Stop)

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
WEBSOCKET_ENABLED=true
```

### Paramètres disponibles

| Variable | Description | Défaut |
|----------|-------------|--------|
| `PRIVATE_KEY` | Clé privée Polygon (sans 0x) | - |
| `BANKROLL` | Capital total (USD) | 100 |
| `MAX_TRADE_PERCENT` | % max par trade | 1.0 |
| `MICRO_ORDER_SIZE` | Taille des ordres appâts | 3 |
| `SPREAD_THRESHOLD` | Spread minimum (USD) | 0.10 |
| `POLLING_INTERVAL` | Intervalle polling REST (sec) | 0.2 |
| `WEBSOCKET_ENABLED` | Activer WebSocket temps réel | true |

## Architecture

```
├── bot.py              # Orchestrateur principal
├── start.py            # Launcher avec gestion venv
├── trades.db           # Base SQLite (auto-créée)
├── config/
│   └── settings.py     # Configuration Pydantic
├── core/
│   ├── scanner.py      # Scanner de marchés (LRU + WebSocket)
│   ├── strategy.py     # Logique de trading (pricing dynamique)
│   ├── executor.py     # Exécution d'ordres (retry + timeout)
│   ├── risk.py         # Gestion des risques + persistence
│   ├── database.py     # SQLite persistence
│   └── websocket.py    # WebSocket manager temps réel
└── ui/
    └── app.py          # Interface CustomTkinter
```

## Optimisations Implémentées (v2.0)

| Composant | Optimisation | Impact |
|-----------|-------------|--------|
| **WebSocket** | Real-time order book | **Latence <50ms** |
| Scanner | Parallel fetch x25 | 2.5x plus rapide |
| Cache | LRU avec éviction (500 max) | Mémoire stable |
| Persistence | SQLite trades + stats | Historique persistant |
| Pricing | Dynamique (% spread) | Profits optimisés |
| Delta detection | O(n) vs O(n²) | 10x plus rapide |
| API calls | Timeout 10s + Retry x3 | Stabilité |
| Risk stats | Running counters | O(1) access |

## Flux de Détection

```
┌─────────────────────────────────────────────────────────────┐
│  WebSocket Mode (si connecté):                              │
│  Market → WS Stream → Cache → Detect (20ms) → Execute      │
│  Latence: <50ms                                             │
├─────────────────────────────────────────────────────────────┤
│  REST Fallback (si WS échoue):                              │
│  Market → REST API → Detect (200ms poll) → Execute         │
│  Latence: ~200ms                                            │
└─────────────────────────────────────────────────────────────┘
```

## Stratégie

1. **Scan** - Détection des marchés avec spread > $0.10
2. **Appât** - Placement d'un micro-ordre (pricing dynamique: 25% du spread)
3. **Surveillance** - Monitoring WebSocket temps réel (<50ms)
4. **Détection** - Identification d'un contre-ordre ≥50 parts en <1s
5. **Exécution** - Frontrun avec offset dynamique (10% du spread)

## Pricing Dynamique

| Paramètre | Calcul | Maximum |
|-----------|--------|---------|
| Bait offset | 25% du spread | $0.05 |
| Frontrun offset | 10% du spread | $0.02 |

Exemple: Spread de $0.20 → Bait à $0.05, Frontrun à $0.02

## WebSocket

Connexion automatique au démarrage:
- `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- Auto-reconnect avec backoff exponentiel
- Fallback REST si connexion impossible

## Base de Données

SQLite automatique (`trades.db`):
- Historique complet des trades
- Stats journalières agrégées
- Chargement au démarrage

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
