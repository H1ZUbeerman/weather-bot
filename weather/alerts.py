from datetime import datetime

import requests

from storage.json_storage import load_json_file, save_json_file
from core.config import DANGER_SUBSCRIBERS_FILE


def weather_code_description(code):
    descriptions = {
        0: "ясно",
        1: "преимущественно ясно",
        2: "переменная облачность",
        3: "пасмурно",
        45: "туман",
        48: "изморозь / туман",
        51: "слабая морось",
        53: "морось",
        55: "сильная морось",
        61: "слабый дождь",
        63: "дождь",
        65: "сильный дождь",
        71: "слабый снег",
        73: "снег",
        75: "сильный снег",
        80: "слабый ливень",
        81: "ливень",
        82: "сильный ливень",
        95: "гроза",
        96: "гроза с градом",
        99: "сильная гроза с градом",
    }

    return descriptions.get(code, "неизвестное явление")


def detect_danger_events(location, hours_limit=48):
    lat = location["latitude"]
    lon = location["longitude"]

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}"
        f"&longitude={lon}"
        "&hourly=temperature_2m,precipitation_probability,precipitation,rain,showers,snowfall,weather_code,wind_speed_10m,wind_gusts_10m,visibility"
        "&forecast_days=3"
        "&timezone=auto"
    )

    data = requests.get(url, timeout=20).json()
    hourly = data.get("hourly", {})

    times = hourly.get("time", [])
    precip_probs = hourly.get("precipitation_probability", [])
    precipitations = hourly.get("precipitation", [])
    weather_codes = hourly.get("weather_code", [])
    winds = hourly.get("wind_speed_10m", [])
    gusts = hourly.get("wind_gusts_10m", [])

    events = []

    for index, time_str in enumerate(times[:hours_limit]):
        precip_prob = precip_probs[index]
        precipitation = precipitations[index]
        code = weather_codes[index]
        wind = winds[index]
        gust = gusts[index]

        local_time = datetime.fromisoformat(time_str)
        formatted_time = local_time.strftime("%d.%m %H:%M")

        description = weather_code_description(code)

        if precip_prob >= 70 or precipitation >= 3:
            severity = "🔴" if precip_prob >= 85 else "🟠"

            events.append({
                "time": formatted_time,
                "type": "rain",
                "severity": severity,
                "text": (
                    f"{severity} 🌧 Дождь: "
                    f"{formatted_time}, "
                    f"вероятность ~{precip_prob}%, "
                    f"{description}"
                ),
            })

        if code in [95, 96, 99]:
            severity = "🔴" if code in [96, 99] else "🟠"

            events.append({
                "time": formatted_time,
                "type": "storm",
                "severity": severity,
                "text": (
                    f"{severity} ⛈ Гроза: "
                    f"{formatted_time}, "
                    f"{description}"
                ),
            })

        if wind >= 30 or gust >= 45:
            severity = "🔴" if wind >= 40 or gust >= 60 else "🟠"

            events.append({
                "time": formatted_time,
                "type": "wind",
                "severity": severity,
                "text": (
                    f"{severity} 💨 Ветер: "
                    f"{formatted_time}, "
                    f"ветер ~{wind} км/ч"
                ),
            })

    return events


def load_danger_subscribers():
    return load_json_file(DANGER_SUBSCRIBERS_FILE, [])


def save_danger_subscribers(subscribers):
    save_json_file(DANGER_SUBSCRIBERS_FILE, subscribers)


def build_danger_signature(events):
    if not events:
        return ""

    important_events = []

    for event in events[:8]:
        important_events.append(
            f"{event.get('time')}:{event.get('type')}:{event.get('severity')}"
        )

    return "|".join(important_events)