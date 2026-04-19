import json
import time
import urllib.request
import config

_CACHE_PATH = config.JARVIS_DIR / "btc_cache.json"
_CACHE_TTL = 15 * 60


def get_price() -> dict:
    if _CACHE_PATH.exists():
        try:
            cached = json.loads(_CACHE_PATH.read_text())
            if time.time() - cached.get("fetched_at", 0) < _CACHE_TTL:
                return cached
        except Exception:
            pass
    try:
        url = (
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin&vs_currencies=eur,usd&include_24hr_change=true"
        )
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read())["bitcoin"]
        result = {
            "eur": data["eur"],
            "usd": data["usd"],
            "change_24h": round(data.get("eur_24h_change", 0), 2),
            "fetched_at": int(time.time()),
        }
        _CACHE_PATH.write_text(json.dumps(result))
        return result
    except Exception as e:
        return {"error": str(e)}


def format_for_prompt() -> str:
    price = get_price()
    if "error" in price:
        return ""
    change = price["change_24h"]
    sign = "+" if change >= 0 else ""
    return f"## BTC\n- Kurs: {price['eur']:,.0f}€ / {price['usd']:,.0f}$ ({sign}{change:.1f}% 24h)"
