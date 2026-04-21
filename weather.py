import requests
import config

_BASE_URL = "https://api.openweathermap.org/data/2.5/weather"


def get_weather(city: str | None = None) -> dict:
    if not config.OPENWEATHERMAP_API_KEY:
        return {"error": "Kein OpenWeatherMap API-Key konfiguriert."}
    city = city or config.WEATHER_CITY
    try:
        resp = requests.get(_BASE_URL, params={
            "q": city,
            "appid": config.OPENWEATHERMAP_API_KEY,
            "units": "metric",
            "lang": "de",
        }, timeout=5)
        resp.raise_for_status()
        d = resp.json()
        return {
            "city": d["name"],
            "temp_c": round(d["main"]["temp"]),
            "feels_like_c": round(d["main"]["feels_like"]),
            "description": d["weather"][0]["description"],
            "humidity_pct": d["main"]["humidity"],
            "wind_kmh": round(d["wind"]["speed"] * 3.6),
        }
    except Exception as e:
        return {"error": str(e)}
