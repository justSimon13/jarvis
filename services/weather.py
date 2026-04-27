import json
import urllib.request
import urllib.parse
import config

_GEO_URL = "https://nominatim.openstreetmap.org/search"
_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

_WMO_CODES = {
    0: "klar", 1: "überwiegend klar", 2: "teilweise bewölkt", 3: "bedeckt",
    45: "neblig", 48: "gefrierender Nebel",
    51: "leichter Nieselregen", 53: "Nieselregen", 55: "starker Nieselregen",
    61: "leichter Regen", 63: "Regen", 65: "starker Regen",
    71: "leichter Schneefall", 73: "Schneefall", 75: "starker Schneefall",
    80: "leichte Regenschauer", 81: "Regenschauer", 82: "starke Regenschauer",
    95: "Gewitter", 96: "Gewitter mit Hagel", 99: "starkes Gewitter mit Hagel",
}


def _geocode(city: str) -> tuple[float, float]:
    url = f"{_GEO_URL}?q={urllib.parse.quote(city)}&format=json&limit=1"
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.0"})
    with urllib.request.urlopen(req, timeout=5) as r:
        results = json.loads(r.read())
    if not results:
        raise ValueError(f"Stadt nicht gefunden: {city}")
    return float(results[0]["lat"]), float(results[0]["lon"])


def get_weather(city: str | None = None) -> dict:
    city = city or config.WEATHER_CITY
    try:
        lat, lon = _geocode(city)
        url = (
            f"{_WEATHER_URL}?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,apparent_temperature,weather_code,"
            "relative_humidity_2m,wind_speed_10m"
            "&wind_speed_unit=kmh&timezone=auto"
        )
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read())
        c = data["current"]
        code = c["weather_code"]
        return {
            "city": city,
            "temp_c": round(c["temperature_2m"]),
            "feels_like_c": round(c["apparent_temperature"]),
            "description": _WMO_CODES.get(code, f"Code {code}"),
            "humidity_pct": c["relative_humidity_2m"],
            "wind_kmh": round(c["wind_speed_10m"]),
        }
    except Exception as e:
        return {"error": str(e)}
