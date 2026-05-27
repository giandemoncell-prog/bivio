# BIVIO — Comparatore Prezzi Multi-Piattaforma

![Python](https://img.shields.io/badge/Python-3.14-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

Confronta prezzi in tempo reale su **Amazon**, **AliExpress**, **eBay** e **Trovaprezzi** con una sola ricerca. Progetto familiare open source, fatto con Python + Gemini AI.

<!-- screenshot -->

## Funzionalità

- Ricerca parallela su 4 marketplace in pochi secondi
- Link affiliati automatici (Amazon Associates, AliExpress Portals, eBay Partner Network)
- Suggerimenti di ricerca mentre si digita
- Wishlist per salvare i prodotti preferiti
- Alert prezzi — notifica quando un prodotto scende sotto la soglia
- Accessibile da casa (localhost) e da remoto via Cloudflare Tunnel
- Avvio con doppio clic, spegnimento con pulsante ⏻
- Nessun account richiesto, tutto gira in locale

## Requisiti

- Python 3.14+
- [Ollama](https://ollama.ai) (opzionale — fallback locale se Gemini quota esaurita)
- Account Cloudflare gratuito (per accesso remoto)

## Installazione

```bash
git clone https://github.com/giandemoncell-prog/bivio.git
cd bivio/backend
pip install -r requirements.txt
```

Copia il file delle credenziali:

```bash
cp .env.example .env
```

Modifica `.env` con le tue chiavi API, poi avvia:

```bash
# Windows — doppio clic su avvia_bivio.bat
# oppure da terminale:
python -m uvicorn main:app --port 8000
```

Apri `bivio.html` nel browser.

## Accesso remoto

Per usare BIVIO da qualsiasi dispositivo:

1. Installa [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
2. `cloudflared tunnel login`
3. `cloudflared tunnel create bivio`
4. `cloudflared tunnel route dns bivio tuodominio.com`
5. Avvia il tunnel: `cloudflared tunnel run bivio`

## Credenziali affiliate

| Programma | Variabile .env | Note |
|---|---|---|
| Amazon Associates | `AMAZON_TAG` | Solo tag — PA-API richiede 3 vendite |
| AliExpress Portals | `ALIEXPRESS_TRACKING_ID` | Registrazione gratuita |
| eBay Partner Network | `EBAY_CAMPAIGN_ID` | Registrazione gratuita |
| Gemini AI | `GEMINI_API_KEY` | Google AI Studio — free tier |

## Struttura

```
bivio/
├── bivio.html          # Frontend single-page
├── avvia_bivio.bat     # Launcher Windows
├── avvia_servizi.ps1   # Avvio silenzioso (Task Scheduler)
└── backend/
    ├── main.py         # FastAPI server
    ├── requirements.txt
    └── .env.example
```

## Licenza

MIT — libero di usare, modificare e distribuire.

---

Fatto da [Gianluca Demontis](https://gianlucademontis.xyz) — Tech Coach, maker, insegnante.
