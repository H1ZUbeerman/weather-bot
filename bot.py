import os
import json
import requests
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from telegram.ext import CommandHandler

from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()


def get_env_value(name):
    value = os.getenv(name)
    return value.strip() if value else None


TOKEN = get_env_value("TELEGRAM_BOT_TOKEN")
WEATHERAPI_KEY = get_env_value("WEATHERAPI_KEY")
VISUALCROSSING_API_KEY = get_env_value("VISUALCROSSING_API_KEY")
METEOSOURCE_API_KEY = get_env_value("METEOSOURCE_API_KEY")
OPENAI_API_KEY = get_env_value("OPENAI_API_KEY")

HISTORY_FILE = "weather_history.json"
SCORES_FILE = "model_scores.json"
RAIN_SCORES_FILE = "rain_scores.json"


USER_SETTINGS_FILE = "settings.json"

def load_user_settings():
    if not os.path.exists(USER_SETTINGS_FILE):
        return {}
    try:
        with open(USER_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_user_settings(data):
    with open(USER_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user_settings(chat_id):
    data = load_user_settings()
    return data.get(str(chat_id), {})

def update_user_setting(chat_id, key, value):
    data = load_user_settings()
    chat_key = str(chat_id)

    if chat_key not in data:
        data[chat_key] = {}

    data[chat_key][key] = value
    save_user_settings(data)


def get_home_location_for_chat(chat_id):
    user_settings = get_user_settings(chat_id)
    home_key = user_settings.get("home_location_key", "home")
    return get_location_by_key(home_key)


def get_location_by_key(location_key):
    return FAVORITE_LOCATIONS.get(location_key, FAVORITE_LOCATIONS["home"])


FAVORITE_LOCATIONS = {
    "home": {"latitude": 55.904068, "longitude": 37.640018, "name": "Дом", "country": "Россия", "region_type": "moscow"},
    "moscow": {"latitude": 55.7558, "longitude": 37.6173, "name": "Москва", "country": "Россия", "region_type": "moscow"},
    "sergiev": {"latitude": 56.3063, "longitude": 38.1506, "name": "Сергиев Посад", "country": "Россия", "region_type": "mixed"},
    "kalyazin": {"latitude": 57.2404, "longitude": 37.8563, "name": "Калязин", "country": "Россия", "region_type": "lake"},
    "khvoynaya": {"latitude": 58.9000, "longitude": 34.5333, "name": "Хвойная", "country": "Россия", "region_type": "north"},
    "lyubytino": {"latitude": 58.8119, "longitude": 33.3922, "name": "Любытино", "country": "Россия", "region_type": "north"},
    "moscow_ilya": {"latitude": 55.873819, "longitude": 37.610251, "name": "Москва Илья", "country": "Россия", "region_type": "urban"},
    "moscow_center": {"latitude": 55.7558, "longitude": 37.6176, "name": "Москва Центр", "country": "Россия", "region_type": "urban"},
    "moscow_north": {"latitude": 55.8800, "longitude": 37.5500, "name": "Москва Север", "country": "Россия", "region_type": "urban"},
    "moscow_south": {"latitude": 55.6200, "longitude": 37.6500, "name": "Москва Юг", "country": "Россия", "region_type": "urban"},
    "moscow_west": {"latitude": 55.7400, "longitude": 37.4200, "name": "Москва Запад", "country": "Россия", "region_type": "urban"},
    "moscow_east": {"latitude": 55.7800, "longitude": 37.8200, "name": "Москва Восток", "country": "Россия", "region_type": "urban"},
}

REGION_WEIGHTS = {
    "moscow": {"openmeteo": 0.30, "weatherapi": 0.30, "visualcrossing": 0.20, "yr": 0.10, "meteosource": 0.10},
    "urban": {"openmeteo": 0.30, "weatherapi": 0.30, "visualcrossing": 0.20, "yr": 0.10, "meteosource": 0.10},
    "north": {"openmeteo": 0.30, "weatherapi": 0.10, "visualcrossing": 0.20, "yr": 0.35, "meteosource": 0.05},
    "forest": {"openmeteo": 0.30, "weatherapi": 0.10, "visualcrossing": 0.20, "yr": 0.35, "meteosource": 0.05},
    "lake": {"openmeteo": 0.30, "weatherapi": 0.15, "visualcrossing": 0.20, "yr": 0.30, "meteosource": 0.05},
    "mixed": {"openmeteo": 0.25, "weatherapi": 0.25, "visualcrossing": 0.20, "yr": 0.20, "meteosource": 0.10},
}

DAY_PARTS = {
    "morning": {
        "title": "🌅 Утро",
        "start_hour": 6,
        "end_hour": 12,
    },
    "day": {
        "title": "☀️ День",
        "start_hour": 12,
        "end_hour": 18,
    },
    "evening": {
        "title": "🌆 Вечер",
        "start_hour": 18,
        "end_hour": 23,
    },
    "night": {
        "title": "🌙 Ночь",
        "start_hour": 23,
        "end_hour": 30,
    },
}


def load_json_file(filename, default):
    path = Path(filename)
    if not path.exists():
        return default
    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json_file(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def weighted_average(values, weights):
    total = 0
    total_weight = 0

    for key, value in values.items():
        if value is not None:
            weight = weights.get(key, 0)
            total += value * weight
            total_weight += weight

    if total_weight == 0:
        return 0

    return round(total / total_weight, 1)


def simple_average(values):
    values = [v for v in values if v is not None]
    if not values:
        return 0
    return round(sum(values) / len(values), 1)


def calculate_confidence(spread, good_limit, medium_limit):
    if spread <= good_limit:
        return "высокая"
    if spread <= medium_limit:
        return "средняя"
    return "низкая"


def rain_score_from_mm(mm):
    if mm is None:
        return 0
    if mm >= 5:
        return 90
    if mm >= 2:
        return 70
    if mm >= 0.5:
        return 45
    if mm > 0:
        return 25
    return 0


def save_forecast_history(location_name, forecast_data):
    history = load_json_file(HISTORY_FILE, [])
    history.append({
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "location": location_name,
        "forecast": forecast_data
    })
    save_json_file(HISTORY_FILE, history)


def load_history():
    return load_json_file(HISTORY_FILE, [])


def load_scores():
    default_scores = {
        "openmeteo": {"checks": 0, "total_error": 0, "wins": 0},
        "weatherapi": {"checks": 0, "total_error": 0, "wins": 0},
        "visualcrossing": {"checks": 0, "total_error": 0, "wins": 0},
        "yr": {"checks": 0, "total_error": 0, "wins": 0},
        "meteosource": {"checks": 0, "total_error": 0, "wins": 0},
        "consensus": {"checks": 0, "total_error": 0, "wins": 0},
    }

    scores = load_json_file(SCORES_FILE, default_scores)

    for key in default_scores:
        if key not in scores:
            scores[key] = default_scores[key]

    return scores


def save_scores(scores):
    save_json_file(SCORES_FILE, scores)


def update_model_scores(errors, consensus_error):
    scores = load_scores()
    all_errors = dict(errors)
    all_errors["consensus"] = consensus_error
    best_model = min(all_errors, key=all_errors.get)

    for model, error in all_errors.items():
        scores[model]["checks"] += 1
        scores[model]["total_error"] += error
        if model == best_model:
            scores[model]["wins"] += 1

    save_scores(scores)
    return best_model


def load_rain_scores():
    default_scores = {
        "openmeteo": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
        "weatherapi": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
        "visualcrossing": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
        "yr": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
        "meteosource": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
        "consensus": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
    }

    scores = load_json_file(RAIN_SCORES_FILE, default_scores)

    for key in default_scores:
        if key not in scores:
            scores[key] = default_scores[key]

    return scores


def save_rain_scores(scores):
    save_json_file(RAIN_SCORES_FILE, scores)


def update_rain_scores(predictions, factual_rain_score):
    scores = load_rain_scores()
    fact_is_rain = factual_rain_score >= 25

    for model, predicted_score in predictions.items():
        predicted_is_rain = predicted_score >= 30
        scores[model]["checks"] += 1
        scores[model]["total_error"] += abs(predicted_score - factual_rain_score)

        if predicted_is_rain == fact_is_rain:
            scores[model]["correct"] += 1
        elif predicted_is_rain and not fact_is_rain:
            scores[model]["false_positive"] += 1
        elif not predicted_is_rain and fact_is_rain:
            scores[model]["missed"] += 1

    save_rain_scores(scores)


def get_adaptive_weights_from_scores():
    scores_data = load_scores()
    source_scores = {k: v for k, v in scores_data.items() if k != "consensus"}

    if all(v["checks"] == 0 for v in source_scores.values()):
        return None

    quality = {}

    for model, data in source_scores.items():
        checks = data["checks"]
        total_error = data["total_error"]
        wins = data["wins"]

        if checks == 0:
            quality[model] = 0.01
            continue

        avg_error = total_error / checks
        win_rate = wins / checks
        quality[model] = (1 / (avg_error + 0.1)) + (win_rate * 0.3)

    total_quality = sum(quality.values())

    adaptive_weights = {
        model: round(score / total_quality, 2)
        for model, score in quality.items()
    }

    correction = round(1 - sum(adaptive_weights.values()), 2)

    if correction != 0:
        best_model = max(adaptive_weights, key=adaptive_weights.get)
        adaptive_weights[best_model] = round(adaptive_weights[best_model] + correction, 2)

    return adaptive_weights


def get_weights_for_location(location):
    adaptive_weights = get_adaptive_weights_from_scores()

    if adaptive_weights:
        return adaptive_weights, "adaptive"

    region_type = location["region_type"]
    return REGION_WEIGHTS[region_type], f"regional:{region_type}"


def get_ai_summary(prompt):
    try:
        if not OPENAI_API_KEY:
            return "AI summary недоступен: отсутствует OPENAI_API_KEY"

        client = OpenAI(api_key=OPENAI_API_KEY)

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты AI weather assistant. "
                        "Пиши кратко и полезно. "
                        "Давай рекомендации человеку: одежда, зонт, ветер, надежность прогноза."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"AI недоступен: {e}"


def get_city_coordinates(city):
    url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={city}&count=1&language=ru&format=json"
    )

    data = requests.get(url).json()

    if "results" not in data:
        return None

    location = data["results"][0]

    return {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "name": location["name"],
        "country": location.get("country", ""),
        "region_type": "mixed",
    }


def get_location(context, default_key="home", chat_id=None):
    if not context.args:
        if default_key == "home" and chat_id is not None:
            return get_home_location_for_chat(chat_id)

        return FAVORITE_LOCATIONS[default_key]

    key = context.args[0].lower()

    if key in FAVORITE_LOCATIONS:
        return FAVORITE_LOCATIONS[key]

    return get_city_coordinates(" ".join(context.args))


def get_location_by_name(location_name):
    for loc in FAVORITE_LOCATIONS.values():
        if loc["name"] == location_name:
            return loc
    return None


def get_yr_data(lat, lon):
    url = (
        "https://api.met.no/weatherapi/locationforecast/2.0/compact"
        f"?lat={lat}&lon={lon}"
    )

    headers = {"User-Agent": "WeatherAnalystBot/1.0"}
    response = requests.get(url, headers=headers, timeout=20)

    if response.status_code != 200:
        return None

    return response.json()


def get_meteosource_data(lat, lon, sections="current"):
    url = (
        "https://www.meteosource.com/api/v1/free/point"
        f"?lat={lat}"
        f"&lon={lon}"
        f"&sections={sections}"
        "&timezone=auto"
        "&language=en"
        "&units=metric"
        f"&key={METEOSOURCE_API_KEY}"
    )

    response = requests.get(url, timeout=20)

    if response.status_code != 200:
        return None

    return response.json()


def get_current_sources(location):
    lat = location["latitude"]
    lon = location["longitude"]

    om_data = requests.get(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}"
        f"&longitude={lon}"
        "&current=temperature_2m,wind_speed_10m,precipitation"
    ).json()

    om_current = om_data["current"]
    om_temp = om_current["temperature_2m"]
    om_wind = om_current["wind_speed_10m"]
    om_rain = rain_score_from_mm(om_current.get("precipitation", 0))

    wa_data = requests.get(
        "https://api.weatherapi.com/v1/current.json"
        f"?key={WEATHERAPI_KEY}"
        f"&q={lat},{lon}&aqi=no"
    ).json()

    wa_current = wa_data["current"]
    wa_temp = wa_current["temp_c"]
    wa_wind = wa_current["wind_kph"]
    wa_rain = rain_score_from_mm(wa_current.get("precip_mm", 0))

    vc_data = requests.get(
        "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/"
        f"{lat},{lon}"
        f"?unitGroup=metric"
        f"&key={VISUALCROSSING_API_KEY}"
        "&include=current"
    ).json()

    vc_current = vc_data["currentConditions"]
    vc_temp = vc_current["temp"]
    vc_wind = vc_current["windspeed"]

    vc_rain = vc_current.get("precipprob")
    if vc_rain is None:
        vc_rain = rain_score_from_mm(vc_current.get("precip", 0))

    yr_data = get_yr_data(lat, lon)

    if not yr_data:
        raise ValueError("yr.no ошибка")

    yr_item = yr_data["properties"]["timeseries"][0]
    yr_now = yr_item["data"]["instant"]["details"]

    yr_temp = yr_now["air_temperature"]
    yr_wind = round(yr_now["wind_speed"] * 3.6, 1)

    yr_next_1h = yr_item["data"].get("next_1_hours", {})
    yr_precip_mm = yr_next_1h.get("details", {}).get("precipitation_amount", 0)
    yr_rain = rain_score_from_mm(yr_precip_mm)

    ms_data = get_meteosource_data(lat, lon, "current")

    if not ms_data:
        raise ValueError("Meteosource ошибка")

    ms_current = ms_data.get("current", {})
    ms_temp = ms_current.get("temperature")

    wind_data = ms_current.get("wind", {})

    if isinstance(wind_data, dict):
        ms_wind = wind_data.get("speed")
    else:
        ms_wind = ms_current.get("wind_speed")

    ms_precipitation = ms_current.get("precipitation", 0)

    if isinstance(ms_precipitation, dict):
        ms_rain = rain_score_from_mm(ms_precipitation.get("total", 0))
    else:
        ms_rain = rain_score_from_mm(ms_precipitation)

    return {
        "temperatures": {
            "openmeteo": om_temp,
            "weatherapi": wa_temp,
            "visualcrossing": vc_temp,
            "yr": yr_temp,
            "meteosource": ms_temp,
        },
        "winds": {
            "openmeteo": om_wind,
            "weatherapi": wa_wind,
            "visualcrossing": vc_wind,
            "yr": yr_wind,
            "meteosource": ms_wind,
        },
        "rain": {
            "openmeteo": om_rain,
            "weatherapi": wa_rain,
            "visualcrossing": vc_rain,
            "yr": yr_rain,
            "meteosource": ms_rain,
        }
    }


def get_hourly_parts_sources(location, target_date=None):
    lat = location["latitude"]
    lon = location["longitude"]

    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    om_data = requests.get(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}"
        f"&longitude={lon}"
        "&hourly=temperature_2m,precipitation_probability,wind_speed_10m"
        "&timezone=auto"
    ).json()

    hourly = om_data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    rain_probs = hourly.get("precipitation_probability", [])
    winds = hourly.get("wind_speed_10m", [])

    part_values = {}

    for part_key, part in DAY_PARTS.items():
        part_temps = []
        part_rains = []
        part_winds = []

        start_hour = part["start_hour"]
        end_hour = part["end_hour"]

        for idx, time_str in enumerate(times):
            dt = datetime.fromisoformat(time_str)
            date_str = dt.strftime("%Y-%m-%d")
            hour = dt.hour

            adjusted_hour = hour
            adjusted_date = date_str

            if part_key == "night" and hour < 6:
                adjusted_hour = hour + 24

            if adjusted_date != target_date:
                continue

            if start_hour <= adjusted_hour < end_hour:
                if idx < len(temps):
                    part_temps.append(temps[idx])
                if idx < len(rain_probs):
                    part_rains.append(rain_probs[idx])
                if idx < len(winds):
                    part_winds.append(winds[idx])

        part_values[part_key] = {
            "title": part["title"],
            "temp": simple_average(part_temps),
            "rain": round(max(part_rains), 1) if part_rains else 0,
            "wind": round(max(part_winds), 1) if part_winds else 0,
            "hours_count": len(part_temps),
        }

    return part_values


def get_daily_sources(location, date_obj):
    lat = location["latitude"]
    lon = location["longitude"]
    target_date = date_obj.strftime("%Y-%m-%d")
    today = datetime.now().date()
    days_ahead = (date_obj.date() - today).days + 1

    if days_ahead < 1:
        days_ahead = 1

    om_data = requests.get(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}"
        f"&longitude={lon}"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max"
        "&timezone=auto"
    ).json()

    om_dates = om_data["daily"]["time"]
    om_index = om_dates.index(target_date)

    om_temp = round(
        (om_data["daily"]["temperature_2m_max"][om_index] + om_data["daily"]["temperature_2m_min"][om_index]) / 2,
        1
    )
    om_rain = om_data["daily"]["precipitation_probability_max"][om_index]
    om_wind = om_data["daily"]["wind_speed_10m_max"][om_index]

    wa_data = requests.get(
        "https://api.weatherapi.com/v1/forecast.json"
        f"?key={WEATHERAPI_KEY}"
        f"&q={lat},{lon}"
        f"&days={max(days_ahead, 2)}"
        "&aqi=no"
        "&alerts=no"
    ).json()

    wa_forecast_days = wa_data["forecast"]["forecastday"]
    wa_day = next(day for day in wa_forecast_days if day["date"] == target_date)["day"]

    wa_temp = wa_day["avgtemp_c"]
    wa_rain = float(wa_day["daily_chance_of_rain"])
    wa_wind = wa_day["maxwind_kph"]

    vc_data = requests.get(
        "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/"
        f"{lat},{lon}/{target_date}"
        f"?unitGroup=metric"
        f"&key={VISUALCROSSING_API_KEY}"
        "&include=days"
    ).json()

    vc_day = vc_data["days"][0]
    vc_temp = vc_day["temp"]
    vc_rain = vc_day.get("precipprob", 0)
    vc_wind = vc_day["windspeed"]

    yr_data = get_yr_data(lat, lon)

    if not yr_data:
        raise ValueError("yr.no ошибка")

    yr_temps = []
    yr_winds = []
    yr_rains = []

    for item in yr_data["properties"]["timeseries"]:
        item_time = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))

        if item_time.strftime("%Y-%m-%d") == target_date:
            details = item["data"]["instant"]["details"]

            if details.get("air_temperature") is not None:
                yr_temps.append(details.get("air_temperature"))

            if details.get("wind_speed") is not None:
                yr_winds.append(details.get("wind_speed") * 3.6)

            next_1h = item["data"].get("next_1_hours", {})
            precip_mm = next_1h.get("details", {}).get("precipitation_amount", 0)
            yr_rains.append(rain_score_from_mm(precip_mm))

    if not yr_temps:
        yr_temp = 0
        yr_wind = 0
        yr_rain = 0
    else:
        yr_temp = round(sum(yr_temps) / len(yr_temps), 1)
        yr_wind = round(max(yr_winds), 1) if yr_winds else 0
        yr_rain = round(max(yr_rains), 1) if yr_rains else 0

    ms_data = get_meteosource_data(lat, lon, "daily")

    if not ms_data:
        raise ValueError("Meteosource ошибка")

    ms_days = ms_data.get("daily", {}).get("data", [])
    ms_day = None

    for day in ms_days:
        if day.get("day") == target_date:
            ms_day = day
            break

    if not ms_day:
        ms_temp = 0
        ms_wind = 0
        ms_rain = 0
    else:
        all_day = ms_day.get("all_day", {})
        ms_temp = all_day.get("temperature")
        wind_data = all_day.get("wind", {})
        ms_wind = wind_data.get("speed") if isinstance(wind_data, dict) else 0
        precip_data = all_day.get("precipitation", {})
        ms_rain = precip_data.get("probability", 0) if isinstance(precip_data, dict) else 0

    return {
        "temperatures": {
            "openmeteo": om_temp,
            "weatherapi": wa_temp,
            "visualcrossing": vc_temp,
            "yr": yr_temp,
            "meteosource": ms_temp,
        },
        "winds": {
            "openmeteo": om_wind,
            "weatherapi": wa_wind,
            "visualcrossing": vc_wind,
            "yr": yr_wind,
            "meteosource": ms_wind,
        },
        "rain": {
            "openmeteo": om_rain,
            "weatherapi": wa_rain,
            "visualcrossing": vc_rain,
            "yr": yr_rain,
            "meteosource": ms_rain,
        }
    }


def build_consensus(location, sources):
    temp_values = sources["temperatures"]
    wind_values = sources["winds"]
    rain_values = sources["rain"]

    weights, weights_mode = get_weights_for_location(location)

    avg_temp = weighted_average(temp_values, weights)
    avg_wind = weighted_average(wind_values, weights)
    avg_rain = weighted_average(rain_values, weights)

    temp_spread = round(max(temp_values.values()) - min(temp_values.values()), 1)
    rain_spread = round(max(rain_values.values()) - min(rain_values.values()), 1)

    temp_confidence = calculate_confidence(temp_spread, 1.5, 3.5)
    rain_confidence = calculate_confidence(rain_spread, 20, 50)

    return {
        "temp_values": temp_values,
        "wind_values": wind_values,
        "rain_values": rain_values,
        "avg_temp": avg_temp,
        "avg_wind": avg_wind,
        "avg_rain": avg_rain,
        "temp_spread": temp_spread,
        "rain_spread": rain_spread,
        "temp_confidence": temp_confidence,
        "rain_confidence": rain_confidence,
        "weights": weights,
        "weights_mode": weights_mode,
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌦 AI Weather Assistant\n\n"
        "Команды:\n"
        "/weather\n"
        "/today_parts\n"
        "/tomorrow_parts\n"
        "/tomorrow\n"
        "/weekend\n"
        "/history\n"
        "/analyze\n"
        "/verify\n"
        "/verify_rain\n"
        "/scores\n"
        "/rain_scores\n"
        "/adaptive\n"
        "/status\n\n"
        "Локации:\n"
        "/home\n"
        "/moscow\n"
        "/moscow_ilya\n"
        "/sergiev\n"
        "/kalyazin\n"
        "/khvoynaya\n"
        "/lyubytino"
    )


async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context, chat_id=update.effective_chat.id)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    try:
        current = get_current_sources(location)
        c = build_consensus(location, current)
    except Exception as e:
        await update.message.reply_text(f"Ошибка получения погоды: {e}")
        return

    ai_prompt = f"""
Локация:
{location['name']}

Температуры:
{c['temp_values']}

Ветер:
{c['wind_values']}

Осадки / rain score:
{c['rain_values']}

Consensus:
Температура: {c['avg_temp']}
Ветер: {c['avg_wind']}
Осадки: {c['avg_rain']}

Уверенность по температуре:
{c['temp_confidence']}

Уверенность по осадкам:
{c['rain_confidence']}

Сделай краткий полезный вывод. Обязательно скажи, брать ли зонт.
"""

    ai_summary = get_ai_summary(ai_prompt)

    forecast_history = {
        "region_type": location["region_type"],
        "temperatures": {**c["temp_values"], "consensus": c["avg_temp"]},
        "winds": {**c["wind_values"], "consensus": c["avg_wind"]},
        "rain": {**c["rain_values"], "consensus": c["avg_rain"]},
        "weights_used": c["weights"],
        "weights_mode": c["weights_mode"],
        "temperature_confidence": c["temp_confidence"],
        "rain_confidence": c["rain_confidence"],
        "confidence": c["temp_confidence"],
        "spread": c["temp_spread"],
        "rain_spread": c["rain_spread"],
    }

    save_forecast_history(location["name"], forecast_history)

    message = (
        f"📍 {location['name']}, {location['country']}\n\n"
        f"🌡 Температура:\n"
        f"🌦 Open-Meteo: {c['temp_values']['openmeteo']}°C\n"
        f"🌤 WeatherAPI: {c['temp_values']['weatherapi']}°C\n"
        f"🌍 Visual Crossing: {c['temp_values']['visualcrossing']}°C\n"
        f"🇳🇴 yr.no: {c['temp_values']['yr']}°C\n"
        f"🌐 Meteosource: {c['temp_values']['meteosource']}°C\n\n"
        f"☔ Осадки / rain score:\n"
        f"🌦 Open-Meteo: {c['rain_values']['openmeteo']}\n"
        f"🌤 WeatherAPI: {c['rain_values']['weatherapi']}\n"
        f"🌍 Visual Crossing: {c['rain_values']['visualcrossing']}\n"
        f"🇳🇴 yr.no: {c['rain_values']['yr']}\n"
        f"🌐 Meteosource: {c['rain_values']['meteosource']}\n\n"
        f"🧠 Weighted Consensus:\n"
        f"🌡 ~{c['avg_temp']}°C\n"
        f"💨 ~{c['avg_wind']} км/ч\n"
        f"☔ Rain Consensus: ~{c['avg_rain']}\n"
        f"📊 Temp Spread: {c['temp_spread']}°C\n"
        f"📊 Rain Spread: {c['rain_spread']}\n"
        f"✅ Temp confidence: {c['temp_confidence']}\n"
        f"✅ Rain confidence: {c['rain_confidence']}\n"
        f"⚙️ Весовой режим: {c['weights_mode']}\n\n"
        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )

    await update.message.reply_text(message)


async def today_parts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context, chat_id=update.effective_chat.id)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    try:
        parts = get_hourly_parts_sources(location)
    except Exception as e:
        await update.message.reply_text(f"Ошибка получения прогноза по частям дня: {e}")
        return

    ai_prompt = f"""
Локация:
{location['name']}

Прогноз на сегодня по частям дня:
{parts}

Дай краткий практичный вывод:
- когда комфортнее гулять
- когда выше риск дождя
- брать ли зонт
- как одеться утром/днем/вечером
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"🕒 Сегодня по частям дня\n"
        f"📍 {location['name']}, {location['country']}\n\n"
    )

    for key in ["morning", "day", "evening", "night"]:
        part = parts[key]
        rain_confidence = "низкая" if part["rain"] >= 50 else "средняя" if part["rain"] >= 25 else "высокая"

        message += (
            f"{part['title']}\n"
            f"🌡 ~{part['temp']}°C\n"
            f"☔ Дождь: ~{part['rain']}%\n"
            f"💨 Ветер: до ~{part['wind']} км/ч\n"
            f"✅ Rain confidence: {rain_confidence}\n\n"
        )

    message += (
        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )

    await update.message.reply_text(message)


async def tomorrow_parts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context, chat_id=update.effective_chat.id)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    target_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        parts = get_hourly_parts_sources(location, target_date)
    except Exception as e:
        await update.message.reply_text(f"Ошибка получения прогноза на завтра по частям дня: {e}")
        return

    ai_prompt = f"""
Локация:
{location['name']}

Прогноз на завтра по частям дня:
{parts}

Дай краткий практичный вывод:
- в какую часть дня лучше выходить/ехать
- когда выше риск дождя
- брать ли зонт или дождевик
- как одеться утром/днем/вечером
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"🕒 Завтра по частям дня\n"
        f"📍 {location['name']}, {location['country']}\n\n"
    )

    for key in ["morning", "day", "evening", "night"]:
        part = parts[key]
        rain_confidence = "низкая" if part["rain"] >= 50 else "средняя" if part["rain"] >= 25 else "высокая"

        message += (
            f"{part['title']}\n"
            f"🌡 ~{part['temp']}°C\n"
            f"☔ Дождь: ~{part['rain']}%\n"
            f"💨 Ветер: до ~{part['wind']} км/ч\n"
            f"✅ Rain confidence: {rain_confidence}\n\n"
        )

    message += (
        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )

    await update.message.reply_text(message)


async def tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context, chat_id=update.effective_chat.id)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    tomorrow_date = datetime.now() + timedelta(days=1)
    target_date = tomorrow_date.strftime("%Y-%m-%d")

    try:
        sources = get_daily_sources(location, tomorrow_date)
        c = build_consensus(location, sources)
        parts = get_hourly_parts_sources(location, target_date)
    except Exception as e:
        await update.message.reply_text(f"Ошибка получения прогноза на завтра: {e}")
        return

    best_part_key = None
    best_part_score = None

    for key in ["morning", "day", "evening", "night"]:
        part = parts.get(key, {})
        temp = part.get("temp", 0)
        rain = part.get("rain", 0)
        wind = part.get("wind", 0)

        comfort_score = 100
        comfort_score -= rain * 0.7
        comfort_score -= max(0, wind - 12) * 1.5
        comfort_score -= abs(temp - 20) * 1.2

        if best_part_score is None or comfort_score > best_part_score:
            best_part_score = comfort_score
            best_part_key = key

    best_part = parts.get(best_part_key, {}) if best_part_key else {}

    ai_prompt = f"""
Локация:
{location['name']}

Прогноз на завтра.

Общий consensus:
Температура {c['avg_temp']}
Осадки {c['avg_rain']}
Ветер {c['avg_wind']}
Уверенность температуры: {c['temp_confidence']}
Уверенность осадков: {c['rain_confidence']}

Завтра по частям дня:
{parts}

Лучшее окно:
{best_part}

Дай краткий полезный вывод:
- когда лучше выходить/ехать
- в какую часть дня выше риск дождя
- брать ли зонт
- как одеваться утром/днем/вечером
- стоит ли ехать на природу
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"📅 Прогноз на завтра\n"
        f"📍 {location['name']}, {location['country']}\n\n"

        f"🧠 Общий consensus:\n"
        f"🌡 ~{c['avg_temp']}°C\n"
        f"💨 ~{c['avg_wind']} км/ч\n"
        f"☔ ~{c['avg_rain']}%\n"
        f"📊 Temp spread: {c['temp_spread']}°C\n"
        f"📊 Rain spread: {c['rain_spread']}%\n"
        f"✅ Temp confidence: {c['temp_confidence']}\n"
        f"✅ Rain confidence: {c['rain_confidence']}\n"
        f"⚙️ Режим: {c['weights_mode']}\n\n"

        f"🕒 Завтра по частям дня:\n\n"
    )

    for key in ["morning", "day", "evening", "night"]:
        part = parts.get(key, {})
        title = part.get("title", key)
        temp = part.get("temp", 0)
        rain = part.get("rain", 0)
        wind = part.get("wind", 0)

        if rain >= 70 or wind >= 30:
            status = "🔴 рискованно"
        elif rain >= 45 or wind >= 20:
            status = "🟠 осторожно"
        elif rain >= 25 or wind >= 15:
            status = "🟡 нормально, но следить"
        else:
            status = "🟢 комфортно"

        message += (
            f"{title}\n"
            f"🌡 ~{temp}°C\n"
            f"☔ Дождь: ~{rain}%\n"
            f"💨 Ветер: до ~{wind} км/ч\n"
            f"{status}\n\n"
        )

    if best_part:
        message += (
            f"🏆 Лучшее окно: {best_part.get('title')}\n"
            f"🌡 ~{best_part.get('temp')}°C, "
            f"☔ ~{best_part.get('rain')}%, "
            f"💨 ~{best_part.get('wind')} км/ч\n\n"
        )

    message += (
        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )

    await update.message.reply_text(message)

async def weekend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context, chat_id=update.effective_chat.id)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    today = datetime.now()
    days_until_saturday = (5 - today.weekday()) % 7

    if days_until_saturday == 0 and today.hour >= 18:
        days_until_saturday = 7

    saturday = today + timedelta(days=days_until_saturday)
    sunday = saturday + timedelta(days=1)

    try:
        saturday_sources = get_daily_sources(location, saturday)
        sunday_sources = get_daily_sources(location, sunday)

        sat = build_consensus(location, saturday_sources)
        sun = build_consensus(location, sunday_sources)

    except Exception as e:
        await update.message.reply_text(f"Ошибка получения прогноза на выходные: {e}")
        return

    ai_prompt = f"""
Локация:
{location['name']}

Анализ выходных.

Суббота:
Температура {sat['avg_temp']}
Осадки {sat['avg_rain']}
Ветер {sat['avg_wind']}
Уверенность по дождю {sat['rain_confidence']}
Rain spread {sat['rain_spread']}

Воскресенье:
Температура {sun['avg_temp']}
Осадки {sun['avg_rain']}
Ветер {sun['avg_wind']}
Уверенность по дождю {sun['rain_confidence']}
Rain spread {sun['rain_spread']}

Дай практичный вывод:
- стоит ли ехать на природу
- какой день лучше
- брать ли зонт/дождевик
- комфортно ли для прогулки/поездки
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"🏕 Прогноз на выходные\n"
        f"📍 {location['name']}, {location['country']}\n\n"
        f"📅 Суббота {saturday.strftime('%Y-%m-%d')}\n"
        f"🌡 ~{sat['avg_temp']}°C\n"
        f"💨 ~{sat['avg_wind']} км/ч\n"
        f"☔ ~{sat['avg_rain']}%\n"
        f"✅ Temp: {sat['temp_confidence']}, Rain: {sat['rain_confidence']}\n"
        f"📊 Rain spread: {sat['rain_spread']}%\n\n"
        f"📅 Воскресенье {sunday.strftime('%Y-%m-%d')}\n"
        f"🌡 ~{sun['avg_temp']}°C\n"
        f"💨 ~{sun['avg_wind']} км/ч\n"
        f"☔ ~{sun['avg_rain']}%\n"
        f"✅ Temp: {sun['temp_confidence']}, Rain: {sun['rain_confidence']}\n"
        f"📊 Rain spread: {sun['rain_spread']}%\n\n"
        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )

    await update.message.reply_text(message)


async def verify_rain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history_items = load_history()

    if not history_items:
        await update.message.reply_text("История пока пустая 😢")
        return

    last_item = history_items[-1]
    location_name = last_item["location"]
    forecast = last_item["forecast"]

    if "rain" not in forecast:
        await update.message.reply_text(
            "В последнем прогнозе ещё нет данных по осадкам. Сначала вызови /weather."
        )
        return

    location = get_location_by_name(location_name)

    if not location:
        await update.message.reply_text(f"Не смог найти координаты для локации: {location_name}")
        return

    try:
        current = get_current_sources(location)
    except Exception as e:
        await update.message.reply_text(f"Ошибка получения факта по осадкам: {e}")
        return

    current_rain_values = current["rain"]
    factual_rain_score = round(sum(current_rain_values.values()) / len(current_rain_values), 1)

    saved_rain = forecast["rain"]
    predictions = {}
    errors = {}

    for source, predicted_rain in saved_rain.items():
        predictions[source] = predicted_rain
        errors[source] = round(abs(predicted_rain - factual_rain_score), 1)

    update_rain_scores(predictions, factual_rain_score)

    fact_text = "осадки есть/вероятны" if factual_rain_score >= 25 else "осадков нет или почти нет"

    best_source = min(errors, key=errors.get)
    worst_source = max(errors, key=errors.get)

    message = (
        f"☔ Проверка дождя\n\n"
        f"📍 Локация: {location_name}\n"
        f"🕒 Прогноз был сохранён: {last_item['saved_at']}\n\n"
        f"🌧 Текущий rain fact score: ~{factual_rain_score}\n"
        f"Факт: {fact_text}\n\n"
        f"📊 Ошибки по rain score:\n"
    )

    for source, error in sorted(errors.items(), key=lambda x: x[1]):
        predicted = saved_rain[source]
        predicted_text = "дождь" if predicted >= 30 else "без дождя"
        message += f"— {source}: ошибка {error}, прогноз: {predicted_text} ({predicted})\n"

    message += (
        f"\n🏆 Лучший по дождю: {best_source}\n"
        f"⚠️ Самый слабый по дождю: {worst_source}\n"
        f"💾 Результат сохранён в rain_scores.json"
    )

    await update.message.reply_text(message)


async def rain_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    scores = load_rain_scores()
    rows = []

    for model, data in scores.items():
        checks = data["checks"]
        correct = data["correct"]
        false_positive = data["false_positive"]
        missed = data["missed"]
        total_error = data["total_error"]

        if checks == 0:
            accuracy = 0
            avg_error = None
        else:
            accuracy = round((correct / checks) * 100)
            avg_error = round(total_error / checks, 1)

        rows.append({
            "model": model,
            "checks": checks,
            "accuracy": accuracy,
            "avg_error": avg_error,
            "false_positive": false_positive,
            "missed": missed,
        })

    rows = sorted(rows, key=lambda x: x["accuracy"], reverse=True)

    message = "☔ Rain model scores\n\n"

    for row in rows:
        avg_error_text = "нет данных" if row["avg_error"] is None else row["avg_error"]

        message += (
            f"— {row['model']}\n"
            f"  Проверок: {row['checks']}\n"
            f"  Accuracy: {row['accuracy']}%\n"
            f"  Средняя ошибка rain score: {avg_error_text}\n"
            f"  False positive: {row['false_positive']}\n"
            f"  Missed rain: {row['missed']}\n\n"
        )

    await update.message.reply_text(message)


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history_items = load_history()

    if not history_items:
        await update.message.reply_text("История пока пустая 😢")
        return

    last_items = history_items[-5:]
    message = "📚 Последние прогнозы:\n\n"

    for item in reversed(last_items):
        saved_at = item["saved_at"]
        location = item["location"]
        forecast = item["forecast"]

        temp = forecast["temperatures"]["consensus"]
        wind = forecast["winds"]["consensus"]
        rain = forecast.get("rain", {}).get("consensus", "нет данных")
        confidence = forecast.get("confidence", "нет данных")
        weights_mode = forecast.get("weights_mode", "old")

        message += (
            f"🕒 {saved_at}\n"
            f"📍 {location}\n"
            f"🌡 ~{temp}°C\n"
            f"💨 ~{wind} км/ч\n"
            f"☔ Rain: ~{rain}\n"
            f"✅ {confidence}\n"
            f"⚙️ {weights_mode}\n\n"
        )

    await update.message.reply_text(message)


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history_items = load_history()

    if not history_items:
        await update.message.reply_text("История пока пустая 😢")
        return

    total = len(history_items)
    locations = Counter()
    confidence_counter = Counter()
    region_counter = Counter()
    temps_by_location = defaultdict(list)
    spreads_by_location = defaultdict(list)

    for item in history_items:
        location = item["location"]
        forecast = item["forecast"]

        locations[location] += 1
        confidence_counter[forecast.get("confidence", "неизвестно")] += 1
        region_counter[forecast.get("region_type", "unknown")] += 1

        temps_by_location[location].append(forecast["temperatures"]["consensus"])
        spreads_by_location[location].append(forecast.get("spread", 0))

    message = (
        f"📊 Анализ истории прогнозов\n\n"
        f"Всего сохранено прогнозов: {total}\n\n"
        f"✅ Уверенность:\n"
    )

    for conf, count in confidence_counter.items():
        message += f"— {conf}: {count}\n"

    message += "\n📍 Локации:\n"

    for location, count in locations.most_common(5):
        avg_temp = round(sum(temps_by_location[location]) / len(temps_by_location[location]), 1)
        avg_spread = round(sum(spreads_by_location[location]) / len(spreads_by_location[location]), 1)

        message += (
            f"— {location}: {count} прогноз(ов), "
            f"средняя t ~{avg_temp}°C, "
            f"средний разброс {avg_spread}°C\n"
        )

    message += "\n🗺 Типы регионов:\n"

    for region, count in region_counter.items():
        message += f"— {region}: {count}\n"

    await update.message.reply_text(message)


async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history_items = load_history()

    if not history_items:
        await update.message.reply_text("История пока пустая 😢")
        return

    last_item = history_items[-1]
    location_name = last_item["location"]
    forecast = last_item["forecast"]

    location = get_location_by_name(location_name)

    if not location:
        await update.message.reply_text(f"Не смог найти координаты для локации: {location_name}")
        return

    try:
        current = get_current_sources(location)
    except Exception as e:
        await update.message.reply_text(f"Ошибка получения факта: {e}")
        return

    current_temp_values = current["temperatures"]
    factual_temp = round(sum(current_temp_values.values()) / len(current_temp_values), 1)

    saved_temps = forecast["temperatures"]
    errors = {}

    for source, predicted_temp in saved_temps.items():
        if source == "consensus":
            continue
        errors[source] = round(abs(predicted_temp - factual_temp), 1)

    consensus_error = round(abs(saved_temps["consensus"] - factual_temp), 1)

    best_source = min(errors, key=errors.get)
    worst_source = max(errors, key=errors.get)
    best_overall = update_model_scores(errors, consensus_error)

    message = (
        f"🔍 Проверка последнего прогноза\n\n"
        f"📍 Локация: {location_name}\n"
        f"🕒 Прогноз был сохранён: {last_item['saved_at']}\n\n"
        f"🌡 Текущий факт по 5 источникам: ~{factual_temp}°C\n"
        f"🧠 Ошибка consensus: {consensus_error}°C\n\n"
        f"📊 Ошибки моделей:\n"
    )

    for source, error in sorted(errors.items(), key=lambda x: x[1]):
        message += f"— {source}: {error}°C\n"

    message += (
        f"\n🏆 Самая точная модель: {best_source}\n"
        f"⚠️ Самая слабая модель: {worst_source}\n"
        f"💾 Результат сохранён в model_scores.json\n"
        f"🥇 Победитель с учётом consensus: {best_overall}"
    )

    await update.message.reply_text(message)


async def scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    scores_data = load_scores()
    rows = []

    for model, data in scores_data.items():
        checks = data["checks"]
        total_error = data["total_error"]
        wins = data["wins"]

        if checks == 0:
            avg_error = None
            win_rate = 0
        else:
            avg_error = round(total_error / checks, 2)
            win_rate = round((wins / checks) * 100)

        rows.append({
            "model": model,
            "checks": checks,
            "avg_error": avg_error,
            "wins": wins,
            "win_rate": win_rate,
        })

    rows = sorted(rows, key=lambda x: x["avg_error"] if x["avg_error"] is not None else 999)

    message = "🏆 Рейтинг моделей\n\n"

    for row in rows:
        avg_error_text = "нет данных" if row["avg_error"] is None else f"{row['avg_error']}°C"

        message += (
            f"— {row['model']}\n"
            f"  Проверок: {row['checks']}\n"
            f"  Средняя ошибка: {avg_error_text}\n"
            f"  Побед: {row['wins']} ({row['win_rate']}%)\n\n"
        )

    await update.message.reply_text(message)


async def adaptive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    adaptive_weights = get_adaptive_weights_from_scores()

    if not adaptive_weights:
        await update.message.reply_text(
            "Пока мало данных для adaptive weights.\n"
            "Сделай несколько проверок через /verify, потом снова вызови /adaptive."
        )
        return

    scores_data = load_scores()

    message = (
        "🧠 Adaptive weights active\n\n"
        "Эти веса автоматически используются в /weather:\n\n"
    )

    for model, weight in sorted(adaptive_weights.items(), key=lambda x: x[1], reverse=True):
        data = scores_data[model]
        checks = data["checks"]
        avg_error = round(data["total_error"] / checks, 2) if checks > 0 else "нет данных"

        message += (
            f"— {model}: {round(weight * 100)}%\n"
            f"  Проверок: {checks}, средняя ошибка: {avg_error}°C\n\n"
        )

    await update.message.reply_text(message)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history_items = load_history()
    scores_data = load_scores()
    rain_scores_data = load_rain_scores()
    adaptive_weights = get_adaptive_weights_from_scores()

    total_temp_checks = sum(
        data.get("checks", 0)
        for model, data in scores_data.items()
        if model != "consensus"
    )

    total_rain_checks = sum(
        data.get("checks", 0)
        for model, data in rain_scores_data.items()
        if model != "consensus"
    )

    best_temp_model = "нет данных"

    model_avg_errors = {}

    for model, data in scores_data.items():
        if model == "consensus":
            continue

        checks = data.get("checks", 0)

        if checks > 0:
            model_avg_errors[model] = data.get("total_error", 0) / checks

    if model_avg_errors:
        best_temp_model = min(model_avg_errors, key=model_avg_errors.get)

    best_rain_model = "нет данных"

    rain_avg_errors = {}

    for model, data in rain_scores_data.items():
        if model == "consensus":
            continue

        checks = data.get("checks", 0)

        if checks > 0:
            rain_avg_errors[model] = data.get("total_error", 0) / checks

    if rain_avg_errors:
        best_rain_model = min(rain_avg_errors, key=rain_avg_errors.get)

    locations_list = ", ".join(FAVORITE_LOCATIONS.keys())

    if adaptive_weights:
        weights_text = "\n".join(
            [f"• {model}: {weight}" for model, weight in adaptive_weights.items()]
        )
        weights_mode = "adaptive"
    else:
        weights_text = "Пока используются региональные веса."
        weights_mode = "regional"

    message = (
        f"🧠 Статус AI Weather Assistant\n\n"

        f"☁️ Режим работы:\n"
        f"✅ Cloud / Render\n"
        f"✅ Telegram polling active\n\n"

        f"📍 Локации:\n"
        f"Всего: {len(FAVORITE_LOCATIONS)}\n"
        f"{locations_list}\n\n"

        f"📚 История прогнозов:\n"
        f"Сохранено: {len(history_items)}\n\n"

        f"📊 Проверки моделей:\n"
        f"🌡 Temperature checks: {total_temp_checks}\n"
        f"☔ Rain checks: {total_rain_checks}\n\n"

        f"🏆 Лучшие модели:\n"
        f"🌡 Temperature: {best_temp_model}\n"
        f"☔ Rain: {best_rain_model}\n\n"

        f"⚙️ Веса:\n"
        f"Режим: {weights_mode}\n"
        f"{weights_text}\n\n"

        f"🧩 Основные команды:\n"
        f"/weather — текущая погода\n"
        f"/tomorrow — завтра с частями дня\n"
        f"/today_parts — сегодня по частям дня\n"
        f"/weekend — выходные\n"
        f"/history — история\n"
        f"/scores — точность моделей\n"
        f"/adaptive — adaptive weights"
    )

    await update.message.reply_text(message)


async def favorite_current(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    context.args = [key]
    await weather(update, context)



async def locations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    available_locations = "\n".join(
        [f"• {key} — {location['name']}" for key, location in FAVORITE_LOCATIONS.items()]
    )

    await update.message.reply_text(
        f"📍 Доступные локации:\n\n"
        f"{available_locations}\n\n"
        f"Чтобы выбрать дом:\n"
        f"/set_home <ключ>\n\n"
        f"Например:\n"
        f"/set_home kalyazin"
    )


async def set_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not context.args:
        user_settings = get_user_settings(chat_id)
        current_key = user_settings.get("home_location_key", "home")
        current_location = get_location_by_key(current_key) or FAVORITE_LOCATIONS["home"]

        available_locations = "\n".join(
            [f"/set_home {key}" for key in FAVORITE_LOCATIONS.keys()]
        )

        await update.message.reply_text(
            f"🏠 Текущая домашняя локация: {current_location['name']}\n\n"
            f"Чтобы изменить, используй:\n"
            f"{available_locations}\n\n"
            f"Сейчас ключ: {current_key}\n\n"
            f"Важно: это меняет home только для тебя."
        )
        return

    location_key = context.args[0].lower()

    if location_key not in FAVORITE_LOCATIONS:
        await update.message.reply_text(
            "Такой избранной локации нет 😢\n\n"
            "Напиши /set_home, чтобы увидеть доступные варианты."
        )
        return

    update_user_setting(chat_id, "home_location_key", location_key)
    location = FAVORITE_LOCATIONS[location_key]

    await update.message.reply_text(
        f"✅ Домашняя локация обновлена только для тебя.\n\n"
        f"🏠 Теперь твой home: {location['name']}, {location['country']}"
    )


def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("weather", weather))
    app.add_handler(CommandHandler("today_parts", today_parts))
    app.add_handler(CommandHandler("tomorrow_parts", tomorrow_parts))
    app.add_handler(CommandHandler("tomorrow", tomorrow))
    app.add_handler(CommandHandler("set_home", set_home))
    app.add_handler(CommandHandler("locations", locations))
    app.add_handler(CommandHandler("weekend", weekend))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(CommandHandler("verify", verify))
    app.add_handler(CommandHandler("verify_rain", verify_rain))
    app.add_handler(CommandHandler("scores", scores))
    app.add_handler(CommandHandler("rain_scores", rain_scores))
    app.add_handler(CommandHandler("adaptive", adaptive))

    app.add_handler(CommandHandler("home", lambda update, context: favorite_current(update, context, "home")))
    app.add_handler(CommandHandler("moscow", lambda update, context: favorite_current(update, context, "moscow")))
    app.add_handler(CommandHandler("moscow_ilya", lambda update, context: favorite_current(update, context, "moscow_ilya")))
    app.add_handler(CommandHandler("sergiev", lambda update, context: favorite_current(update, context, "sergiev")))
    app.add_handler(CommandHandler("kalyazin", lambda update, context: favorite_current(update, context, "kalyazin")))
    app.add_handler(CommandHandler("khvoynaya", lambda update, context: favorite_current(update, context, "khvoynaya")))
    app.add_handler(CommandHandler("lyubytino", lambda update, context: favorite_current(update, context, "lyubytino")))

    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
