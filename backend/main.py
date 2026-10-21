import os
import ssl
import warnings

# Disable SSL verification globally — antivirus SSL inspection workaround
ssl._create_default_https_context = ssl._create_unverified_context
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

# Patch httpx so google-genai and any other httpx client skips SSL verify
import httpx as _httpx
_orig_httpx_init = _httpx.Client.__init__
def _httpx_no_ssl(self, *args, **kwargs):
    kwargs.setdefault("verify", False)
    _orig_httpx_init(self, *args, **kwargs)
_httpx.Client.__init__ = _httpx_no_ssl

# Patch amazon_paapi SDK Configuration so its PoolManager skips SSL verify
# (amazon_paapi uses urllib3 directly, bypassing requests.Session)
import urllib3 as _urllib3
_urllib3.disable_warnings()
from amazon_paapi.sdk.configuration import Configuration as _AmazonConfig
_orig_amazon_config_init = _AmazonConfig.__init__
def _amazon_config_no_ssl(self, *args, **kwargs):
    _orig_amazon_config_init(self, *args, **kwargs)
    self.verify_ssl = False
_AmazonConfig.__init__ = _amazon_config_no_ssl

import re
import json
import time
import logging
import sqlite3
import threading
from urllib.parse import quote_plus
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), "bivio.db")

SYSTEM_PROMPT = (
    "Sei un comparatore prezzi professionale. "
    "Usa web_search per trovare prezzi reali e aggiornati sui marketplace richiesti. "
    "Rispondi ESCLUSIVAMENTE con JSON valido nel formato specificato dall'utente. "
    "Niente markdown, niente backtick, niente testo aggiuntivo prima o dopo il JSON."
)

AMAZON_KEY    = os.getenv("AMAZON_ACCESS_KEY", "")
AMAZON_SECRET = os.getenv("AMAZON_SECRET_KEY", "")
AMAZON_TAG    = os.getenv("AMAZON_TAG", "")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_API_KEY_2 = os.getenv("GEMINI_API_KEY_2", "")
ALIEXPRESS_TRACKING_ID = os.getenv("ALIEXPRESS_TRACKING_ID", "")
EBAY_CAMPAIGN_ID = os.getenv("EBAY_CAMPAIGN_ID", "")
scheduler = BackgroundScheduler(daemon=True)
log = logging.getLogger("bivio")

# Cache delle ricerche: stessa query + stesse piattaforme entro il TTL non
# richiama Gemini (la quota giornaliera è il collo di bottiglia principale).
SEARCH_CACHE_TTL = int(os.getenv("SEARCH_CACHE_TTL", str(6 * 3600)))
_search_cache: dict = {}
_search_cache_lock = threading.Lock()


def _cache_get(key):
    with _search_cache_lock:
        hit = _search_cache.get(key)
        if hit and time.monotonic() - hit[0] < SEARCH_CACHE_TTL:
            return hit[1]
        _search_cache.pop(key, None)
    return None


def _cache_put(key, value):
    with _search_cache_lock:
        if len(_search_cache) > 500:
            _search_cache.clear()
        _search_cache[key] = (time.monotonic(), value)


def _parse_price(value):
    """Normalizza il prezzo restituito dal modello: 19.99, "19,99", "€ 1.299,00" → float."""
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    s = re.sub(r"[^\d.,]", "", value)
    if not s:
        return None
    if "," in s and "." in s:
        # il separatore decimale è quello più a destra
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        price = float(s)
    except ValueError:
        return None
    return price if price > 0 else None


def _clean_items(items) -> list[dict]:
    """Tiene solo prodotti con titolo e prezzo valido, ordinati per prezzo."""
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        price = _parse_price(item.get("price"))
        if price is None:
            continue
        item["price"] = price
        out.append(item)
    out.sort(key=lambda x: x["price"])
    return out


def _affiliate_url(platform: str, query: str) -> str:
    """Build a real affiliate search URL for the given platform and query."""
    q = quote_plus(query)
    if platform == "amazon":
        url = f"https://www.amazon.it/s?k={q}"
        if AMAZON_TAG:
            url += f"&tag={AMAZON_TAG}"
        return url
    if platform == "aliexpress":
        url = f"https://www.aliexpress.com/wholesale?SearchText={q}"
        if ALIEXPRESS_TRACKING_ID:
            url += f"&tblci={ALIEXPRESS_TRACKING_ID}"
        return url
    if platform == "ebay":
        url = f"https://www.ebay.it/sch/i.html?_nkw={q}"
        if EBAY_CAMPAIGN_ID:
            url += f"&mkcid=1&mkrid=724-53478-19255-0&siteid=101&campid={EBAY_CAMPAIGN_ID}&toolid=10001&mkevt=1"
        return url
    if platform == "trovaprezzi":
        return f"https://www.trovaprezzi.it/cerca.aspx?search={q}"
    return ""


def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS wishlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                price REAL,
                url TEXT,
                image TEXT,
                platform TEXT,
                shipping TEXT,
                rating TEXT,
                saved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT,
                platform TEXT,
                target_price REAL,
                last_price REAL DEFAULT 0,
                triggered INTEGER DEFAULT 0,
                created_at TEXT,
                checked_at TEXT
            );
        """)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.add_job(check_all_alerts, "interval", hours=24, id="daily_check")
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="BIVIO API", version="2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PLATFORM_NAMES = {
    "amazon":      "Amazon.it",
    "aliexpress":  "AliExpress",
    "ebay":        "eBay.it",
    "trovaprezzi": "Trovaprezzi.it",
}


class SearchRequest(BaseModel):
    query: str
    platforms: list[str] = ["amazon", "aliexpress", "ebay", "trovaprezzi"]


class WishlistItem(BaseModel):
    title: str
    price: float = 0.0
    url: str = ""
    image: str = ""
    platform: str
    shipping: str = ""
    rating: str = ""


class AlertCreate(BaseModel):
    title: str
    url: str = ""
    platform: str
    target_price: float
    last_price: float = 0.0


# ── Amazon PA-API ─────────────────────────────────────────────────────────────

def _search_amazon_paapi(query: str, count: int = 3) -> list[dict]:
    """Use Amazon PA-API v5 for accurate, real-time Amazon.it results."""
    if not all([AMAZON_KEY, AMAZON_SECRET, AMAZON_TAG]):
        return []
    try:
        from amazon_paapi import AmazonApi
        api = AmazonApi(AMAZON_KEY, AMAZON_SECRET, AMAZON_TAG, "IT")
        result = api.search_items(
            keywords=query,
            item_count=count,
        )
        if not result or not result.items:
            return []

        items = []
        for item in result.items:
            try:
                title = ""
                if item.item_info and item.item_info.title:
                    title = item.item_info.title.display_value or ""

                price_val = None
                shipping = ""
                if item.offers and item.offers.listings:
                    listing = item.offers.listings[0]
                    if listing.price:
                        price_val = float(listing.price.amount)
                    if listing.delivery_info:
                        d = listing.delivery_info
                        if getattr(d, "is_free_shipping_eligible", False):
                            shipping = "Gratuita"
                        elif getattr(d, "is_amazon_fulfilled", False):
                            shipping = "Amazon"

                url   = item.detail_page_url or ""
                image = ""
                if item.images and item.images.primary and item.images.primary.medium:
                    image = item.images.primary.medium.url or ""

                rating = ""
                if hasattr(item, "customer_reviews") and item.customer_reviews:
                    sr = getattr(item.customer_reviews, "star_rating", None)
                    if sr:
                        rating = str(sr.display_value)

                if title and price_val is not None:
                    items.append({
                        "title": title,
                        "price": price_val,
                        "url": url,
                        "image": image,
                        "shipping": shipping,
                        "rating": rating,
                    })
            except Exception:
                continue

        items.sort(key=lambda x: x["price"])
        return items[:count]
    except Exception as e:
        log.warning("Amazon PA-API failed (%s), falling back to AI search", e)
        return _search_amazon_via_ai(query, count)


def _search_amazon_via_ai(query: str, count: int = 3) -> list[dict]:
    """Fallback: search Amazon.it via Gemini/Ollama when PA-API is unavailable."""
    prompt = f"""Cerca i prezzi attuali su Amazon.it per: "{query}"

Trova fino a {count} prodotti. Per ciascuno:
- title: nome prodotto (max 100 caratteri)
- price: prezzo EUR come numero decimale (es. 199.99)
- url: link Amazon.it al prodotto
- image: URL immagine prodotto
- shipping: info spedizione (es. "Prime", "Gratuita", "")
- rating: valutazione media (es. "4.5") o stringa vuota

Rispondi SOLO con JSON valido. Formato:
{{"amazon": [{{"title":"...","price":9.99,"url":"https://www.amazon.it/...","image":"https://...","shipping":"Prime","rating":"4.5"}}]}}

Ordina per prezzo crescente. Array vuoto se nessun risultato."""

    try:
        text = _gemini_search(prompt)
    except Exception:
        text = _ollama_search(prompt)

    data = _extract_json(text)
    return data.get("amazon", [])[:count]


# ── Gemini web search ─────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    m = re.search(r"\{[\s\S]*\}", cleaned)
    if not m:
        raise ValueError("Nessun JSON valido nella risposta del modello")
    return json.loads(m.group())


_gemini_clients: dict = {}


def _gemini_search(prompt: str, model: str = "gemini-2.5-flash") -> str:
    from google import genai as _genai
    from google.genai import types as _gtypes

    def _call(api_key: str) -> str:
        # un client per chiave, riusato tra le richieste (evita di ricreare la connessione)
        gclient = _gemini_clients.get(api_key)
        if gclient is None:
            gclient = _gemini_clients[api_key] = _genai.Client(api_key=api_key)
        response = gclient.models.generate_content(
            model=model,
            contents=SYSTEM_PROMPT + "\n\n" + prompt,
            config=_gtypes.GenerateContentConfig(
                tools=[_gtypes.Tool(google_search=_gtypes.GoogleSearch())],
            ),
        )
        return response.text or ""

    primary   = GEMINI_API_KEY_2 or GEMINI_API_KEY
    secondary = GEMINI_API_KEY if GEMINI_API_KEY_2 else ""
    try:
        return _call(primary)
    except Exception:
        if secondary:
            return _call(secondary)
        raise


def _ollama_search(prompt: str, model: str = "mistral:latest") -> str:
    """Fallback local search via Ollama when Gemini quota is exhausted.
    Prices are model-generated (not real web data) — for testing only."""
    import urllib.request
    payload = json.dumps({
        "model": model,
        "prompt": "/no_think\n" + prompt,
        "stream": False,
        "options": {"temperature": 0.3},
    }).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        text = json.loads(resp.read())["response"]
    # strip any <think>...</think> blocks qwen3 might still emit
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return text


def _search_others_via_gemini(query: str, platforms: list[str]) -> dict:
    """Search AliExpress, eBay, Trovaprezzi via Gemini (fallback: Ollama)."""
    platform_list = ", ".join(PLATFORM_NAMES[p] for p in platforms)
    keys_list     = ", ".join(f'"{p}"' for p in platforms)

    prompt = f"""Cerca i prezzi reali e attuali per: "{query}"
Piattaforme: {platform_list}

Per ogni piattaforma trova fino a 3 prodotti. Per ciascuno:
- title: nome breve (max 100 caratteri)
- price: prezzo EUR come numero decimale (es. 19.99)
- url: link diretto al prodotto
- image: URL immagine prodotto
- shipping: info spedizione sintetica
- rating: valutazione media (es. "4.5") o stringa vuota

Rispondi SOLO con JSON valido. Chiavi: {keys_list}
Array vuoto [] se nessun risultato. Ordina per prezzo crescente.

Formato:
{{
  "aliexpress": [{{"title":"...","price":9.99,"url":"https://...","image":"https://...","shipping":"Gratuita","rating":"4.7"}}],
  "ebay": [...],
  "trovaprezzi": [...]
}}"""

    try:
        text = _gemini_search(prompt)
    except Exception:
        text = _ollama_search(prompt)

    return _extract_json(text)


# ── Health ────────────────────────────────────────────────────────────────────

FRONTEND = os.path.join(os.path.dirname(__file__), "..", "bivio.html")

@app.get("/")
def root():
    return FileResponse(FRONTEND, media_type="text/html")

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "version": "2.0",
        "amazon_paapi": bool(AMAZON_KEY and AMAZON_SECRET and AMAZON_TAG),
    }


# ── Search ────────────────────────────────────────────────────────────────────

@app.post("/api/search")
async def search(req: SearchRequest):
    import asyncio

    platforms = [p for p in req.platforms if p in PLATFORM_NAMES]
    if not platforms:
        raise HTTPException(status_code=400, detail="Nessuna piattaforma valida selezionata")
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query vuota")

    cache_key = (query.lower(), tuple(sorted(platforms)))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    tasks = {}

    paapi_available = bool(AMAZON_KEY and AMAZON_SECRET and AMAZON_TAG)

    if "amazon" in platforms:
        fn = _search_amazon_paapi if paapi_available else _search_amazon_via_ai
        tasks["amazon"] = loop.run_in_executor(None, fn, query)

    others = [p for p in platforms if p != "amazon"]
    if others:
        tasks["others"] = loop.run_in_executor(None, _search_others_via_gemini, query, others)

    # Ogni piattaforma richiesta parte come lista vuota: il frontend distingue
    # "nessun risultato" da "errore" leggendo il campo "errors" della risposta.
    result: dict = {p: [] for p in platforms}
    errors: dict = {}

    resolved = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for key, val in zip(tasks.keys(), resolved):
        if isinstance(val, BaseException):
            if key == "amazon":
                errors["amazon"] = str(val)
            else:
                # il task "others" copre tutte le piattaforme non-Amazon
                for p in others:
                    errors[p] = str(val)
        elif key == "amazon":
            result["amazon"] = _clean_items(val)
        else:
            # val è un dict {piattaforma: [...]}
            for p, items in val.items():
                if p in result:
                    result[p] = _clean_items(items)

    # Replace AI-generated (unreliable) URLs with real affiliate search URLs
    for platform, items in result.items():
        for item in items:
            item["url"] = _affiliate_url(platform, item.get("title") or query)

    if errors:
        result["errors"] = errors
    else:
        # in cache solo le ricerche riuscite: un errore di quota non va ripetuto per ore
        _cache_put(cache_key, result)
    return result


# ── Wishlist ──────────────────────────────────────────────────────────────────

@app.get("/api/wishlist")
def get_wishlist():
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM wishlist ORDER BY saved_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/wishlist", status_code=201)
def add_wishlist(item: WishlistItem):
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(
            "INSERT INTO wishlist (title,price,url,image,platform,shipping,rating,saved_at) VALUES (?,?,?,?,?,?,?,?)",
            (item.title, item.price, item.url, item.image, item.platform,
             item.shipping, item.rating, now),
        )
    return {"id": cur.lastrowid}


@app.delete("/api/wishlist/{item_id}")
def delete_wishlist(item_id: int):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("DELETE FROM wishlist WHERE id=?", (item_id,))
    return {"ok": True}


# ── Alerts ────────────────────────────────────────────────────────────────────

@app.get("/api/alerts")
def get_alerts():
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM alerts ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/alerts", status_code=201)
def add_alert(item: AlertCreate):
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(
            "INSERT INTO alerts (title,url,platform,target_price,last_price,triggered,created_at) VALUES (?,?,?,?,?,0,?)",
            (item.title, item.url, item.platform, item.target_price, item.last_price, now),
        )
    return {"id": cur.lastrowid}


@app.delete("/api/alerts/{alert_id}")
def delete_alert(alert_id: int):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
    return {"ok": True}


def check_all_alerts() -> dict:
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        alerts = [dict(r) for r in con.execute("SELECT * FROM alerts WHERE triggered=0").fetchall()]

    results: dict = {"checked": len(alerts), "triggered": []}

    for alert in alerts:
        try:
            # Use PA-API for Amazon alerts (free + accurate)
            if alert["platform"] == "amazon" and AMAZON_KEY:
                items = _search_amazon_paapi(alert["title"], count=1)
                current = items[0]["price"] if items else None
            else:
                prompt = f"""Qual è il prezzo attuale di questo prodotto?
Titolo: {alert['title']}
URL: {alert['url']}
Piattaforma: {alert['platform']}

Rispondi SOLO con un oggetto JSON nel formato: {{"price": 29.99}}"""
                text = _gemini_search(prompt)
                try:
                    parsed = _extract_json(text)
                    current = float(parsed["price"])
                except Exception:
                    m = re.search(r"\d+(?:[.,]\d+)?", text.strip())
                    current = float(m.group().replace(",", ".")) if m else None

            if current is None:
                continue

            triggered = current <= alert["target_price"]
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(DB_PATH) as con:
                con.execute(
                    "UPDATE alerts SET last_price=?, triggered=?, checked_at=? WHERE id=?",
                    (current, 1 if triggered else 0, now, alert["id"]),
                )
            if triggered:
                results["triggered"].append({
                    "id": alert["id"],
                    "title": alert["title"],
                    "price": current,
                })
        except Exception as e:
            print(f"[alert {alert['id']}] errore: {e}")

    return results


@app.post("/api/alerts/check")
def trigger_check():
    return check_all_alerts()

@app.post("/api/shutdown")
async def shutdown(request: Request, background_tasks: BackgroundTasks):
    import subprocess, signal
    # Solo dal PC di casa: via Cloudflare Tunnel la richiesta arriva da 127.0.0.1
    # ma porta l'header cf-connecting-ip, quindi va rifiutata anche in quel caso.
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1", "localhost") or "cf-connecting-ip" in request.headers:
        raise HTTPException(status_code=403, detail="Spegnimento consentito solo in locale")
    def _kill():
        try:
            subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], capture_output=True)
        except Exception:
            pass
        os.kill(os.getpid(), signal.SIGTERM)
    background_tasks.add_task(_kill)
    return {"status": "shutting_down"}
