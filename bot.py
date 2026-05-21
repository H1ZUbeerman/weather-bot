import os
import json
import requests
from ai_layer.reasoning import *
from storage.user_settings import get_user_settings, update_user_setting
from weather.messages import *
from outdoor.messages import *
from outdoor.advisors import *
from outdoor.scoring import *
from core.scheduler import *
from weather.learning import *
from weather.alerts import *
from weather.providers import get_openmeteo_current, get_weatherapi_current, get_visualcrossing_current, get_yr_current, get_meteosource_current
from weather.locations import *
from weather.consensus import weighted_average, calculate_confidence, rain_score_from_mm
from ai_layer.summaries import get_ai_summary
from core.config import *
from storage.json_storage import load_json_file, save_json_file
from pathlib import Path
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from collections import Counter, defaultdict

from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEATHERAPI_KEY = os.getenv("WEATHERAPI_KEY")
VISUALCROSSING_API_KEY = os.getenv("VISUALCROSSING_API_KEY")
METEOSOURCE_API_KEY = os.getenv("METEOSOURCE_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)




REGION_WEIGHTS = {
    "moscow": {"openmeteo": 0.30, "weatherapi": 0.30, "visualcrossing": 0.20, "yr": 0.10, "meteosource": 0.10},
    "north": {"openmeteo": 0.30, "weatherapi": 0.10, "visualcrossing": 0.20, "yr": 0.35, "meteosource": 0.05},
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


def simple_average(values):
    values = [v for v in values if v is not None]
    if not values:
        return 0
    return round(sum(values) / len(values), 1)


def get_user_home_location_key(chat_id):
    user_settings = get_user_settings(chat_id)
    return user_settings.get("home_location_key", "home")


def get_user_home_location(chat_id):
    home_key = get_user_home_location_key(chat_id)
    return FAVORITE_LOCATIONS.get(home_key, FAVORITE_LOCATIONS["home"])


def get_user_morning_time(chat_id):
    user_settings = get_user_settings(chat_id)
    return user_settings.get("morning_time", "08:00")


def get_user_timezone(chat_id):
    user_settings = get_user_settings(chat_id)
    return user_settings.get("timezone", "Europe/Moscow")


def get_location(context, default_key="home"):
    if not context.args:
        if default_key == "home":
            chat_id = getattr(context, "_chat_id", None)

            if chat_id:
                return get_user_home_location(chat_id)

            return FAVORITE_LOCATIONS["home"]

        return FAVORITE_LOCATIONS[default_key]

    key = context.args[0].lower()

    if key in FAVORITE_LOCATIONS:
        return FAVORITE_LOCATIONS[key]

    return get_city_coordinates(" ".join(context.args))



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


def load_morning_subscribers():
    return load_json_file(MORNING_SUBSCRIBERS_FILE, [])


def save_morning_subscribers(subscribers):
    save_json_file(MORNING_SUBSCRIBERS_FILE, subscribers)


def add_morning_subscriber(chat_id, location_key="home"):
    subscribers = load_morning_subscribers()
    chat_id = str(chat_id)

    updated = False

    for subscriber in subscribers:
        if str(subscriber.get("chat_id")) == chat_id:
            subscriber["location_key"] = location_key
            updated = True
            break

    if not updated:
        subscribers.append({
            "chat_id": chat_id,
            "location_key": location_key,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    save_morning_subscribers(subscribers)


def remove_morning_subscriber(chat_id):
    subscribers = load_morning_subscribers()
    chat_id = str(chat_id)

    subscribers = [
        subscriber for subscriber in subscribers
        if str(subscriber.get("chat_id")) != chat_id
    ]

    save_morning_subscribers(subscribers)










LOCATION_MODEL_SCORES_FILE = "location_model_scores.json"


def get_location_key_from_location(location):
    for key, favorite_location in FAVORITE_LOCATIONS.items():
        if (
            favorite_location.get("name") == location.get("name")
            and round(float(favorite_location.get("latitude")), 4) == round(float(location.get("latitude")), 4)
            and round(float(favorite_location.get("longitude")), 4) == round(float(location.get("longitude")), 4)
        ):
            return key

    for key, favorite_location in FAVORITE_LOCATIONS.items():
        if favorite_location.get("name") == location.get("name"):
            return key

    return None


def default_location_model_scores():
    return {
        "temperature": {
            "openmeteo": {"checks": 0, "total_error": 0, "wins": 0},
            "weatherapi": {"checks": 0, "total_error": 0, "wins": 0},
            "visualcrossing": {"checks": 0, "total_error": 0, "wins": 0},
            "yr": {"checks": 0, "total_error": 0, "wins": 0},
            "meteosource": {"checks": 0, "total_error": 0, "wins": 0},
            "consensus": {"checks": 0, "total_error": 0, "wins": 0},
        },
        "rain": {
            "openmeteo": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
            "weatherapi": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
            "visualcrossing": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
            "yr": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
            "meteosource": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
            "consensus": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
        },
        "wind": {
            "openmeteo": {"checks": 0, "total_error": 0, "wins": 0},
            "weatherapi": {"checks": 0, "total_error": 0, "wins": 0},
            "visualcrossing": {"checks": 0, "total_error": 0, "wins": 0},
            "yr": {"checks": 0, "total_error": 0, "wins": 0},
            "meteosource": {"checks": 0, "total_error": 0, "wins": 0},
            "consensus": {"checks": 0, "total_error": 0, "wins": 0},
        },
    }


def load_location_model_scores():
    return load_json_file(LOCATION_MODEL_SCORES_FILE, {})


def save_location_model_scores(data):
    save_json_file(LOCATION_MODEL_SCORES_FILE, data)


def ensure_location_scores(data, location_key):
    if location_key not in data:
        data[location_key] = default_location_model_scores()

    default_scores = default_location_model_scores()

    for section, models in default_scores.items():
        if section not in data[location_key]:
            data[location_key][section] = models

        for model, model_defaults in models.items():
            if model not in data[location_key][section]:
                data[location_key][section][model] = model_defaults

            for metric_key, metric_value in model_defaults.items():
                if metric_key not in data[location_key][section][model]:
                    data[location_key][section][model][metric_key] = metric_value

    return data


def update_error_scores_for_section(section_scores, errors, consensus_error):
    all_errors = dict(errors)
    all_errors["consensus"] = consensus_error

    best_model = min(all_errors, key=all_errors.get)

    for model, error in all_errors.items():
        if model not in section_scores:
            continue

        section_scores[model]["checks"] += 1
        section_scores[model]["total_error"] += error

        if model == best_model:
            section_scores[model]["wins"] += 1

    return best_model


def update_location_rain_scores(section_scores, predictions, factual_rain_score):
    fact_is_rain = factual_rain_score >= 25

    for model, predicted_score in predictions.items():
        if model not in section_scores:
            continue

        predicted_is_rain = predicted_score >= 30

        section_scores[model]["checks"] += 1
        section_scores[model]["total_error"] += abs(predicted_score - factual_rain_score)

        if predicted_is_rain == fact_is_rain:
            section_scores[model]["correct"] += 1
        elif predicted_is_rain and not fact_is_rain:
            section_scores[model]["false_positive"] += 1
        elif not predicted_is_rain and fact_is_rain:
            section_scores[model]["missed"] += 1


def update_location_model_scores(location_key, temp_errors, consensus_temp_error, wind_errors, consensus_wind_error, rain_predictions, factual_rain_score):
    if not location_key:
        return None

    data = load_location_model_scores()
    data = ensure_location_scores(data, location_key)

    best_temp_model = update_error_scores_for_section(
        data[location_key]["temperature"],
        temp_errors,
        consensus_temp_error,
    )

    best_wind_model = update_error_scores_for_section(
        data[location_key]["wind"],
        wind_errors,
        consensus_wind_error,
    )

    if rain_predictions:
        update_location_rain_scores(
            data[location_key]["rain"],
            rain_predictions,
            factual_rain_score,
        )

    save_location_model_scores(data)

    return {
        "best_temp_model": best_temp_model,
        "best_wind_model": best_wind_model,
    }


def calculate_location_adaptive_weights(location_key):
    data = load_location_model_scores()

    if location_key not in data:
        return None

    scores = data[location_key]
    models = ["openmeteo", "weatherapi", "visualcrossing", "yr", "meteosource"]

    total_checks = sum(scores.get("temperature", {}).get(model, {}).get("checks", 0) for model in models)

    if total_checks == 0:
        return None

    quality = {}

    for model in models:
        temp_data = scores["temperature"].get(model, {})
        wind_data = scores["wind"].get(model, {})
        rain_data = scores["rain"].get(model, {})

        temp_checks = temp_data.get("checks", 0)
        wind_checks = wind_data.get("checks", 0)
        rain_checks = rain_data.get("checks", 0)

        temp_quality = 0.01
        wind_quality = 0.01
        rain_quality = 0.01

        if temp_checks > 0:
            avg_temp_error = temp_data.get("total_error", 0) / temp_checks
            temp_win_rate = temp_data.get("wins", 0) / temp_checks
            temp_quality = (1 / (avg_temp_error + 0.1)) + (temp_win_rate * 0.3)

        if wind_checks > 0:
            avg_wind_error = wind_data.get("total_error", 0) / wind_checks
            wind_win_rate = wind_data.get("wins", 0) / wind_checks
            wind_quality = (1 / ((avg_wind_error / 5) + 0.1)) + (wind_win_rate * 0.2)

        if rain_checks > 0:
            avg_rain_error = rain_data.get("total_error", 0) / rain_checks
            rain_accuracy = rain_data.get("correct", 0) / rain_checks
            rain_quality = (1 / ((avg_rain_error / 20) + 0.1)) + (rain_accuracy * 0.5)

        # Температура важнее всего, потом осадки, потом ветер.
        quality[model] = (temp_quality * 0.55) + (rain_quality * 0.30) + (wind_quality * 0.15)

    total_quality = sum(quality.values())

    if total_quality <= 0:
        return None

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
    location_key = get_location_key_from_location(location)
    location_weights = calculate_location_adaptive_weights(location_key) if location_key else None

    if location_weights:
        return location_weights, f"adaptive_location:{location_key}"

    adaptive_weights = get_adaptive_weights_from_scores()

    if adaptive_weights:
        return adaptive_weights, "adaptive_global"

    region_type = location.get("region_type", "mixed")
    return REGION_WEIGHTS.get(region_type, REGION_WEIGHTS["mixed"]), f"regional:{region_type}"


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
    openmeteo = get_openmeteo_current(location)

    om_temp = openmeteo["temperature"]
    om_wind = openmeteo["wind"]
    om_rain = openmeteo["rain"]

    weatherapi = get_weatherapi_current(location, WEATHERAPI_KEY)

    wa_temp = weatherapi["temperature"]
    wa_wind = weatherapi["wind"]
    wa_rain = weatherapi["rain"]

    visualcrossing = get_visualcrossing_current(location, VISUALCROSSING_API_KEY)

    vc_temp = visualcrossing["temperature"]
    vc_wind = visualcrossing["wind"]
    vc_rain = visualcrossing["rain"]

    yr = get_yr_current(location)

    yr_temp = yr["temperature"]
    yr_wind = yr["wind"]
    yr_rain = yr["rain"]

    meteosource = get_meteosource_current(location, METEOSOURCE_API_KEY)

    ms_temp = meteosource["temperature"]
    ms_wind = meteosource["wind"]
    ms_rain = meteosource["rain"]

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


def get_severity_emoji(rain, wind):
    if rain >= 70 or wind >= 35:
        return "🔴"
    if rain >= 50 or wind >= 25:
        return "🟠"
    if rain >= 25 or wind >= 18:
        return "🟡"
    return "🟢"


def get_best_part(parts):
    best_key = None
    best_score = None

    for key, part in parts.items():
        temp = part.get("temp", 0)
        rain = part.get("rain", 0)
        wind = part.get("wind", 0)

        comfort_score = 100

        comfort_score -= rain * 0.7
        comfort_score -= max(0, wind - 12) * 1.5
        comfort_score -= abs(temp - 20) * 1.2

        if best_score is None or comfort_score > best_score:
            best_score = comfort_score
            best_key = key

    return best_key, round(best_score, 1)


def build_alerts_from_parts(parts):
    alerts = []

    for key in ["morning", "day", "evening", "night"]:
        part = parts.get(key, {})
        title = part.get("title", key)
        rain = part.get("rain", 0)
        wind = part.get("wind", 0)
        temp = part.get("temp", 0)

        if rain >= 70:
            alerts.append(f"🔴 {title}: высокий риск дождя — ~{rain}%")
        elif rain >= 50:
            alerts.append(f"🟠 {title}: заметный риск дождя — ~{rain}%")
        elif rain >= 25:
            alerts.append(f"🟡 {title}: возможны осадки — ~{rain}%")

        if wind >= 35:
            alerts.append(f"🔴 {title}: сильный ветер — до ~{wind} км/ч")
        elif wind >= 25:
            alerts.append(f"🟠 {title}: ощутимый ветер — до ~{wind} км/ч")

        if temp <= 5:
            alerts.append(f"🔵 {title}: холодно — ~{temp}°C")
        elif temp >= 28:
            alerts.append(f"🟠 {title}: жарко — ~{temp}°C")

    if not alerts:
        alerts.append("🟢 Серьёзных погодных рисков не видно.")

    return alerts


def load_learning_forecasts():
    return load_json_file(LEARNING_FILE, [])


def save_learning_forecasts(items):
    save_json_file(LEARNING_FILE, items)


def save_auto_learning_forecast(location, location_key=None):
    current = get_current_sources(location)
    c = build_consensus(location, current)

    if location_key is None:
        location_key = get_home_location_key()

    item = {
        "id": f"{location_key}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "location": location["name"],
        "location_key": location_key,
        "forecast": {
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
        },
        "verified": False,
        "verified_at": None,
        "temperature_errors": None,
        "rain_errors": None,
    }

    items = load_learning_forecasts()
    items.append(item)
    save_learning_forecasts(items)

    # Также сохраняем в общую историю, чтобы /history и /analyze видели авто-прогнозы.
    save_forecast_history(location["name"], item["forecast"])

    return item


def save_auto_learning_forecasts_for_all_locations():
    created_items = []

    for location_key, location in FAVORITE_LOCATIONS.items():
        try:
            item = save_auto_learning_forecast(location, location_key)
            created_items.append(item)
        except Exception:
            continue

    return created_items


def verify_auto_learning_forecast(item):
    location_key = item.get("location_key")
    location = get_location_by_key(location_key) if location_key else None

    if not location:
        location = get_location_by_name(item["location"])

    if not location:
        raise ValueError(f"Не найдена локация: {item['location']}")

    current = get_current_sources(location)

    current_temp_values = current["temperatures"]
    factual_temp = round(sum(current_temp_values.values()) / len(current_temp_values), 1)

    saved_temps = item["forecast"]["temperatures"]

    temp_errors = {}

    for source, predicted_temp in saved_temps.items():
        if source == "consensus":
            continue

        temp_errors[source] = round(abs(predicted_temp - factual_temp), 1)

    consensus_temp_error = round(abs(saved_temps["consensus"] - factual_temp), 1)
    best_temp_model = update_model_scores(temp_errors, consensus_temp_error)

    current_wind_values = current["winds"]
    factual_wind = round(sum(current_wind_values.values()) / len(current_wind_values), 1)

    saved_winds = item["forecast"].get("winds", {})

    wind_errors = {}

    for source, predicted_wind in saved_winds.items():
        if source == "consensus":
            continue

        wind_errors[source] = round(abs(predicted_wind - factual_wind), 1)

    consensus_wind_error = None

    if saved_winds.get("consensus") is not None:
        consensus_wind_error = round(abs(saved_winds["consensus"] - factual_wind), 1)
    else:
        consensus_wind_error = round(sum(wind_errors.values()) / len(wind_errors), 1) if wind_errors else 0

    current_rain_values = current["rain"]
    factual_rain_score = round(sum(current_rain_values.values()) / len(current_rain_values), 1)

    saved_rain = item["forecast"].get("rain", {})

    rain_predictions = {}
    rain_errors = {}

    for source, predicted_rain in saved_rain.items():
        rain_predictions[source] = predicted_rain
        rain_errors[source] = round(abs(predicted_rain - factual_rain_score), 1)

    if rain_predictions:
        update_rain_scores(rain_predictions, factual_rain_score)

    location_learning_result = update_location_model_scores(
        item.get("location_key"),
        temp_errors,
        consensus_temp_error,
        wind_errors,
        consensus_wind_error,
        rain_predictions,
        factual_rain_score,
    )

    item["verified"] = True
    item["verified_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    item["temperature_errors"] = temp_errors
    item["temperature_consensus_error"] = consensus_temp_error
    item["best_temperature_model"] = best_temp_model
    item["factual_temperature"] = factual_temp
    item["wind_errors"] = wind_errors
    item["wind_consensus_error"] = consensus_wind_error
    item["factual_wind"] = factual_wind
    item["rain_errors"] = rain_errors
    item["factual_rain_score"] = factual_rain_score

    if location_learning_result:
        item["location_best_temperature_model"] = location_learning_result.get("best_temp_model")
        item["location_best_wind_model"] = location_learning_result.get("best_wind_model")

    return item


async def location_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_location_model_scores()

    if not data:
        await update.message.reply_text(
            "Пока нет location learning данных.\n\n"
            "Запусти:\n"
            "/learning_forecast_all_now\n"
            "а позже:\n"
            "/learning_verify_all_now"
        )
        return

    requested_key = context.args[0] if context.args else None

    if requested_key:
        keys = [requested_key]
    else:
        keys = list(FAVORITE_LOCATIONS.keys())

    message = "🧠 Location learning scores\n\n"

    for location_key in keys:
        if location_key not in data:
            continue

        location = FAVORITE_LOCATIONS.get(location_key, {"name": location_key})
        weights = calculate_location_adaptive_weights(location_key)

        temp_scores = data[location_key].get("temperature", {})
        rain_scores = data[location_key].get("rain", {})
        wind_scores = data[location_key].get("wind", {})

        message += f"📍 {location.get('name')} ({location_key})\n"

        if weights:
            top_model = max(weights, key=weights.get)
            message += f"⚖️ Top weight: {top_model} — {round(weights[top_model] * 100)}%\n"

        temp_best = None
        temp_best_error = None

        for model, item in temp_scores.items():
            if model == "consensus" or item.get("checks", 0) == 0:
                continue

            avg_error = item.get("total_error", 0) / item.get("checks", 1)

            if temp_best_error is None or avg_error < temp_best_error:
                temp_best_error = avg_error
                temp_best = model

        rain_best = None
        rain_best_accuracy = None

        for model, item in rain_scores.items():
            if model == "consensus" or item.get("checks", 0) == 0:
                continue

            accuracy = item.get("correct", 0) / item.get("checks", 1)

            if rain_best_accuracy is None or accuracy > rain_best_accuracy:
                rain_best_accuracy = accuracy
                rain_best = model

        wind_best = None
        wind_best_error = None

        for model, item in wind_scores.items():
            if model == "consensus" or item.get("checks", 0) == 0:
                continue

            avg_error = item.get("total_error", 0) / item.get("checks", 1)

            if wind_best_error is None or avg_error < wind_best_error:
                wind_best_error = avg_error
                wind_best = model

        message += f"🌡 Temp best: {temp_best or 'нет данных'}"
        if temp_best_error is not None:
            message += f" (~{round(temp_best_error, 1)}°C error)"
        message += "\n"

        message += f"☔ Rain best: {rain_best or 'нет данных'}"
        if rain_best_accuracy is not None:
            message += f" (~{round(rain_best_accuracy * 100)}% accuracy)"
        message += "\n"

        message += f"💨 Wind best: {wind_best or 'нет данных'}"
        if wind_best_error is not None:
            message += f" (~{round(wind_best_error, 1)} км/ч error)"
        message += "\n\n"

        if len(message) > 3300:
            message += "...список сокращён."
            break

    await update.message.reply_text(message)


async def subscribe_learning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = load_settings()
    settings["learning_enabled"] = True
    save_settings(settings)

    await update.message.reply_text(
        "✅ Автообучение включено.\n\n"
        "Как работает:\n"
        "08:00 — бот сам сохраняет прогноз по home location.\n"
        "20:00 — бот сам проверяет факт и обновляет model_scores/rain_scores.\n\n"
        "Проверить статус:\n"
        "/learning_status"
    )


async def unsubscribe_learning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = load_settings()
    settings["learning_enabled"] = False
    save_settings(settings)

    await update.message.reply_text(
        "✅ Автообучение отключено."
    )


async def learning_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = load_settings()
    items = load_learning_forecasts()

    total = len(items)
    verified = len([item for item in items if item.get("verified")])
    pending = total - verified

    learning_enabled = settings.get("learning_enabled", False)

    last_forecast_date = settings.get("last_learning_forecast_date", "")
    last_verify_date = settings.get("last_learning_verify_date", "")

    message = (
        f"🧠 Auto-learning status\n\n"
        f"Статус: {'✅ включено' if learning_enabled else '❌ выключено'}\n\n"
        f"📚 Всего авто-прогнозов: {total}\n"
        f"✅ Проверено: {verified}\n"
        f"⏳ Ожидают проверки: {pending}\n\n"
        f"🕗 Последний авто-прогноз: {last_forecast_date or 'нет данных'}\n"
        f"🕗 Последняя авто-проверка: {last_verify_date or 'нет данных'}\n\n"
        f"🏠 Home location: {get_home_location()['name']}\n"
        f"📍 Auto-learning locations: {len(FAVORITE_LOCATIONS)}\n\n"
        f"Файлы:\n"
        f"📁 learning_forecasts.json\n"
        f"📁 model_scores.json\n"
        f"📁 rain_scores.json\n"
        f"📁 location_model_scores.json"
    )

    await update.message.reply_text(message)


async def run_learning_forecast_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_home_location()

    try:
        item = save_auto_learning_forecast(location)
    except Exception as e:
        await update.message.reply_text(f"Ошибка ручного авто-прогноза: {e}")
        return

    await update.message.reply_text(
        f"✅ Learning forecast сохранён вручную.\n\n"
        f"📍 {item['location']}\n"
        f"🕒 {item['created_at']}\n"
        f"🌡 Consensus: ~{item['forecast']['temperatures']['consensus']}°C\n"
        f"☔ Rain: ~{item['forecast']['rain']['consensus']}"
    )


async def run_learning_verify_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = load_learning_forecasts()
    pending_items = [item for item in items if not item.get("verified")]

    if not pending_items:
        await update.message.reply_text("Нет авто-прогнозов, ожидающих проверки.")
        return

    item = pending_items[-1]

    try:
        verified_item = verify_auto_learning_forecast(item)
    except Exception as e:
        await update.message.reply_text(f"Ошибка ручной авто-проверки: {e}")
        return

    for index, existing_item in enumerate(items):
        if existing_item.get("id") == verified_item.get("id"):
            items[index] = verified_item
            break

    save_learning_forecasts(items)

    best_rain_model = None

    if verified_item.get("rain_errors"):
        best_rain_model = min(
            verified_item["rain_errors"],
            key=verified_item["rain_errors"].get
        )

    await update.message.reply_text(
        f"✅ Learning verify выполнен вручную.\n\n"
        f"📍 {verified_item['location']}\n"
        f"🌡 Факт: ~{verified_item['factual_temperature']}°C\n"
        f"🏆 Лучшая temp-модель: {verified_item['best_temperature_model']}\n"
        f"☔ Rain fact score: ~{verified_item['factual_rain_score']}\n"
        f"🏆 Лучшая rain-модель: {best_rain_model or 'нет данных'}\n\n"
        f"Scores обновлены."
    )


async def check_learning_schedule(context: ContextTypes.DEFAULT_TYPE):
    settings = load_settings()

    if not settings.get("learning_enabled", False):
        return

    now = datetime.now(ZoneInfo(settings.get("timezone", "Europe/Moscow")))
    current_time = now.strftime("%H:%M")
    current_date = now.strftime("%Y-%m-%d")

    # Утром сохраняем прогнозы по всем избранным локациям.
    if current_time == "08:00" and settings.get("last_learning_forecast_date") != current_date:
        try:
            save_auto_learning_forecasts_for_all_locations()
            settings["last_learning_forecast_date"] = current_date
            save_settings(settings)

        except Exception:
            return

    # Вечером проверяем все непроверенные прогнозы за сегодня.
    if current_time == "20:00" and settings.get("last_learning_verify_date") != current_date:
        items = load_learning_forecasts()
        pending_items = [
            item for item in items
            if not item.get("verified") and item.get("date") == current_date
        ]

        if not pending_items:
            return

        changed = False

        for item in pending_items:
            try:
                verified_item = verify_auto_learning_forecast(item)

                for index, existing_item in enumerate(items):
                    if existing_item.get("id") == verified_item.get("id"):
                        items[index] = verified_item
                        changed = True
                        break

            except Exception:
                continue

        if changed:
            save_learning_forecasts(items)

        settings["last_learning_verify_date"] = current_date
        save_settings(settings)


async def learning_forecast_all_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        items = save_auto_learning_forecasts_for_all_locations()
    except Exception as e:
        await update.message.reply_text(f"Ошибка learning forecast all: {e}")
        return

    await update.message.reply_text(
        f"✅ Learning forecast сохранён по всем избранным локациям.\n\n"
        f"Сохранено: {len(items)}\n"
        f"Локации: {', '.join([item['location_key'] for item in items])}"
    )


async def learning_verify_all_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = load_learning_forecasts()
    pending_items = [item for item in items if not item.get("verified")]

    if not pending_items:
        await update.message.reply_text("Нет авто-прогнозов, ожидающих проверки.")
        return

    verified_count = 0

    for item in pending_items:
        try:
            verified_item = verify_auto_learning_forecast(item)

            for index, existing_item in enumerate(items):
                if existing_item.get("id") == verified_item.get("id"):
                    items[index] = verified_item
                    verified_count += 1
                    break

        except Exception:
            continue

    save_learning_forecasts(items)

    await update.message.reply_text(
        f"✅ Learning verify выполнен по всем доступным непроверенным прогнозам.\n\n"
        f"Проверено: {verified_count}\n"
        f"Scores обновлены."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌦 AI Weather Assistant\n\n"
        "Команды:\n"
        "/weather\n"
        "/alerts\n"
        "/danger_alerts\n"
        "/subscribe_danger_alerts\n"
        "/unsubscribe_danger_alerts\n"
        "/danger_status\n"
        "/trip\n"
        "/baidarka\n"
        "/camping\n"
        "/morning\n"
        "/subscribe_morning\n"
        "/unsubscribe_morning\n"
        "/today_parts\n"
        "/tomorrow_parts\n"
        "/tomorrow\n"
        "/weekend\n"
        "/weekend_parts\n"
        "/week\n"
        "/week_parts\n"
        "/history\n"
        "/analyze\n"
        "/verify\n"
        "/verify_rain\n"
        "/scores\n"
        "/rain_scores\n"
        "/adaptive\n\n"
        "Локации:\n"
        "/home\n"
        "/moscow\n"
        "/sergiev\n"
        "/kalyazin\n"
        "/khvoynaya\n"
        "/lyubytino"
    )


async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    try:
        current = get_current_sources(location)
        c = build_consensus(location, current)
    except Exception as e:
        await update.message.reply_text(f"Ошибка получения погоды: {e}")
        return

    ai_prompt = build_weather_prompt(location, c)

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

    message = build_weather_message(location, c, ai_summary)

    await update.message.reply_text(message)


def build_morning_message_for_location(location):
    current = get_current_sources(location)
    current_consensus = build_consensus(location, current)
    parts = get_hourly_parts_sources(location)

    morning_part = parts.get("morning", {})
    day_part = parts.get("day", {})
    evening_part = parts.get("evening", {})
    night_part = parts.get("night", {})

    ai_prompt = build_morning_prompt(
        location,
        current_consensus,
        morning_part,
        day_part,
        evening_part,
        night_part,
    )

    ai_summary = get_ai_summary(ai_prompt)

    return build_morning_message(
        location,
        current_consensus,
        morning_part,
        day_part,
        evening_part,
        night_part,
        ai_summary,
    )
    ai_summary = get_ai_summary(ai_prompt)

    return (
        f"🌅 Утренний прогноз\n"
        f"📍 {location['name']}, {location['country']}\n\n"

        f"📌 Сейчас:\n"
        f"🌡 ~{current_consensus['avg_temp']}°C\n"
        f"💨 ~{current_consensus['avg_wind']} км/ч\n"
        f"☔ Rain: ~{current_consensus['avg_rain']}\n"
        f"✅ Temp: {current_consensus['temp_confidence']}, Rain: {current_consensus['rain_confidence']}\n"
        f"⚙️ Режим: {current_consensus['weights_mode']}\n\n"

        f"🕒 Сегодня:\n"
        f"🌅 Утро: ~{morning_part.get('temp')}°C, дождь ~{morning_part.get('rain')}%, ветер до ~{morning_part.get('wind')} км/ч\n"
        f"☀️ День: ~{day_part.get('temp')}°C, дождь ~{day_part.get('rain')}%, ветер до ~{day_part.get('wind')} км/ч\n"
        f"🌆 Вечер: ~{evening_part.get('temp')}°C, дождь ~{evening_part.get('rain')}%, ветер до ~{evening_part.get('wind')} км/ч\n"
        f"🌙 Ночь: ~{night_part.get('temp')}°C, дождь ~{night_part.get('rain')}%, ветер до ~{night_part.get('wind')} км/ч\n\n"

        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )



async def trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context)

    if not location:
        await update.message.reply_text(
            "Локация не найдена 😢\n\n"
            "Пример:\n"
            "/trip kalyazin\n"
            "/trip khvoynaya"
        )
        return

    today = datetime.now()
    tomorrow_date = today + timedelta(days=1)

    days_until_saturday = (5 - today.weekday()) % 7

    if days_until_saturday == 0 and today.hour >= 18:
        days_until_saturday = 7

    saturday = today + timedelta(days=days_until_saturday)
    sunday = saturday + timedelta(days=1)

    try:
        tomorrow_sources = get_daily_sources(location, tomorrow_date)
        tomorrow_c = build_consensus(location, tomorrow_sources)

        saturday_sources = get_daily_sources(location, saturday)
        saturday_c = build_consensus(location, saturday_sources)

        sunday_sources = get_daily_sources(location, sunday)
        sunday_c = build_consensus(location, sunday_sources)

        week_items = []

        for i in range(7):
            target_day = today + timedelta(days=i)
            sources = get_daily_sources(location, target_day)
            c = build_consensus(location, sources)

            score = calculate_trip_score(
                c["avg_temp"],
                c["avg_rain"],
                c["avg_wind"],
                c["rain_spread"],
            )

            week_items.append({
                "date": target_day.strftime("%Y-%m-%d"),
                "weekday": target_day.strftime("%a"),
                "temp": c["avg_temp"],
                "rain": c["avg_rain"],
                "wind": c["avg_wind"],
                "rain_confidence": c["rain_confidence"],
                "rain_spread": c["rain_spread"],
                "score": score,
                "recommendation": trip_recommendation_from_score(score),
            })

    except Exception as e:
        await update.message.reply_text(f"Ошибка анализа поездки: {e}")
        return

    tomorrow_score = calculate_trip_score(
        tomorrow_c["avg_temp"],
        tomorrow_c["avg_rain"],
        tomorrow_c["avg_wind"],
        tomorrow_c["rain_spread"],
    )

    saturday_score = calculate_trip_score(
        saturday_c["avg_temp"],
        saturday_c["avg_rain"],
        saturday_c["avg_wind"],
        saturday_c["rain_spread"],
    )

    sunday_score = calculate_trip_score(
        sunday_c["avg_temp"],
        sunday_c["avg_rain"],
        sunday_c["avg_wind"],
        sunday_c["rain_spread"],
    )

    best_week_day = max(week_items, key=lambda x: x["score"])
    worst_week_day = min(week_items, key=lambda x: x["score"])

    ai_prompt = build_trip_prompt(
    location,
    tomorrow_c,
    tomorrow_score,
    saturday_c,
    saturday_score,
    sunday_c,
    sunday_score,
    week_items,
)

    ai_summary = get_ai_summary(ai_prompt)

    message = build_trip_message(
    location,
    tomorrow_date,
    tomorrow_c,
    tomorrow_score,
    saturday,
    saturday_c,
    saturday_score,
    sunday,
    sunday_c,
    sunday_score,
    best_week_day,
    worst_week_day,
    ai_summary,
    trip_recommendation_from_score,
)

    await update.message.reply_text(message)



async def baidarka(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context)

    if not location:
        await update.message.reply_text(
            "Локация не найдена 😢\n\n"
            "Пример:\n"
            "/baidarka kalyazin\n"
            "/baidarka khvoynaya"
        )
        return

    today = datetime.now()
    tomorrow = today + timedelta(days=1)

    days_until_saturday = (5 - today.weekday()) % 7

    if days_until_saturday == 0 and today.hour >= 18:
        days_until_saturday = 7

    saturday = today + timedelta(days=days_until_saturday)
    sunday = saturday + timedelta(days=1)

    try:
        today_parts_data = get_hourly_parts_sources(location, today.strftime("%Y-%m-%d"))
        tomorrow_parts_data = get_hourly_parts_sources(location, tomorrow.strftime("%Y-%m-%d"))

        saturday_sources = get_daily_sources(location, saturday)
        saturday_c = build_consensus(location, saturday_sources)

        sunday_sources = get_daily_sources(location, sunday)
        sunday_c = build_consensus(location, sunday_sources)

    except Exception as e:
        await update.message.reply_text(f"Ошибка анализа условий для байдарки: {e}")
        return

    # Анализируем лучшие окна сегодня и завтра по частям дня.
    part_candidates = []

    for day_label, date_obj, parts in [
        ("Сегодня", today, today_parts_data),
        ("Завтра", tomorrow, tomorrow_parts_data),
    ]:
        for key in ["morning", "day", "evening"]:
            part = parts[key]

            score = calculate_baidarka_score(
                part.get("temp", 0),
                part.get("rain", 0),
                part.get("wind", 0),
                0,
            )

            part_candidates.append({
                "day_label": day_label,
                "date": date_obj.strftime("%Y-%m-%d"),
                "part_title": part.get("title"),
                "temp": part.get("temp", 0),
                "rain": part.get("rain", 0),
                "wind": part.get("wind", 0),
                "score": score,
                "recommendation": baidarka_recommendation(
                    score,
                    part.get("wind", 0),
                    part.get("rain", 0),
                ),
            })

    best_window = max(part_candidates, key=lambda x: x["score"])
    worst_window = min(part_candidates, key=lambda x: x["score"])

    saturday_score = calculate_baidarka_score(
        saturday_c["avg_temp"],
        saturday_c["avg_rain"],
        saturday_c["avg_wind"],
        saturday_c["rain_spread"],
    )

    sunday_score = calculate_baidarka_score(
        sunday_c["avg_temp"],
        sunday_c["avg_rain"],
        sunday_c["avg_wind"],
        sunday_c["rain_spread"],
    )

    ai_prompt = build_baidarka_prompt(
    location,
    part_candidates,
    best_window,
    worst_window,
    saturday_c,
    saturday_score,
    sunday_c,
    sunday_score,
)

    ai_summary = get_ai_summary(ai_prompt)

    message = build_baidarka_message(
    location,
    best_window,
    worst_window,
    saturday,
    saturday_c,
    saturday_score,
    sunday,
    sunday_c,
    sunday_score,
    ai_summary,
    baidarka_recommendation,
)

    await update.message.reply_text(message)



async def camping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context)

    if not location:
        await update.message.reply_text(
            "Локация не найдена 😢\n\n"
            "Пример:\n"
            "/camping kalyazin\n"
            "/camping khvoynaya"
        )
        return

    today = datetime.now()

    try:
        week_data = []

        for i in range(7):
            target_day = today + timedelta(days=i)
            target_date = target_day.strftime("%Y-%m-%d")

            parts = get_hourly_parts_sources(location, target_date)

            morning = parts["morning"]
            day = parts["day"]
            evening = parts["evening"]
            night = parts["night"]

            overall_score = (
                calculate_camping_score(
                    day.get("temp", 0),
                    day.get("rain", 0),
                    day.get("wind", 0),
                    0,
                    False,
                ) * 0.4
                +
                calculate_camping_score(
                    evening.get("temp", 0),
                    evening.get("rain", 0),
                    evening.get("wind", 0),
                    0,
                    False,
                ) * 0.3
                +
                calculate_camping_score(
                    night.get("temp", 0),
                    night.get("rain", 0),
                    night.get("wind", 0),
                    0,
                    True,
                ) * 0.3
            )

            overall_score = round(overall_score, 1)

            week_data.append({
                "date": target_date,
                "weekday": target_day.strftime("%a"),
                "day_temp": day.get("temp", 0),
                "night_temp": night.get("temp", 0),
                "rain": max(
                    morning.get("rain", 0),
                    day.get("rain", 0),
                    evening.get("rain", 0),
                    night.get("rain", 0),
                ),
                "wind": max(
                    morning.get("wind", 0),
                    day.get("wind", 0),
                    evening.get("wind", 0),
                    night.get("wind", 0),
                ),
                "score": overall_score,
                "recommendation": camping_recommendation(
                    overall_score,
                    max(
                        morning.get("rain", 0),
                        day.get("rain", 0),
                        evening.get("rain", 0),
                        night.get("rain", 0),
                    ),
                    max(
                        morning.get("wind", 0),
                        day.get("wind", 0),
                        evening.get("wind", 0),
                        night.get("wind", 0),
                    ),
                    night.get("temp", 0),
                ),
                "parts": parts,
            })

    except Exception as e:
        await update.message.reply_text(f"Ошибка camping mode: {e}")
        return

    best_day = max(week_data, key=lambda x: x["score"])
    worst_day = min(week_data, key=lambda x: x["score"])

    ai_prompt = build_camping_prompt(
    location,
    week_data,
    best_day,
    worst_day,
)

    ai_summary = get_ai_summary(ai_prompt)

    message = build_camping_message(
    location,
    best_day,
    worst_day,
    week_data,
    ai_summary,
)
    for item in week_data:
        message += (
            f"📅 {item['date']} ({item['weekday']}) — "
            f"{item['score']}/100\n"
            f"🌡 День ~{item['day_temp']}°C / Ночь ~{item['night_temp']}°C\n"
            f"☔ ~{item['rain']}% | 💨 ~{item['wind']} км/ч\n"
            f"{item['recommendation']}\n\n"
        )

    message += (
        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )

    await update.message.reply_text(message)


    data = requests.get(url, timeout=20).json()
    hourly = data.get("hourly", {})

    times = hourly.get("time", [])
    temperatures = hourly.get("temperature_2m", [])
    precip_probs = hourly.get("precipitation_probability", [])
    precipitations = hourly.get("precipitation", [])
    rains = hourly.get("rain", [])
    showers = hourly.get("showers", [])
    snowfalls = hourly.get("snowfall", [])
    weather_codes = hourly.get("weather_code", [])
    winds = hourly.get("wind_speed_10m", [])
    gusts = hourly.get("wind_gusts_10m", [])
    visibility = hourly.get("visibility", [])

    events = []

    for index, time_str in enumerate(times[:hours_limit]):
        temp = temperatures[index] if index < len(temperatures) else 0
        precip_prob = precip_probs[index] if index < len(precip_probs) else 0
        precipitation = precipitations[index] if index < len(precipitations) else 0
        rain = rains[index] if index < len(rains) else 0
        shower = showers[index] if index < len(showers) else 0
        snowfall = snowfalls[index] if index < len(snowfalls) else 0
        code = weather_codes[index] if index < len(weather_codes) else 0
        wind = winds[index] if index < len(winds) else 0
        gust = gusts[index] if index < len(gusts) else 0
        vis = visibility[index] if index < len(visibility) else None

        local_time = datetime.fromisoformat(time_str)
        formatted_time = local_time.strftime("%d.%m %H:%M")
        description = weather_code_description(code)

        # Дождь
        if precip_prob >= 70 or precipitation >= 3 or rain >= 2 or shower >= 2 or code in [61, 63, 65, 80, 81, 82]:
            severity = "🔴" if precip_prob >= 85 or precipitation >= 5 or code in [65, 82] else "🟠"
            events.append({
                "time": formatted_time,
                "type": "rain",
                "severity": severity,
                "text": f"{severity} 🌧 Дождь: {formatted_time}, вероятность ~{precip_prob}%, осадки ~{precipitation} мм, {description}",
            })

        # Гроза
        if code in [95, 96, 99]:
            severity = "🔴" if code in [96, 99] else "🟠"
            events.append({
                "time": formatted_time,
                "type": "storm",
                "severity": severity,
                "text": f"{severity} ⛈ Гроза: {formatted_time}, {description}",
            })

        # Град
        if code in [96, 99]:
            events.append({
                "time": formatted_time,
                "type": "hail",
                "severity": "🔴",
                "text": f"🔴 🧊 Риск града: {formatted_time}, {description}",
            })

        # Снег
        if snowfall > 0 or code in [71, 73, 75, 77, 85, 86]:
            severity = "🔴" if snowfall >= 2 or code in [75, 86] else "🟠"
            events.append({
                "time": formatted_time,
                "type": "snow",
                "severity": severity,
                "text": f"{severity} ❄️ Снег: {formatted_time}, снег ~{snowfall} мм, {description}",
            })

        # Сильный ветер
        if wind >= 30 or gust >= 45:
            severity = "🔴" if wind >= 40 or gust >= 60 else "🟠"
            events.append({
                "time": formatted_time,
                "type": "wind",
                "severity": severity,
                "text": f"{severity} 💨 Сильный ветер: {formatted_time}, ветер ~{wind} км/ч, порывы ~{gust} км/ч",
            })

        # Туман
        if code in [45, 48] or (vis is not None and vis <= 1000):
            events.append({
                "time": formatted_time,
                "type": "fog",
                "severity": "🟡",
                "text": f"🟡 🌫 Туман/плохая видимость: {formatted_time}, видимость ~{vis} м, {description}",
            })

        # Жара
        if temp >= 30:
            severity = "🔴" if temp >= 35 else "🟠"
            events.append({
                "time": formatted_time,
                "type": "heat",
                "severity": severity,
                "text": f"{severity} 🔥 Жара: {formatted_time}, температура ~{temp}°C",
            })

        # Резкое похолодание / холод
        if temp <= -10:
            severity = "🔴" if temp <= -20 else "🟠"
            events.append({
                "time": formatted_time,
                "type": "cold",
                "severity": severity,
                "text": f"{severity} 🥶 Сильный холод: {formatted_time}, температура ~{temp}°C",
            })

    # Уберём дубли по одному типу и времени.
    unique = []
    seen = set()

    for event in events:
        key = (event["time"], event["type"])
        if key not in seen:
            unique.append(event)
            seen.add(key)

    return unique

def add_danger_subscriber(chat_id, location_key="home"):
    subscribers = load_danger_subscribers()
    chat_id = str(chat_id)

    updated = False

    for subscriber in subscribers:
        if str(subscriber.get("chat_id")) == chat_id:
            subscriber["location_key"] = location_key
            updated = True
            break

    if not updated:
        subscribers.append({
            "chat_id": chat_id,
            "location_key": location_key,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_alert_signature": "",
            "last_alert_date": "",
        })

    save_danger_subscribers(subscribers)


def remove_danger_subscriber(chat_id):
    subscribers = load_danger_subscribers()
    chat_id = str(chat_id)

    subscribers = [
        subscriber for subscriber in subscribers
        if str(subscriber.get("chat_id")) != chat_id
    ]

    save_danger_subscribers(subscribers)



def build_danger_message(location, events, auto=False):
    if not events:
        return (
            f"🟢 Опасных погодных событий не найдено\n\n"
            f"📍 {location['name']}, {location['country']}\n"
            f"Период: ближайшие 48 часов"
        )

    prefix = "🚨 Auto Danger Alert" if auto else "⚠️ Danger Weather Alerts"

    message = (
        f"{prefix}\n"
        f"📍 {location['name']}, {location['country']}\n"
        f"Период: ближайшие 48 часов\n\n"
    )

    for event in events[:10]:
        message += f"{event['text']}\n"

    if len(events) > 10:
        message += f"\n...и ещё {len(events) - 10} событий.\n"

    # Для авто-алертов делаем коротко, без GPT, чтобы не тратить токены каждый час.
    if auto:
        message += (
            "\nСовет: проверь планы на улицу, дорогу, воду или кемпинг. "
            "Если риск связан с дождём/грозой — лучше иметь дождевик и запасной план."
        )

    return message


async def subscribe_danger_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    location_key = "home"

    if context.args:
        requested_key = context.args[0].lower()

        if requested_key not in FAVORITE_LOCATIONS:
            await update.message.reply_text(
                "Такой избранной локации нет 😢\n\n"
                "Пример:\n"
                "/subscribe_danger_alerts home\n"
                "/subscribe_danger_alerts kalyazin\n"
                "/subscribe_danger_alerts khvoynaya"
            )
            return

        location_key = requested_key

    add_danger_subscriber(chat_id, location_key)

    if location_key == "home":
        location = get_user_home_location(chat_id)
    else:
        location = get_location_by_key(location_key)

    await update.message.reply_text(
        f"✅ Авто-алерты опасной погоды включены только для тебя.\n\n"
        f"📍 Локация: {location['name']}\n"
        f"Проверка: каждый час\n"
        f"Период анализа: ближайшие 48 часов\n\n"
        f"Бот будет писать только если найдёт риск: дождь, сильный ветер, грозу, снег, град, туман, жару или сильный холод."
    )


async def unsubscribe_danger_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remove_danger_subscriber(update.effective_chat.id)

    await update.message.reply_text(
        "✅ Авто-алерты опасной погоды отключены."
    )


async def danger_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribers = load_danger_subscribers()
    chat_id = str(update.effective_chat.id)

    subscription = None

    for subscriber in subscribers:
        if str(subscriber.get("chat_id")) == chat_id:
            subscription = subscriber
            break

    if not subscription:
        await update.message.reply_text(
            "🚨 Danger alerts: ❌ отключены\n\n"
            "Включить:\n"
            "/subscribe_danger_alerts\n"
            "/subscribe_danger_alerts kalyazin"
        )
        return

    location_key = subscription.get("location_key", "home")

    if location_key == "home":
        location = get_user_home_location(chat_id)
    else:
        location = get_location_by_key(location_key)

    await update.message.reply_text(
        f"🚨 Danger alerts: ✅ включены\n\n"
        f"📍 Локация: {location['name']}\n"
        f"Проверка: каждый час\n"
        f"Период анализа: ближайшие 48 часов\n"
        f"Последний alert date: {subscription.get('last_alert_date') or 'нет данных'}"
    )


async def check_danger_alerts_schedule(context: ContextTypes.DEFAULT_TYPE):
    subscribers = load_danger_subscribers()

    if not subscribers:
        return

    updated_subscribers = []

    for subscriber in subscribers:
        chat_id = str(subscriber.get("chat_id"))
        location_key = subscriber.get("location_key", "home")

        if location_key == "home":
            location = get_user_home_location(chat_id)
        else:
            location = get_location_by_key(location_key)

        if not location:
            updated_subscribers.append(subscriber)
            continue

        try:
            events = detect_danger_events(location, hours_limit=48)

            if not events:
                updated_subscribers.append(subscriber)
                continue

            signature = build_danger_signature(events)
            today = datetime.now().strftime("%Y-%m-%d")

            # Не шлём одно и то же повторно.
            if (
                subscriber.get("last_alert_signature") == signature
                and subscriber.get("last_alert_date") == today
            ):
                updated_subscribers.append(subscriber)
                continue

            message = build_danger_message(location, events, auto=True)

            await context.bot.send_message(
                chat_id=int(chat_id),
                text=message
            )

            subscriber["last_alert_signature"] = signature
            subscriber["last_alert_date"] = today

        except Exception:
            pass

        updated_subscribers.append(subscriber)

    save_danger_subscribers(updated_subscribers)


async def morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # По умолчанию утренний брифинг делаем по дому.
    # Но можно вызвать /morning kalyazin или /morning khvoynaya.
    location = get_location(context, default_key="home")

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    try:
        message = build_morning_message_for_location(location)
    except Exception as e:
        await update.message.reply_text(f"Ошибка утреннего прогноза: {e}")
        return

    await update.message.reply_text(message)


async def subscribe_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    location_key = "home"

    if context.args:
        requested_key = context.args[0].lower()

        if requested_key not in FAVORITE_LOCATIONS:
            await update.message.reply_text(
                "Такой избранной локации нет 😢\n\n"
                "Пример:\n"
                "/subscribe_morning home\n"
                "/subscribe_morning kalyazin\n"
                "/subscribe_morning khvoynaya"
            )
            return

        location_key = requested_key

    add_morning_subscriber(chat_id, location_key)

    if location_key == "home":
        location = get_user_home_location(chat_id)
    else:
        location = get_location_by_key(location_key)

    morning_time = get_user_morning_time(chat_id)

    await update.message.reply_text(
        f"✅ Утренний прогноз включён только для тебя.\n"
        f"Локация: {location['name']}\n"
        f"Время: каждый день в {morning_time} по Москве.\n\n"
        f"Проверить вручную можно командой:\n"
        f"/morning {location_key}"
    )


async def unsubscribe_morning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remove_morning_subscriber(update.effective_chat.id)

    await update.message.reply_text(
        "✅ Утренний прогноз отключён."
    )


async def send_scheduled_morning(context: ContextTypes.DEFAULT_TYPE):
    subscribers = load_morning_subscribers()

    for subscriber in subscribers:
        chat_id = subscriber.get("chat_id")
        location_key = subscriber.get("location_key", "home")
        location = get_location_by_key(location_key)

        try:
            message = build_morning_message_for_location(location)
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=message
            )

        except Exception as e:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=f"Ошибка утреннего прогноза: {e}"
            )


async def today_parts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context)

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
    location = get_location(context)

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
    location = get_location(context)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    tomorrow_date = datetime.now() + timedelta(days=1)

    try:
        sources = get_daily_sources(location, tomorrow_date)
        c = build_consensus(location, sources)
    except Exception as e:
        await update.message.reply_text(f"Ошибка получения прогноза на завтра: {e}")
        return

    ai_prompt = f"""
Локация:
{location['name']}

Прогноз на завтра.

Температуры:
{c['temp_values']}

Осадки:
{c['rain_values']}

Ветер:
{c['wind_values']}

Consensus:
Температура {c['avg_temp']}
Осадки {c['avg_rain']}
Ветер {c['avg_wind']}

Уверенность температуры:
{c['temp_confidence']}

Уверенность осадков:
{c['rain_confidence']}

Дай краткий полезный вывод:
- брать ли зонт
- как одеваться
- стоит ли ехать на природу
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"📅 Прогноз на завтра\n"
        f"📍 {location['name']}, {location['country']}\n\n"
        f"🌡 Температура:\n"
        f"🌦 Open-Meteo: {c['temp_values']['openmeteo']}°C\n"
        f"🌤 WeatherAPI: {c['temp_values']['weatherapi']}°C\n"
        f"🌍 Visual Crossing: {c['temp_values']['visualcrossing']}°C\n"
        f"🇳🇴 yr.no: {c['temp_values']['yr']}°C\n"
        f"🌐 Meteosource: {c['temp_values']['meteosource']}°C\n\n"
        f"☔ Вероятность осадков:\n"
        f"🌦 Open-Meteo: {c['rain_values']['openmeteo']}%\n"
        f"🌤 WeatherAPI: {c['rain_values']['weatherapi']}%\n"
        f"🌍 Visual Crossing: {c['rain_values']['visualcrossing']}%\n"
        f"🇳🇴 yr.no: {c['rain_values']['yr']}%\n"
        f"🌐 Meteosource: {c['rain_values']['meteosource']}%\n\n"
        f"🧠 Consensus:\n"
        f"🌡 ~{c['avg_temp']}°C\n"
        f"💨 ~{c['avg_wind']} км/ч\n"
        f"☔ ~{c['avg_rain']}%\n"
        f"📊 Temp spread: {c['temp_spread']}°C\n"
        f"📊 Rain spread: {c['rain_spread']}%\n"
        f"✅ Temp confidence: {c['temp_confidence']}\n"
        f"✅ Rain confidence: {c['rain_confidence']}\n"
        f"⚙️ Режим: {c['weights_mode']}\n\n"
        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )

    await update.message.reply_text(message)


async def weekend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context)

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


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    today = datetime.now()

    daily_results = []

    try:
        for i in range(7):
            target_day = today + timedelta(days=i)
            sources = get_daily_sources(location, target_day)
            c = build_consensus(location, sources)

            daily_results.append({
                "date": target_day.strftime("%Y-%m-%d"),
                "weekday": target_day.strftime("%a"),
                "avg_temp": c["avg_temp"],
                "avg_wind": c["avg_wind"],
                "avg_rain": c["avg_rain"],
                "temp_confidence": c["temp_confidence"],
                "rain_confidence": c["rain_confidence"],
                "rain_spread": c["rain_spread"],
                "weights_mode": c["weights_mode"],
            })

    except Exception as e:
        await update.message.reply_text(f"Ошибка получения прогноза на неделю: {e}")
        return

    ai_prompt = f"""
Локация:
{location['name']}

Прогноз на 7 дней:
{daily_results}

Дай краткий полезный вывод:
- какие дни самые комфортные
- когда выше риск дождя
- когда лучше планировать поездку/прогулку
- где прогноз спорный
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"📆 Прогноз на неделю\n"
        f"📍 {location['name']}, {location['country']}\n\n"
    )

    for item in daily_results:
        message += (
            f"📅 {item['date']} ({item['weekday']})\n"
            f"🌡 ~{item['avg_temp']}°C\n"
            f"💨 ~{item['avg_wind']} км/ч\n"
            f"☔ ~{item['avg_rain']}%\n"
            f"✅ Temp: {item['temp_confidence']}, Rain: {item['rain_confidence']}\n"
            f"📊 Rain spread: {item['rain_spread']}%\n\n"
        )

    message += (
        f"⚙️ Режим: {daily_results[0]['weights_mode']}\n\n"
        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )

    await update.message.reply_text(message)


async def weekend_parts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    today = datetime.now()
    days_until_saturday = (5 - today.weekday()) % 7

    if days_until_saturday == 0 and today.hour >= 18:
        days_until_saturday = 7

    saturday = today + timedelta(days=days_until_saturday)
    sunday = saturday + timedelta(days=1)

    saturday_date = saturday.strftime("%Y-%m-%d")
    sunday_date = sunday.strftime("%Y-%m-%d")

    try:
        saturday_parts = get_hourly_parts_sources(location, saturday_date)
        sunday_parts = get_hourly_parts_sources(location, sunday_date)

    except Exception as e:
        await update.message.reply_text(f"Ошибка получения прогноза выходных по частям дня: {e}")
        return

    ai_prompt = f"""
Локация:
{location['name']}

Выходные по частям дня.

Суббота:
{saturday_parts}

Воскресенье:
{sunday_parts}

Дай практичный вывод:
- какой день лучше для поездки/прогулки
- когда выше риск дождя
- когда лучше планировать активность на улице
- брать ли зонт/дождевик
- есть ли смысл ехать на природу
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"🏕 Выходные по частям дня\n"
        f"📍 {location['name']}, {location['country']}\n\n"
        f"📅 Суббота {saturday_date}\n"
    )

    for key in ["morning", "day", "evening", "night"]:
        part = saturday_parts[key]
        rain_confidence = "низкая" if part["rain"] >= 50 else "средняя" if part["rain"] >= 25 else "высокая"

        message += (
            f"{part['title']}\n"
            f"🌡 ~{part['temp']}°C\n"
            f"☔ Дождь: ~{part['rain']}%\n"
            f"💨 Ветер: до ~{part['wind']} км/ч\n"
            f"✅ Rain confidence: {rain_confidence}\n\n"
        )

    message += f"📅 Воскресенье {sunday_date}\n"

    for key in ["morning", "day", "evening", "night"]:
        part = sunday_parts[key]
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


async def week_parts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    today = datetime.now()

    week_data = []

    try:
        for i in range(7):
            target_day = today + timedelta(days=i)
            target_date = target_day.strftime("%Y-%m-%d")
            parts = get_hourly_parts_sources(location, target_date)

            day_summary = {
                "date": target_date,
                "weekday": target_day.strftime("%a"),
                "parts": parts,
            }

            week_data.append(day_summary)

    except Exception as e:
        await update.message.reply_text(f"Ошибка получения прогноза недели по частям дня: {e}")
        return

    ai_prompt = f"""
Локация:
{location['name']}

Прогноз на неделю по частям дня:
{week_data}

Дай краткий полезный вывод:
- какие дни лучше для прогулок/поездок
- в какие дни и части дня выше риск дождя
- где стоит брать зонт
- какой день самый комфортный
- какой день лучше избегать для активностей на улице
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"📆 Неделя по частям дня\n"
        f"📍 {location['name']}, {location['country']}\n\n"
    )

    for day in week_data:
        message += f"📅 {day['date']} ({day['weekday']})\n"

        for key in ["morning", "day", "evening", "night"]:
            part = day["parts"][key]

            message += (
                f"{part['title']}: "
                f"🌡 ~{part['temp']}°C, "
                f"☔ ~{part['rain']}%, "
                f"💨 до ~{part['wind']} км/ч\n"
            )

        message += "\n"

    message += (
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


def is_valid_time_string(value):
    try:
        hour_str, minute_str = value.split(":")
        hour = int(hour_str)
        minute = int(minute_str)

        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return True

        return False

    except Exception:
        return False


async def set_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not context.args:
        user_settings = get_user_settings(chat_id)
        current_key = user_settings.get("home_location_key", "home")
        current_location = get_location_by_key(current_key)

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
            "Доступные варианты:\n"
            "home, moscow_center, moscow_north, moscow_south, "
            "moscow_west, moscow_east, sergiev, "
            "kalyazin, khvoynaya, lyubytino"
        )
        return

    update_user_setting(chat_id, "home_location_key", location_key)

    location = FAVORITE_LOCATIONS[location_key]

    await update.message.reply_text(
        f"✅ Домашняя локация обновлена только для тебя.\n\n"
        f"🏠 Теперь твой home: {location['name']}, {location['country']}\n\n"
        f"У других пользователей home не изменится."
    )


async def set_morning_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not context.args:
        user_settings = get_user_settings(chat_id)

        await update.message.reply_text(
            f"🕒 Текущее время твоего утреннего прогноза: {user_settings.get('morning_time', '08:00')}\n\n"
            f"Чтобы изменить, используй формат HH:MM:\n"
            f"/set_morning_time 07:30\n"
            f"/set_morning_time 09:00\n\n"
            f"Важно: это меняет время только для тебя."
        )
        return

    new_time = context.args[0].strip()

    if not is_valid_time_string(new_time):
        await update.message.reply_text(
            "Неверный формат времени 😢\n\n"
            "Используй формат HH:MM, например:\n"
            "/set_morning_time 07:30"
        )
        return

    update_user_setting(chat_id, "morning_time", new_time)

    await update.message.reply_text(
        f"✅ Время утреннего прогноза обновлено только для тебя.\n\n"
        f"Теперь бот будет присылать тебе прогноз каждый день в {new_time} по Москве.\n\n"
        f"Проверить настройки:\n"
        f"/status"
    )


def format_best_temperature_model(scores_data):
    rows = []

    for model, data in scores_data.items():
        checks = data.get("checks", 0)

        if checks == 0:
            continue

        avg_error = round(data.get("total_error", 0) / checks, 2)
        wins = data.get("wins", 0)
        win_rate = round((wins / checks) * 100)

        rows.append({
            "model": model,
            "checks": checks,
            "avg_error": avg_error,
            "wins": wins,
            "win_rate": win_rate,
        })

    if not rows:
        return None

    rows = sorted(rows, key=lambda x: x["avg_error"])
    return rows[0], rows


def format_best_rain_model(rain_scores_data):
    rows = []

    for model, data in rain_scores_data.items():
        checks = data.get("checks", 0)

        if checks == 0:
            continue

        correct = data.get("correct", 0)
        accuracy = round((correct / checks) * 100)
        avg_error = round(data.get("total_error", 0) / checks, 1)
        false_positive = data.get("false_positive", 0)
        missed = data.get("missed", 0)

        rows.append({
            "model": model,
            "checks": checks,
            "accuracy": accuracy,
            "avg_error": avg_error,
            "false_positive": false_positive,
            "missed": missed,
        })

    if not rows:
        return None

    rows = sorted(rows, key=lambda x: (-x["accuracy"], x["avg_error"]))
    return rows[0], rows


async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history_items = load_history()
    learning_items = load_learning_forecasts()
    scores_data = load_scores()
    rain_scores_data = load_rain_scores()
    settings = load_settings()

    adaptive_weights = get_adaptive_weights_from_scores()

    temp_best_result = format_best_temperature_model(scores_data)
    rain_best_result = format_best_rain_model(rain_scores_data)

    total_history = len(history_items)
    total_learning = len(learning_items)
    verified_learning = len([item for item in learning_items if item.get("verified")])
    pending_learning = total_learning - verified_learning

    home_location = get_home_location()

    learning_enabled = settings.get("learning_enabled", False)
    morning_time = settings.get("morning_time", "08:00")

    if temp_best_result:
        best_temp, temp_rows = temp_best_result
        temp_text = (
            f"🏆 {best_temp['model']}\n"
            f"Средняя ошибка: {best_temp['avg_error']}°C\n"
            f"Проверок: {best_temp['checks']}, побед: {best_temp['wins']} ({best_temp['win_rate']}%)"
        )
    else:
        temp_rows = []
        temp_text = "Пока нет данных. Сделай /verify или включи /subscribe_learning."

    if rain_best_result:
        best_rain, rain_rows = rain_best_result
        rain_text = (
            f"🏆 {best_rain['model']}\n"
            f"Accuracy: {best_rain['accuracy']}%\n"
            f"Средняя ошибка rain score: {best_rain['avg_error']}\n"
            f"False positive: {best_rain['false_positive']}, missed: {best_rain['missed']}"
        )
    else:
        rain_rows = []
        rain_text = "Пока нет данных. Сделай /verify_rain или включи /subscribe_learning."

    if adaptive_weights:
        adaptive_text = ""

        for model, weight in sorted(adaptive_weights.items(), key=lambda x: x[1], reverse=True):
            adaptive_text += f"— {model}: {round(weight * 100)}%\n"

    else:
        adaptive_text = "Пока недостаточно данных."

    message = (
        f"📊 AI Weather Dashboard\n\n"

        f"🏠 Home: {home_location['name']}\n"
        f"🌅 Morning time: {morning_time}\n"
        f"🧠 Auto-learning: {'✅ включено' if learning_enabled else '❌ выключено'}\n\n"

        f"📚 Data:\n"
        f"История прогнозов: {total_history}\n"
        f"Auto-learning прогнозов: {total_learning}\n"
        f"Проверено auto-learning: {verified_learning}\n"
        f"Ожидают проверки: {pending_learning}\n\n"

        f"🌡 Temperature model leader:\n"
        f"{temp_text}\n\n"

        f"☔ Rain model leader:\n"
        f"{rain_text}\n\n"

        f"⚖️ Current adaptive weights:\n"
        f"{adaptive_text}\n"
    )

    # Добавим короткий топ по температуре, если есть место.
    if temp_rows:
        message += "\n🌡 Top temperature models:\n"

        for row in temp_rows[:5]:
            message += (
                f"— {row['model']}: "
                f"{row['avg_error']}°C avg error, "
                f"{row['checks']} checks\n"
            )

    if rain_rows:
        message += "\n☔ Top rain models:\n"

        for row in rain_rows[:5]:
            message += (
                f"— {row['model']}: "
                f"{row['accuracy']}% accuracy, "
                f"{row['avg_error']} avg error\n"
            )

    await update.message.reply_text(message)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    subscribers = load_morning_subscribers()
    history_items = load_history()
    scores_data = load_scores()
    rain_scores_data = load_rain_scores()
    user_settings = get_user_settings(chat_id)
    home_location = get_user_home_location(chat_id)

    current_subscription = None

    for subscriber in subscribers:
        if str(subscriber.get("chat_id")) == chat_id:
            current_subscription = subscriber
            break

    adaptive_weights = get_adaptive_weights_from_scores()
    adaptive_enabled = adaptive_weights is not None

    total_model_checks = sum(
        model.get("checks", 0)
        for model in scores_data.values()
    )

    total_rain_checks = sum(
        model.get("checks", 0)
        for model in rain_scores_data.values()
    )

    if current_subscription:
        location_key = current_subscription.get("location_key", "home")

        if location_key == "home":
            location = get_user_home_location(chat_id)
        else:
            location = get_location_by_key(location_key)

        morning_status = (
            f"✅ Включен\n"
            f"📍 Локация: {location['name']}\n"
            f"🕒 Время: {user_settings.get('morning_time', '08:00')} {user_settings.get('timezone', 'Europe/Moscow')}"
        )
    else:
        morning_status = "❌ Отключен"

    files_status = (
        f"📁 weather_history.json\n"
        f"📁 model_scores.json\n"
        f"📁 rain_scores.json\n"
        f"📁 morning_subscribers.json\n"
        f"📁 settings.json\n"
        f"📁 user_settings.json"
    )

    if adaptive_enabled:
        best_model = max(adaptive_weights, key=adaptive_weights.get)
        adaptive_text = (
            f"✅ Активен\n"
            f"🏆 Лучшая модель сейчас: {best_model}\n"
            f"⚖️ Вес: {round(adaptive_weights[best_model] * 100)}%"
        )
    else:
        adaptive_text = "❌ Пока недостаточно данных"

    message = (
        f"🧠 Статус AI Weather Assistant\n\n"

        f"👤 User settings:\n"
        f"Chat ID: {chat_id}\n"
        f"🏠 Home: {home_location['name']} ({user_settings.get('home_location_key', 'home')})\n"
        f"🕒 Morning time: {user_settings.get('morning_time', '08:00')} {user_settings.get('timezone', 'Europe/Moscow')}\n\n"

        f"🌅 Morning alerts:\n"
        f"{morning_status}\n\n"

        f"📚 История прогнозов:\n"
        f"Сохранено: {len(history_items)}\n\n"

        f"📊 Проверки моделей:\n"
        f"🌡 Temperature verify: {total_model_checks}\n"
        f"☔ Rain verify: {total_rain_checks}\n\n"

        f"⚙️ Adaptive weights:\n"
        f"{adaptive_text}\n\n"

        f"💾 Локальные файлы:\n"
        f"{files_status}\n\n"

        f"🖥 Режим работы:\n"
        f"Cloud/Render autonomous mode"
    )

    await update.message.reply_text(message)


async def check_morning_schedule(context: ContextTypes.DEFAULT_TYPE):
    subscribers = load_morning_subscribers()

    if not subscribers:
        return

    for subscriber in subscribers:
        chat_id = str(subscriber.get("chat_id"))
        user_settings = get_user_settings(chat_id)

        timezone_name = user_settings.get("timezone", "Europe/Moscow")
        morning_time = user_settings.get("morning_time", "08:00")
        last_sent_date = user_settings.get("last_morning_sent_date", "")

        if not is_valid_time_string(morning_time):
            continue

        now = datetime.now(ZoneInfo(timezone_name))
        current_time = now.strftime("%H:%M")
        current_date = now.strftime("%Y-%m-%d")

        if current_time != morning_time:
            continue

        if last_sent_date == current_date:
            continue

        location_key = subscriber.get("location_key", "home")

        if location_key == "home":
            location = get_user_home_location(chat_id)
        else:
            location = get_location_by_key(location_key)

        if not location:
            continue

        try:
            message = build_morning_message_for_location(location)
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=message
            )
            update_user_setting(chat_id, "last_morning_sent_date", current_date)

        except Exception as e:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=f"Ошибка утреннего прогноза: {e}"
            )


async def favorite_current(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    context.args = [key]
    await weather(update, context)




async def alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    today_date = datetime.now().strftime("%Y-%m-%d")
    tomorrow_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        today_parts_data = get_hourly_parts_sources(location, today_date)
        tomorrow_parts_data = get_hourly_parts_sources(location, tomorrow_date)

    except Exception as e:
        await update.message.reply_text(f"Ошибка получения alerts: {e}")
        return

    today_alerts = build_alerts_from_parts(today_parts_data)
    tomorrow_alerts = build_alerts_from_parts(tomorrow_parts_data)

    today_best_key, today_best_score = get_best_part(today_parts_data)
    tomorrow_best_key, tomorrow_best_score = get_best_part(tomorrow_parts_data)

    today_best = today_parts_data[today_best_key]
    tomorrow_best = tomorrow_parts_data[tomorrow_best_key]

    ai_prompt = f"""
Локация:
{location['name']}

Сегодня по частям дня:
{today_parts_data}

Завтра по частям дня:
{tomorrow_parts_data}

Предупреждения сегодня:
{today_alerts}

Предупреждения завтра:
{tomorrow_alerts}

Лучшее окно сегодня:
{today_best}

Лучшее окно завтра:
{tomorrow_best}

Дай короткий практичный вывод:
- главные риски
- лучшее окно сегодня
- лучшее окно завтра
- брать ли зонт
- стоит ли переносить планы
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"⚠️ Smart Weather Alerts\n"
        f"📍 {location['name']}, {location['country']}\n\n"
        f"📅 Сегодня {today_date}\n"
    )

    for alert in today_alerts:
        message += f"{alert}\n"

    message += (
        f"\n✅ Лучшее окно сегодня:\n"
        f"{today_best.get('title')} — "
        f"~{today_best.get('temp')}°C, "
        f"дождь ~{today_best.get('rain')}%, "
        f"ветер до ~{today_best.get('wind')} км/ч\n"
        f"Score: {today_best_score}\n\n"
        f"📅 Завтра {tomorrow_date}\n"
    )

    for alert in tomorrow_alerts:
        message += f"{alert}\n"

    message += (
        f"\n✅ Лучшее окно завтра:\n"
        f"{tomorrow_best.get('title')} — "
        f"~{tomorrow_best.get('temp')}°C, "
        f"дождь ~{tomorrow_best.get('rain')}%, "
        f"ветер до ~{tomorrow_best.get('wind')} км/ч\n"
        f"Score: {tomorrow_best_score}\n\n"
        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )

    await update.message.reply_text(message)

async def danger_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    location = get_location(context)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    try:
        events = detect_danger_events(location, hours_limit=48)
    except Exception as e:
        await update.message.reply_text(f"Ошибка danger alerts: {e}")
        return

    if not events:
        await update.message.reply_text(
            f"🟢 Опасных погодных событий не найдено\n\n"
            f"📍 {location['name']}, {location['country']}\n"
            f"Период: ближайшие 48 часов"
        )
        return

    message = build_danger_message(location, events, auto=False)

    await update.message.reply_text(message)

async def setup_bot_commands(app):
    commands = [
        BotCommand("weather", "Текущая погода"),
        BotCommand("tomorrow", "Прогноз на завтра"),
        BotCommand("weekend", "Прогноз на выходные"),
        BotCommand("week", "Прогноз на неделю"),
        BotCommand("today_parts", "Сегодня по частям дня"),
        BotCommand("tomorrow_parts", "Завтра по частям дня"),
        BotCommand("weekend_parts", "Выходные по частям дня"),
        BotCommand("week_parts", "Неделя по частям дня"),
        BotCommand("alerts", "Погодные alerts"),
        BotCommand("danger_alerts", "Опасные погодные явления"),
        BotCommand("subscribe_danger_alerts", "Подписка на danger alerts"),
        BotCommand("unsubscribe_danger_alerts", "Отключить danger alerts"),
        BotCommand("danger_status", "Статус danger alerts"),
        BotCommand("morning", "Утренний прогноз"),
        BotCommand("subscribe_morning", "Подписка на утренний прогноз"),
        BotCommand("set_morning_time", "Время утреннего прогноза"),
        BotCommand("trip", "Режим поездки"),
        BotCommand("baidarka", "Режим байдарки"),
        BotCommand("camping", "Режим палатки"),
        BotCommand("dashboard", "Dashboard"),
        BotCommand("scores", "Веса моделей"),
        BotCommand("adaptive", "Adaptive weights"),
        BotCommand("location_scores", "Learning по локациям"),
        BotCommand("status", "Статус бота"),
        BotCommand("set_home", "Изменить домашнюю локацию"),
    ]

    await app.bot.set_my_commands(commands)

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.post_init = setup_bot_commands

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("weather", weather))
    app.add_handler(CommandHandler("alerts", alerts))
    app.add_handler(CommandHandler("danger_alerts", danger_alerts))
    app.add_handler(CommandHandler("subscribe_danger_alerts", subscribe_danger_alerts))
    app.add_handler(CommandHandler("unsubscribe_danger_alerts", unsubscribe_danger_alerts))
    app.add_handler(CommandHandler("danger_status", danger_status))
    app.add_handler(CommandHandler("trip", trip))
    app.add_handler(CommandHandler("baidarka", baidarka))
    app.add_handler(CommandHandler("camping", camping))
    app.add_handler(CommandHandler("morning", morning))
    app.add_handler(CommandHandler("subscribe_morning", subscribe_morning))
    app.add_handler(CommandHandler("unsubscribe_morning", unsubscribe_morning))
    app.add_handler(CommandHandler("today_parts", today_parts))
    app.add_handler(CommandHandler("tomorrow_parts", tomorrow_parts))
    app.add_handler(CommandHandler("tomorrow", tomorrow))
    app.add_handler(CommandHandler("weekend", weekend))
    app.add_handler(CommandHandler("weekend_parts", weekend_parts))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("week_parts", week_parts))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(CommandHandler("verify", verify))
    app.add_handler(CommandHandler("verify_rain", verify_rain))
    app.add_handler(CommandHandler("scores", scores))
    app.add_handler(CommandHandler("rain_scores", rain_scores))
    app.add_handler(CommandHandler("adaptive", adaptive))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("subscribe_learning", subscribe_learning))
    app.add_handler(CommandHandler("unsubscribe_learning", unsubscribe_learning))
    app.add_handler(CommandHandler("learning_status", learning_status))
    app.add_handler(CommandHandler("location_scores", location_scores))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("learning_forecast_now", run_learning_forecast_now))
    app.add_handler(CommandHandler("learning_verify_now", run_learning_verify_now))
    app.add_handler(CommandHandler("learning_forecast_all_now", learning_forecast_all_now))
    app.add_handler(CommandHandler("learning_verify_all_now", learning_verify_all_now))
    app.add_handler(CommandHandler("set_home", set_home))
    app.add_handler(CommandHandler("set_morning_time", set_morning_time))

    app.add_handler(CommandHandler("home", lambda update, context: favorite_current(update, context, "home")))
    app.add_handler(CommandHandler("moscow", lambda update, context: favorite_current(update, context, "moscow")))
    app.add_handler(CommandHandler("sergiev", lambda update, context: favorite_current(update, context, "sergiev")))
    app.add_handler(CommandHandler("kalyazin", lambda update, context: favorite_current(update, context, "kalyazin")))
    app.add_handler(CommandHandler("khvoynaya", lambda update, context: favorite_current(update, context, "khvoynaya")))
    app.add_handler(CommandHandler("lyubytino", lambda update, context: favorite_current(update, context, "lyubytino")))

    if app.job_queue:
        app.job_queue.run_repeating(
            check_morning_schedule,
            interval=60,
            first=5,
            name="dynamic_morning_weather",
        )

        app.job_queue.run_repeating(
            check_learning_schedule,
            interval=60,
            first=10,
            name="auto_learning_engine",
        )

        app.job_queue.run_repeating(
            check_danger_alerts_schedule,
            interval=3600,
            first=30,
            name="danger_weather_monitor",
        )

    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
