import os
import json
import csv
import requests
from io import BytesIO, StringIO
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import Counter, defaultdict

from dotenv import load_dotenv
from openai import OpenAI
from telegram import BotCommand, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from outdoor.scoring import (
    baidarka_recommendation,
    calculate_baidarka_score,
    calculate_camping_score,
    calculate_trip_score,
    camping_recommendation,
    trip_recommendation_from_score,
)

load_dotenv()


def get_env_value(name):
    value = os.getenv(name)
    return value.strip() if value else None


TOKEN = get_env_value("TELEGRAM_BOT_TOKEN")
WEATHERAPI_KEY = get_env_value("WEATHERAPI_KEY")
VISUALCROSSING_API_KEY = get_env_value("VISUALCROSSING_API_KEY")
METEOSOURCE_API_KEY = get_env_value("METEOSOURCE_API_KEY")
OPENAI_API_KEY = get_env_value("OPENAI_API_KEY")
DATA_DIR = Path(get_env_value("DATA_DIR") or ".")

HISTORY_FILE = "weather_history.json"
SCORES_FILE = "model_scores.json"
RAIN_SCORES_FILE = "rain_scores.json"


USER_SETTINGS_FILE = "settings.json"
MORNING_SUBSCRIBERS_FILE = "morning_subscribers.json"
LEARNING_FILE = "learning_forecasts.json"
DANGER_SUBSCRIBERS_FILE = "danger_subscribers.json"
RAIN_ALERT_SUBSCRIBERS_FILE = "rain_alert_subscribers.json"
DEFAULT_TIMEZONE = "Europe/Moscow"
EXPORT_FILES = [
    USER_SETTINGS_FILE,
    MORNING_SUBSCRIBERS_FILE,
    RAIN_ALERT_SUBSCRIBERS_FILE,
    LEARNING_FILE,
    SCORES_FILE,
    RAIN_SCORES_FILE,
    HISTORY_FILE,
    DANGER_SUBSCRIBERS_FILE,
]
PROFILE_OPTIONS = {
    "cold": {
        "title": "я мерзну",
        "instruction": "Пользователь мерзнет: советуй одеваться немного теплее обычного, особенно утром и вечером.",
    },
    "car": {
        "title": "я на машине",
        "instruction": "Пользователь на машине: обращай внимание на дождь, ветер, видимость, скользкую дорогу и время для поездки.",
    },
    "rain": {
        "title": "важны дожди",
        "instruction": "Пользователю особенно важны дожди: говори про риск осадков строже и явно советуй зонт/дождевик.",
    },
    "camping": {
        "title": "палатка",
        "instruction": "Пользователь планирует палатку: выделяй ночную температуру, дождь вечером/ночью, ветер и запасной план.",
    },
    "kayak": {
        "title": "байдарка",
        "instruction": "Пользователь планирует байдарку: отдельно оцени ветер, порывы, грозу, дождь и безопасность на воде.",
    },
}


def get_data_path(filename):
    path = Path(filename)

    if path.is_absolute():
        return path

    return DATA_DIR / path


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_user_settings():
    path = get_data_path(USER_SETTINGS_FILE)

    if not path.exists():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_user_settings(data):
    ensure_data_dir()

    with open(get_data_path(USER_SETTINGS_FILE), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user_settings(chat_id):
    data = load_user_settings()
    return data.get(str(chat_id), {})


def load_all_user_settings():
    data = load_user_settings()
    return data if isinstance(data, dict) else {}


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


def build_location_options(current_key=None, command_prefix="/set_home"):
    lines = []

    for key, location in FAVORITE_LOCATIONS.items():
        marker = " ← текущий" if key == current_key else ""
        lines.append(f"{command_prefix} {key} — {location['name']}{marker}")

    return "\n".join(lines)


def normalize_profile_keys(raw_profiles):
    if not isinstance(raw_profiles, list):
        return []

    return [
        profile
        for profile in raw_profiles
        if profile in PROFILE_OPTIONS
    ]


def get_user_profiles(chat_id):
    settings = get_user_settings(chat_id)
    return normalize_profile_keys(settings.get("profiles", []))


def save_user_profiles(chat_id, profiles):
    update_user_setting(chat_id, "profiles", normalize_profile_keys(profiles))


def build_profile_text(profiles):
    if not profiles:
        return "не настроен"

    return ", ".join(PROFILE_OPTIONS[profile]["title"] for profile in profiles)


def build_profile_instructions(chat_id):
    profiles = get_user_profiles(chat_id)

    if not profiles:
        return "Профиль пользователя: не настроен."

    instructions = "\n".join(
        f"- {PROFILE_OPTIONS[profile]['instruction']}"
        for profile in profiles
    )

    return f"Профиль пользователя:\n{instructions}"


def build_export_payload():
    exported_at = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
    files = {}

    for filename in EXPORT_FILES:
        files[filename] = load_json_file(filename, None)

    return {
        "exported_at": exported_at,
        "data_dir": str(DATA_DIR),
        "files": files,
    }


def build_learning_csv():
    forecasts = load_learning_forecasts()
    output = StringIO()
    fieldnames = [
        "id",
        "chat_id",
        "date",
        "created_at",
        "verified",
        "verified_at",
        "location",
        "location_key",
        "source",
        "predicted_temp",
        "factual_temp",
        "temp_error",
        "predicted_rain",
        "factual_rain",
        "rain_error",
        "weights_mode",
        "temp_confidence",
        "rain_confidence",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for item in forecasts:
        forecast = item.get("forecast", {})
        temperatures = forecast.get("temperatures", {})
        rain_values = forecast.get("rain", {})
        temp_errors = item.get("temperature_errors", {})
        rain_errors = item.get("rain_errors", {})
        sources = sorted(set(temperatures.keys()) | set(rain_values.keys()))

        for source in sources:
            writer.writerow({
                "id": item.get("id", ""),
                "chat_id": item.get("chat_id", ""),
                "date": item.get("date", ""),
                "created_at": item.get("created_at", ""),
                "verified": item.get("verified", False),
                "verified_at": item.get("verified_at", ""),
                "location": item.get("location", ""),
                "location_key": item.get("location_key", ""),
                "source": source,
                "predicted_temp": temperatures.get(source, ""),
                "factual_temp": item.get("factual_temperature", ""),
                "temp_error": temp_errors.get(source, ""),
                "predicted_rain": rain_values.get(source, ""),
                "factual_rain": item.get("factual_rain_score", ""),
                "rain_error": rain_errors.get(source, ""),
                "weights_mode": forecast.get("weights_mode", ""),
                "temp_confidence": forecast.get("temperature_confidence", ""),
                "rain_confidence": forecast.get("rain_confidence", ""),
            })

    return output.getvalue()


def average(values):
    values = [value for value in values if value is not None]

    if not values:
        return None

    return round(sum(values) / len(values), 2)


def format_avg_error(value, suffix=""):
    if value is None:
        return "нет данных"

    return f"{value}{suffix}"


def build_learning_report_text():
    forecasts = load_learning_forecasts()
    verified_items = [item for item in forecasts if item.get("verified")]
    pending_count = len(forecasts) - len(verified_items)
    temp_errors_by_source = defaultdict(list)
    rain_errors_by_source = defaultdict(list)
    location_counter = Counter()

    for item in verified_items:
        location_counter[item.get("location", "unknown")] += 1

        for source, error in item.get("temperature_errors", {}).items():
            temp_errors_by_source[source].append(error)

        for source, error in item.get("rain_errors", {}).items():
            if source != "consensus":
                rain_errors_by_source[source].append(error)

    temp_rows = sorted(
        [
            (source, len(errors), average(errors))
            for source, errors in temp_errors_by_source.items()
        ],
        key=lambda row: row[2] if row[2] is not None else 999,
    )
    rain_rows = sorted(
        [
            (source, len(errors), average(errors))
            for source, errors in rain_errors_by_source.items()
        ],
        key=lambda row: row[2] if row[2] is not None else 999,
    )

    message = (
        f"🧪 Learning report\n\n"
        f"Всего записей: {len(forecasts)}\n"
        f"Проверено: {len(verified_items)}\n"
        f"Ждут проверки: {pending_count}\n\n"
    )

    if temp_rows:
        message += "🌡 Температура: средняя ошибка\n"

        for source, checks, avg_error in temp_rows[:6]:
            message += f"• {source}: {format_avg_error(avg_error, '°C')} ({checks} проверок)\n"

        message += "\n"
    else:
        message += "🌡 Температура: пока нет проверенных данных\n\n"

    if rain_rows:
        message += "☔ Дождь: средняя ошибка rain score\n"

        for source, checks, avg_error in rain_rows[:6]:
            message += f"• {source}: {format_avg_error(avg_error)} ({checks} проверок)\n"

        message += "\n"
    else:
        message += "☔ Дождь: пока нет проверенных данных\n\n"

    if location_counter:
        message += "📍 Локации с проверками\n"

        for location, count in location_counter.most_common(5):
            message += f"• {location}: {count}\n"

        message += "\n"

    if verified_items:
        message += "🕒 Последние проверки\n"

        for item in verified_items[-3:][::-1]:
            message += (
                f"• {item.get('verified_at', 'нет даты')} — {item.get('location', 'unknown')}: "
                f"факт {item.get('factual_temperature', '—')}°C, "
                f"rain {item.get('factual_rain_score', '—')}, "
                f"лучшая t: {item.get('best_temperature_model', '—')}\n"
            )

    return message


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
    path = get_data_path(filename)

    if not path.exists():
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def count_json_items(filename):
    data = load_json_file(filename, [])

    if isinstance(data, list):
        return len(data)

    if isinstance(data, dict):
        return len(data)

    return 0


def save_json_file(filename, data):
    ensure_data_dir()

    with open(get_data_path(filename), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_morning_subscribers():
    subscribers = load_json_file(MORNING_SUBSCRIBERS_FILE, [])
    return subscribers if isinstance(subscribers, list) else []


def save_morning_subscribers(subscribers):
    save_json_file(MORNING_SUBSCRIBERS_FILE, subscribers)


def load_rain_alert_subscribers():
    subscribers = load_json_file(RAIN_ALERT_SUBSCRIBERS_FILE, [])
    return subscribers if isinstance(subscribers, list) else []


def save_rain_alert_subscribers(subscribers):
    save_json_file(RAIN_ALERT_SUBSCRIBERS_FILE, subscribers)


def find_rain_alert_subscriber(subscribers, chat_id):
    chat_key = str(chat_id)

    for subscriber in subscribers:
        if str(subscriber.get("chat_id")) == chat_key:
            return subscriber

    return None


def load_learning_forecasts():
    forecasts = load_json_file(LEARNING_FILE, [])
    return forecasts if isinstance(forecasts, list) else []


def save_learning_forecasts(forecasts):
    save_json_file(LEARNING_FILE, forecasts)


def find_morning_subscriber(subscribers, chat_id):
    chat_key = str(chat_id)

    for subscriber in subscribers:
        if str(subscriber.get("chat_id")) == chat_key:
            return subscriber

    return None


def is_valid_time(value):
    if len(value) != 5 or value[2] != ":":
        return False

    try:
        datetime.strptime(value, "%H:%M")
        return True
    except ValueError:
        return False


def resolve_subscriber_location(subscriber):
    chat_id = subscriber.get("chat_id")
    location_key = subscriber.get("location_key", "home")

    if location_key == "home":
        return get_home_location_for_chat(chat_id)

    return get_location_by_key(location_key)


def normalize_learning_locations(raw_locations):
    if not isinstance(raw_locations, list):
        return []

    locations = []

    for location_key in raw_locations:
        if location_key in FAVORITE_LOCATIONS and location_key not in locations:
            locations.append(location_key)

    return locations


def get_learning_locations(settings):
    locations = normalize_learning_locations(settings.get("learning_location_keys", []))

    if locations:
        return locations

    legacy_location = settings.get("learning_location_key", "home")

    if legacy_location in FAVORITE_LOCATIONS:
        return [legacy_location]

    return ["home"]


def save_learning_locations(chat_id, locations):
    update_user_setting(chat_id, "learning_location_keys", normalize_learning_locations(locations))


def resolve_learning_location(chat_id, location_key):
    if location_key == "home":
        return get_home_location_for_chat(chat_id)

    return get_location_by_key(location_key)


def build_learning_locations_text(location_keys):
    if not location_keys:
        return "не выбраны"

    lines = []

    for location_key in location_keys:
        location = FAVORITE_LOCATIONS.get(location_key)

        if location:
            lines.append(f"• {location_key} — {location['name']}")

    return "\n".join(lines) if lines else "не выбраны"


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


def save_forecast_history(location_name, forecast_data, chat_id=None):
    history = load_json_file(HISTORY_FILE, [])
    item = {
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "location": location_name,
        "forecast": forecast_data,
    }

    if chat_id is not None:
        item["chat_id"] = str(chat_id)

    history.append(item)
    save_json_file(HISTORY_FILE, history)


def load_history():
    return load_json_file(HISTORY_FILE, [])


def find_last_history_item(chat_id=None, location=None):
    history_items = load_history()

    for item in reversed(history_items):
        if chat_id is not None and item.get("chat_id") != str(chat_id):
            continue

        if location is not None and item.get("location") != location.get("name"):
            continue

        return item

    if chat_id is None and location is None:
        return history_items[-1] if history_items else None

    return None


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


def get_week_parts_sources(location, days=7):
    lat = location["latitude"]
    lon = location["longitude"]

    data = requests.get(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}"
        f"&longitude={lon}"
        "&hourly=temperature_2m,precipitation_probability,wind_speed_10m"
        f"&forecast_days={days}"
        "&timezone=auto",
        timeout=20,
    ).json()

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    rain_probs = hourly.get("precipitation_probability", [])
    winds = hourly.get("wind_speed_10m", [])

    dates = []

    for time_str in times:
        date_str = datetime.fromisoformat(time_str).strftime("%Y-%m-%d")

        if date_str not in dates:
            dates.append(date_str)

        if len(dates) >= days:
            break

    week_items = []

    for date_str in dates:
        parts = {}

        for part_key, part in DAY_PARTS.items():
            part_temps = []
            part_rains = []
            part_winds = []
            start_hour = part["start_hour"]
            end_hour = part["end_hour"]

            for idx, time_str in enumerate(times):
                dt = datetime.fromisoformat(time_str)
                hour = dt.hour
                item_date = dt.strftime("%Y-%m-%d")
                adjusted_hour = hour

                if part_key == "night" and hour < 6:
                    previous_date = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
                    item_date = previous_date
                    adjusted_hour = hour + 24

                if item_date != date_str:
                    continue

                if start_hour <= adjusted_hour < end_hour:
                    if idx < len(temps):
                        part_temps.append(temps[idx])
                    if idx < len(rain_probs):
                        part_rains.append(rain_probs[idx])
                    if idx < len(winds):
                        part_winds.append(winds[idx])

            parts[part_key] = {
                "title": part["title"],
                "temp": simple_average(part_temps),
                "rain": round(max(part_rains), 1) if part_rains else 0,
                "wind": round(max(part_winds), 1) if part_winds else 0,
            }

        day_rain = max(part["rain"] for part in parts.values())
        day_wind = max(part["wind"] for part in parts.values())
        day_temps = [part["temp"] for part in parts.values() if part["temp"] is not None]
        day_temp = simple_average(day_temps)

        week_items.append({
            "date": date_str,
            "weekday": datetime.fromisoformat(date_str).strftime("%a"),
            "parts": parts,
            "temp": day_temp,
            "rain": day_rain,
            "wind": day_wind,
        })

    return week_items


def get_rain_alert_status(location, hours=3):
    lat = location["latitude"]
    lon = location["longitude"]

    data = requests.get(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}"
        f"&longitude={lon}"
        "&current=precipitation,rain,showers,weather_code"
        "&hourly=precipitation_probability,precipitation,weather_code"
        "&forecast_days=1"
        "&timezone=auto",
        timeout=20,
    ).json()

    current = data.get("current", {})
    current_precip = current.get("precipitation", 0) or current.get("rain", 0) or current.get("showers", 0) or 0
    current_code = current.get("weather_code")

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    probs = hourly.get("precipitation_probability", [])
    precips = hourly.get("precipitation", [])
    codes = hourly.get("weather_code", [])

    now = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).replace(tzinfo=None)
    next_items = []

    for idx, time_str in enumerate(times):
        dt = datetime.fromisoformat(time_str)

        if dt < now:
            continue

        if len(next_items) >= hours:
            break

        next_items.append({
            "time": dt.strftime("%H:%M"),
            "probability": probs[idx] if idx < len(probs) else 0,
            "precipitation": precips[idx] if idx < len(precips) else 0,
            "code": codes[idx] if idx < len(codes) else None,
        })

    max_probability = max((item["probability"] for item in next_items), default=0)
    max_precipitation = max((item["precipitation"] for item in next_items), default=0)
    rain_codes = {51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99}
    current_is_rain = current_precip > 0 or current_code in rain_codes
    forecast_is_rain = max_probability >= 50 or max_precipitation >= 0.5 or any(item["code"] in rain_codes for item in next_items)
    should_alert = current_is_rain or forecast_is_rain

    signature = f"{datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).strftime('%Y-%m-%d-%H')}:{round(max_probability)}:{round(max_precipitation, 1)}:{round(current_precip, 1)}"

    return {
        "should_alert": should_alert,
        "current_precip": current_precip,
        "current_code": current_code,
        "max_probability": max_probability,
        "max_precipitation": max_precipitation,
        "next_items": next_items,
        "signature": signature,
    }


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


def format_source_rows(c):
    source_titles = {
        "openmeteo": "Open-Meteo",
        "weatherapi": "WeatherAPI",
        "visualcrossing": "Visual Crossing",
        "yr": "yr.no",
        "meteosource": "Meteosource",
    }
    rows = []

    for source, title in source_titles.items():
        rows.append(
            f"• {title}: "
            f"{c['temp_values'].get(source)}°C, "
            f"дождь {c['rain_values'].get(source)}, "
            f"ветер {c['wind_values'].get(source)} км/ч"
        )

    return "\n".join(rows)


def rain_advice(avg_rain):
    if avg_rain >= 70:
        return "зонт/дождевик точно лучше взять"
    if avg_rain >= 45:
        return "зонт лучше взять"
    if avg_rain >= 25:
        return "зонт по ситуации, риск есть"
    return "зонт скорее не нужен"


def part_status(temp, rain, wind):
    if rain >= 70 or wind >= 30:
        return "🔴 рискованно"
    if rain >= 45 or wind >= 20:
        return "🟠 осторожно"
    if rain >= 25 or wind >= 15:
        return "🟡 нормально, но следить"
    return "🟢 комфортно"


def format_score(score):
    if score >= 75:
        return f"🟢 {score}/100"
    if score >= 55:
        return f"🟡 {score}/100"
    if score >= 35:
        return f"🟠 {score}/100"
    return f"🔴 {score}/100"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌦 AI Weather Assistant\n\n"
        "☀️ Каждый день\n"
        "/weather — погода сейчас\n"
        "/home — погода по домашней локации\n"
        "/today_parts — сегодня по частям дня\n"
        "/morning_now — утренний брифинг сейчас\n\n"

        "📅 Планирование\n"
        "/tomorrow — завтра с лучшим окном\n"
        "/tomorrow_parts — завтра по частям дня\n"
        "/week — неделя по частям дня\n"
        "/weekend — ближайшие выходные\n\n"

        "🏕 Outdoor\n"
        "/trip — поездка\n"
        "/camping — палатка\n"
        "/kayak — байдарка\n\n"

        "📍 Локации и профиль\n"
        "/locations — все избранные места\n"
        "/set_home — выбрать home\n"
        "/profile — настроить рекомендации\n\n"

        "🔔 Уведомления\n"
        "/morning_on — включить утренний брифинг\n"
        "/morning_time 08:30 — задать время\n"
        "/morning_off — выключить брифинг\n"
        "/rain_alert_on — включить алерты дождя\n"
        "/rain_alert_now — проверить дождь сейчас\n"
        "/rain_alert_off — выключить алерты\n\n"

        "🧪 Обучение моделей\n"
        "/learning_on — включить обучение\n"
        "/learning_add kalyazin — добавить локацию\n"
        "/learning_locations — локации обучения\n"
        "/learning_status — статус обучения\n"
        "/learning_report — отчет по learning\n\n"

        "🛠 Сервис\n"
        "/status — состояние бота\n"
        "/scores — точность температуры\n"
        "/rain_scores — точность дождя\n"
        "/adaptive — веса моделей\n"
        "/export_all — backup данных\n"
        "/export_learning_csv — CSV для таблицы\n\n"

        "Быстрые сценарии:\n"
        "1. Дом: /set_home kalyazin\n"
        "2. Утро: /morning_time 08:30 → /morning_on\n"
        "3. Learning: /learning_on → /learning_add moscow_ilya"
    )


BOT_COMMANDS = [
    BotCommand("start", "помощь и список команд"),
    BotCommand("status", "статус бота, API, профиля и learning"),
    BotCommand("weather", "текущая погода для home или города"),
    BotCommand("home", "погода по твоей домашней локации"),
    BotCommand("today_parts", "сегодня по частям дня"),
    BotCommand("tomorrow", "подробный прогноз на завтра"),
    BotCommand("week", "прогноз на неделю по частям дня"),
    BotCommand("weekend", "прогноз на ближайшие выходные"),
    BotCommand("trip", "оценка погоды для поездки"),
    BotCommand("camping", "оценка погоды для палатки"),
    BotCommand("kayak", "оценка погоды для байдарки"),
    BotCommand("locations", "список избранных локаций"),
    BotCommand("set_home", "выбрать домашнюю локацию"),
    BotCommand("profile", "персональные настройки рекомендаций"),
    BotCommand("morning_on", "включить утренний брифинг"),
    BotCommand("morning_time", "задать время утреннего брифинга"),
    BotCommand("morning_now", "получить утренний брифинг сейчас"),
    BotCommand("rain_alert_on", "включить дождевые алерты"),
    BotCommand("rain_alert_now", "проверить дождь сейчас"),
    BotCommand("learning_on", "включить авто-обучение"),
    BotCommand("learning_status", "статус авто-обучения"),
    BotCommand("learning_add", "добавить локацию в авто-обучение"),
    BotCommand("learning_locations", "локации авто-обучения"),
    BotCommand("learning_now", "сохранить learning-прогноз сейчас"),
    BotCommand("learning_report", "короткий отчет по learning"),
    BotCommand("export_all", "выгрузить резервную копию данных"),
    BotCommand("export_learning_csv", "выгрузить learning в CSV"),
]


async def setup_bot_commands(app):
    await app.bot.set_my_commands(BOT_COMMANDS)
    print("Telegram command menu updated")


async def weather(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    location = get_location(context, chat_id=chat_id)

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

{build_profile_instructions(chat_id)}
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

    save_forecast_history(location["name"], forecast_history, chat_id=chat_id)

    message = (
        f"📍 {location['name']}, {location['country']}\n\n"
        f"🤖 Коротко:\n"
        f"{ai_summary}\n\n"

        f"🧠 Итог по 5 источникам:\n"
        f"🌡 Температура: ~{c['avg_temp']}°C\n"
        f"💨 Ветер: ~{c['avg_wind']} км/ч\n"
        f"☔ Дождь / rain score: ~{c['avg_rain']} — {rain_advice(c['avg_rain'])}\n"
        f"✅ Надежность: температура — {c['temp_confidence']}, дождь — {c['rain_confidence']}\n\n"

        f"📊 Разброс моделей:\n"
        f"Температура: {c['temp_spread']}°C\n"
        f"Дождь: {c['rain_spread']}\n"
        f"Весовой режим: {c['weights_mode']}\n\n"

        f"🔎 Источники:\n"
        f"{format_source_rows(c)}"
    )

    await update.message.reply_text(message)


async def today_parts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    location = get_location(context, chat_id=chat_id)

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

{build_profile_instructions(chat_id)}
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
    chat_id = update.effective_chat.id
    location = get_location(context, chat_id=chat_id)

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

{build_profile_instructions(chat_id)}
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


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    location = get_location(context, chat_id=chat_id)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    try:
        week_items = get_week_parts_sources(location)
    except Exception as e:
        await update.message.reply_text(f"Ошибка получения прогноза на неделю: {e}")
        return

    ai_prompt = f"""
Локация:
{location['name']}

Прогноз на неделю по частям дня:
{week_items}

Дай короткий практичный вывод:
- какие дни лучшие
- где выше риск дождя
- когда лучше планировать поездки/прогулки
- что важно по ветру

{build_profile_instructions(chat_id)}
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"📆 Прогноз на неделю\n"
        f"📍 {location['name']}, {location['country']}\n\n"
        f"🤖 Коротко:\n"
        f"{ai_summary}\n\n"
        f"🗓 По дням:\n"
    )

    for item in week_items:
        message += (
            f"📅 {item['date']} ({item['weekday']})\n"
            f"Итого: 🌡 ~{item['temp']}°C, ☔ до ~{item['rain']}%, 💨 до ~{item['wind']} км/ч "
            f"{part_status(item['temp'], item['rain'], item['wind'])}\n"
        )

        for key in ["morning", "day", "evening", "night"]:
            part = item["parts"][key]
            message += (
                f"{part['title']}: "
                f"~{part['temp']}°C, "
                f"☔ ~{part['rain']}%, "
                f"💨 ~{part['wind']} км/ч\n"
            )

        message += "\n"

    message += "Источник: Open-Meteo hourly"

    await update.message.reply_text(message)


def build_morning_briefing(location, chat_id=None):
    current = get_current_sources(location)
    c = build_consensus(location, current)
    parts = get_hourly_parts_sources(location)

    ai_prompt = f"""
Локация:
{location['name']}

Утренний погодный брифинг.

Сейчас:
Температура {c['avg_temp']}
Ветер {c['avg_wind']}
Осадки {c['avg_rain']}
Уверенность температуры: {c['temp_confidence']}
Уверенность осадков: {c['rain_confidence']}

Сегодня по частям дня:
{parts}

Дай короткий практичный вывод:
- как одеться утром и днем
- брать ли зонт
- когда лучше выходить
- есть ли риск дождя вечером
- стоит ли планировать прогулку или дела на улице

{build_profile_instructions(chat_id) if chat_id is not None else "Профиль пользователя: не настроен."}
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"🌅 Утренний брифинг\n"
        f"📍 {location['name']}, {location['country']}\n\n"

        f"🤖 Коротко:\n"
        f"{ai_summary}\n\n"

        f"📌 Сейчас:\n"
        f"🌡 ~{c['avg_temp']}°C, "
        f"💨 ~{c['avg_wind']} км/ч, "
        f"☔ ~{c['avg_rain']} — {rain_advice(c['avg_rain'])}\n"
        f"✅ Надежность: температура — {c['temp_confidence']}, дождь — {c['rain_confidence']}\n\n"

        f"🕒 Сегодня по частям дня:\n"
    )

    for key in ["morning", "day", "evening", "night"]:
        part = parts.get(key, {})
        temp = part.get("temp")
        rain = part.get("rain")
        wind = part.get("wind")
        message += (
            f"{part.get('title', key)}: "
            f"🌡 ~{temp}°C, "
            f"☔ ~{rain}%, "
            f"💨 до ~{wind} км/ч — {part_status(temp, rain, wind)}\n"
        )

    message += (
        f"\n📊 Режим весов: {c['weights_mode']}"
    )

    return message


def build_learning_forecast(chat_id, location_key):
    location = resolve_learning_location(chat_id, location_key)
    current = get_current_sources(location)
    c = build_consensus(location, current)
    now = datetime.now(ZoneInfo(DEFAULT_TIMEZONE))

    forecast = {
        "id": f"{chat_id}-{location_key}-{now.strftime('%Y%m%d%H%M%S')}",
        "chat_id": str(chat_id),
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
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
    }

    return forecast


def save_learning_forecast(chat_id, location_key="home"):
    forecasts = load_learning_forecasts()
    today = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).strftime("%Y-%m-%d")

    for item in forecasts:
        if (
            str(item.get("chat_id")) == str(chat_id)
            and item.get("date") == today
            and item.get("location_key") == location_key
            and not item.get("manual")
        ):
            return item, False

    forecast = build_learning_forecast(chat_id, location_key)
    forecasts.append(forecast)
    save_learning_forecasts(forecasts)

    return forecast, True


def verify_learning_forecast(item):
    location_key = item.get("location_key", "home")
    location = resolve_learning_location(item.get("chat_id"), location_key)

    if not location:
        return None

    current = get_current_sources(location)
    current_temp_values = current["temperatures"]
    factual_temp = round(sum(current_temp_values.values()) / len(current_temp_values), 1)

    current_rain_values = current["rain"]
    factual_rain_score = round(sum(current_rain_values.values()) / len(current_rain_values), 1)

    saved_forecast = item["forecast"]
    saved_temps = saved_forecast["temperatures"]
    saved_rain = saved_forecast.get("rain", {})

    temperature_errors = {
        source: round(abs(predicted_temp - factual_temp), 1)
        for source, predicted_temp in saved_temps.items()
        if source != "consensus"
    }
    consensus_error = round(abs(saved_temps["consensus"] - factual_temp), 1)
    best_temperature_model = update_model_scores(temperature_errors, consensus_error)

    rain_predictions = {}
    rain_errors = {}

    for source, predicted_rain in saved_rain.items():
        rain_predictions[source] = predicted_rain
        rain_errors[source] = round(abs(predicted_rain - factual_rain_score), 1)

    update_rain_scores(rain_predictions, factual_rain_score)

    item["verified"] = True
    item["verified_at"] = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
    item["temperature_errors"] = temperature_errors
    item["rain_errors"] = rain_errors
    item["temperature_consensus_error"] = consensus_error
    item["best_temperature_model"] = best_temperature_model
    item["factual_temperature"] = factual_temp
    item["factual_rain_score"] = factual_rain_score

    return item


def verify_pending_learning_forecasts():
    forecasts = load_learning_forecasts()
    verified_items = []

    for item in forecasts:
        if item.get("verified"):
            continue

        try:
            verified_item = verify_learning_forecast(item)

            if verified_item:
                verified_items.append(verified_item)
        except Exception as e:
            print("Learning verify error:", e)

    if verified_items:
        save_learning_forecasts(forecasts)

    return verified_items


async def tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    location = get_location(context, chat_id=chat_id)

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

{build_profile_instructions(chat_id)}
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"📅 Прогноз на завтра\n"
        f"📍 {location['name']}, {location['country']}\n\n"

        f"🤖 Коротко:\n"
        f"{ai_summary}\n\n"

        f"🧠 Итог по 5 источникам:\n"
        f"🌡 ~{c['avg_temp']}°C\n"
        f"💨 ~{c['avg_wind']} км/ч\n"
        f"☔ ~{c['avg_rain']}% — {rain_advice(c['avg_rain'])}\n"
        f"✅ Надежность: температура — {c['temp_confidence']}, дождь — {c['rain_confidence']}\n\n"
    )

    if best_part:
        message += (
            f"🏆 Лучшее окно: {best_part.get('title')}\n"
            f"🌡 ~{best_part.get('temp')}°C, "
            f"☔ ~{best_part.get('rain')}%, "
            f"💨 ~{best_part.get('wind')} км/ч\n\n"
        )

    message += "🕒 Завтра по частям дня:\n"

    for key in ["morning", "day", "evening", "night"]:
        part = parts.get(key, {})
        title = part.get("title", key)
        temp = part.get("temp", 0)
        rain = part.get("rain", 0)
        wind = part.get("wind", 0)

        status = part_status(temp, rain, wind)

        message += (
            f"{title}: "
            f"🌡 ~{temp}°C, "
            f"☔ ~{rain}%, "
            f"💨 до ~{wind} км/ч — {status}\n"
        )

    message += (
        f"\n📊 Разброс моделей:\n"
        f"Температура: {c['temp_spread']}°C\n"
        f"Дождь: {c['rain_spread']}%\n"
        f"Весовой режим: {c['weights_mode']}"
    )

    await update.message.reply_text(message)

async def weekend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    location = get_location(context, chat_id=chat_id)

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

{build_profile_instructions(chat_id)}
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


async def trip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    location = get_location(context, chat_id=chat_id)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    try:
        week_items = get_week_parts_sources(location)
    except Exception as e:
        await update.message.reply_text(f"Ошибка анализа поездки: {e}")
        return

    scored_days = []

    for item in week_items:
        score = calculate_trip_score(item["temp"], item["rain"], item["wind"])
        scored_days.append({**item, "score": score, "recommendation": trip_recommendation_from_score(score)})

    best_day = max(scored_days, key=lambda item: item["score"])
    worst_day = min(scored_days, key=lambda item: item["score"])

    ai_prompt = f"""
Локация:
{location['name']}

Оценка погоды для поездки на неделю:
{scored_days}

Лучший день:
{best_day}

Худший день:
{worst_day}

Дай короткий вывод:
- стоит ли планировать поездку
- какой день лучше
- какие риски по дождю и ветру
- нужен ли запасной план

{build_profile_instructions(chat_id)}
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"🚗 Trip mode\n"
        f"📍 {location['name']}, {location['country']}\n\n"
        f"🤖 Коротко:\n"
        f"{ai_summary}\n\n"
        f"🏆 Лучший день: {best_day['date']} ({best_day['weekday']}) — {format_score(best_day['score'])}\n"
        f"🌡 ~{best_day['temp']}°C, ☔ ~{best_day['rain']}%, 💨 ~{best_day['wind']} км/ч\n"
        f"{best_day['recommendation']}\n\n"
        f"⚠️ Худший день: {worst_day['date']} ({worst_day['weekday']}) — {format_score(worst_day['score'])}\n"
        f"🌡 ~{worst_day['temp']}°C, ☔ ~{worst_day['rain']}%, 💨 ~{worst_day['wind']} км/ч\n"
        f"{worst_day['recommendation']}\n\n"
        f"📆 Неделя:\n"
    )

    for item in scored_days:
        message += (
            f"• {item['date']} ({item['weekday']}): {format_score(item['score'])}, "
            f"☔ {item['rain']}%, 💨 {item['wind']} км/ч\n"
        )

    await update.message.reply_text(message)


async def camping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    location = get_location(context, chat_id=chat_id)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    try:
        week_items = get_week_parts_sources(location)
    except Exception as e:
        await update.message.reply_text(f"Ошибка анализа палатки: {e}")
        return

    scored_days = []

    for item in week_items:
        day_part = item["parts"]["day"]
        night_part = item["parts"]["night"]
        day_score = calculate_camping_score(day_part["temp"], item["rain"], item["wind"])
        night_score = calculate_camping_score(night_part["temp"], night_part["rain"], night_part["wind"], night=True)
        score = round((day_score + night_score) / 2, 1)
        recommendation = camping_recommendation(score, item["rain"], item["wind"], night_part["temp"])
        scored_days.append({
            **item,
            "score": score,
            "day_temp": day_part["temp"],
            "night_temp": night_part["temp"],
            "recommendation": recommendation,
        })

    best_day = max(scored_days, key=lambda item: item["score"])
    worst_day = min(scored_days, key=lambda item: item["score"])

    ai_prompt = f"""
Локация:
{location['name']}

Оценка погоды для палатки:
{scored_days}

Лучший день:
{best_day}

Худший день:
{worst_day}

Дай короткий вывод:
- стоит ли ехать с палаткой
- какая ночь комфортнее
- риск дождя ночью
- риск ветра
- что взять с собой

{build_profile_instructions(chat_id)}
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"🏕 Camping mode\n"
        f"📍 {location['name']}, {location['country']}\n\n"
        f"🤖 Коротко:\n"
        f"{ai_summary}\n\n"
        f"🏆 Лучший день: {best_day['date']} ({best_day['weekday']}) — {format_score(best_day['score'])}\n"
        f"🌡 День ~{best_day['day_temp']}°C / Ночь ~{best_day['night_temp']}°C\n"
        f"☔ ~{best_day['rain']}%, 💨 ~{best_day['wind']} км/ч\n"
        f"{best_day['recommendation']}\n\n"
        f"⚠️ Худший день: {worst_day['date']} ({worst_day['weekday']}) — {format_score(worst_day['score'])}\n"
        f"🌡 День ~{worst_day['day_temp']}°C / Ночь ~{worst_day['night_temp']}°C\n"
        f"☔ ~{worst_day['rain']}%, 💨 ~{worst_day['wind']} км/ч\n"
        f"{worst_day['recommendation']}\n\n"
        f"📆 Неделя:\n"
    )

    for item in scored_days:
        message += (
            f"• {item['date']} ({item['weekday']}): {format_score(item['score'])}, "
            f"ночь ~{item['night_temp']}°C, ☔ {item['rain']}%, 💨 {item['wind']} км/ч\n"
        )

    await update.message.reply_text(message)


async def kayak(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    location = get_location(context, chat_id=chat_id)

    if not location:
        await update.message.reply_text("Локация не найдена 😢")
        return

    try:
        today_parts_data = get_hourly_parts_sources(location)
        tomorrow_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        tomorrow_parts_data = get_hourly_parts_sources(location, tomorrow_date)
    except Exception as e:
        await update.message.reply_text(f"Ошибка анализа байдарки: {e}")
        return

    windows = []

    for day_label, date_label, parts_data in [
        ("Сегодня", datetime.now().strftime("%Y-%m-%d"), today_parts_data),
        ("Завтра", tomorrow_date, tomorrow_parts_data),
    ]:
        for key in ["morning", "day", "evening"]:
            part = parts_data.get(key, {})
            score = calculate_baidarka_score(part.get("temp", 0), part.get("rain", 0), part.get("wind", 0))
            recommendation = baidarka_recommendation(score, part.get("wind", 0), part.get("rain", 0))
            windows.append({
                "day_label": day_label,
                "date": date_label,
                "part_title": part.get("title", key),
                "temp": part.get("temp", 0),
                "rain": part.get("rain", 0),
                "wind": part.get("wind", 0),
                "score": score,
                "recommendation": recommendation,
            })

    best_window = max(windows, key=lambda item: item["score"])
    worst_window = min(windows, key=lambda item: item["score"])

    ai_prompt = f"""
Локация:
{location['name']}

Оценка окон для байдарки:
{windows}

Лучшее окно:
{best_window}

Худшее окно:
{worst_window}

Дай короткий вывод:
- можно ли выходить на воду
- когда лучшее окно
- главные риски по ветру/дождю
- когда лучше отказаться

{build_profile_instructions(chat_id)}
"""

    ai_summary = get_ai_summary(ai_prompt)

    message = (
        f"🚣 Kayak mode\n"
        f"📍 {location['name']}, {location['country']}\n\n"
        f"🤖 Коротко:\n"
        f"{ai_summary}\n\n"
        f"🏆 Лучшее окно: {best_window['day_label']} {best_window['date']} — {best_window['part_title']}\n"
        f"{format_score(best_window['score'])}: 🌡 ~{best_window['temp']}°C, "
        f"☔ ~{best_window['rain']}%, 💨 ~{best_window['wind']} км/ч\n"
        f"{best_window['recommendation']}\n\n"
        f"⚠️ Худшее окно: {worst_window['day_label']} {worst_window['date']} — {worst_window['part_title']}\n"
        f"{format_score(worst_window['score'])}: 🌡 ~{worst_window['temp']}°C, "
        f"☔ ~{worst_window['rain']}%, 💨 ~{worst_window['wind']} км/ч\n\n"
        f"🕒 Окна:\n"
    )

    for item in sorted(windows, key=lambda row: row["score"], reverse=True):
        message += (
            f"• {item['day_label']} {item['part_title']}: {format_score(item['score'])}, "
            f"☔ {item['rain']}%, 💨 {item['wind']} км/ч\n"
        )

    await update.message.reply_text(message)


async def verify_rain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    requested_location = get_location(context, chat_id=chat_id)
    last_item = find_last_history_item(chat_id=chat_id, location=requested_location)

    if not last_item:
        last_item = find_last_history_item(location=requested_location)

    if not last_item:
        await update.message.reply_text(
            f"Нет сохранённого прогноза для {requested_location['name']}.\n"
            f"Сначала вызови /weather"
        )
        return

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
    chat_id = update.effective_chat.id
    requested_location = get_location(context, chat_id=chat_id)
    last_item = find_last_history_item(chat_id=chat_id, location=requested_location)

    if not last_item:
        last_item = find_last_history_item(location=requested_location)

    if not last_item:
        await update.message.reply_text(
            f"Нет сохранённого прогноза для {requested_location['name']}.\n"
            f"Сначала вызови /weather"
        )
        return

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
    chat_id = update.effective_chat.id
    user_settings = get_user_settings(chat_id)
    home_key = user_settings.get("home_location_key", "home")
    home_location = get_location_by_key(home_key)
    profiles = get_user_profiles(chat_id)

    history_items = load_history()
    scores_data = load_scores()
    rain_scores_data = load_rain_scores()
    adaptive_weights = get_adaptive_weights_from_scores()
    morning_subscribers_count = count_json_items(MORNING_SUBSCRIBERS_FILE)
    danger_subscribers_count = count_json_items(DANGER_SUBSCRIBERS_FILE)
    rain_alert_subscribers_count = count_json_items(RAIN_ALERT_SUBSCRIBERS_FILE)
    learning_forecasts_count = count_json_items(LEARNING_FILE)

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
    api_statuses = {
        "Telegram": bool(TOKEN),
        "WeatherAPI": bool(WEATHERAPI_KEY),
        "Visual Crossing": bool(VISUALCROSSING_API_KEY),
        "Meteosource": bool(METEOSOURCE_API_KEY),
        "OpenAI": bool(OPENAI_API_KEY),
    }
    api_text = "\n".join(
        f"{'✅' if is_enabled else '⚠️'} {name}"
        for name, is_enabled in api_statuses.items()
    )

    if adaptive_weights:
        weights_text = "\n".join(
            [f"• {model}: {round(weight * 100)}%" for model, weight in adaptive_weights.items()]
        )
        weights_mode = "adaptive"
    else:
        weights_text = "Пока используются региональные веса."
        weights_mode = "regional"

    message = (
        f"🧠 Статус AI Weather Assistant\n\n"

        f"☁️ Режим работы:\n"
        f"✅ Cloud / Render\n"
        f"✅ Telegram polling active\n"
        f"✅ Python locked: 3.11.11\n"
        f"💾 DATA_DIR: {DATA_DIR}\n\n"

        f"🏠 Твой home:\n"
        f"{home_location['name']} ({home_key})\n\n"

        f"👤 Профиль:\n"
        f"{build_profile_text(profiles)}\n\n"

        f"📍 Локации:\n"
        f"Всего: {len(FAVORITE_LOCATIONS)}\n"
        f"{locations_list}\n\n"

        f"🔑 Подключения:\n"
        f"{api_text}\n\n"

        f"🔔 Подписки и автоматика:\n"
        f"🌅 Morning subscribers: {morning_subscribers_count}\n"
        f"☔ Rain alert subscribers: {rain_alert_subscribers_count}\n"
        f"🚨 Danger subscribers: {danger_subscribers_count}\n"
        f"🧪 Learning forecasts: {learning_forecasts_count}\n\n"

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


async def export_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payload = build_export_payload()
    timestamp = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).strftime("%Y%m%d_%H%M%S")
    filename = f"weather_bot_backup_{timestamp}.json"
    content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    backup_file = BytesIO(content)
    backup_file.name = filename

    await update.message.reply_document(
        document=backup_file,
        filename=filename,
        caption=(
            "💾 Резервная копия данных бота.\n"
            "Сохрани файл в Telegram или скачай на компьютер."
        ),
    )


async def export_learning_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    csv_text = build_learning_csv()
    timestamp = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).strftime("%Y%m%d_%H%M%S")
    filename = f"weather_bot_learning_{timestamp}.csv"
    csv_file = BytesIO(csv_text.encode("utf-8-sig"))
    csv_file.name = filename

    await update.message.reply_document(
        document=csv_file,
        filename=filename,
        caption=(
            "📊 Learning export в CSV.\n"
            "Можно открыть в Excel, Numbers или загрузить в Google Sheets."
        ),
    )


async def learning_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_learning_report_text())


async def favorite_current(update: Update, context: ContextTypes.DEFAULT_TYPE, key: str):
    context.args = [key]
    await weather(update, context)


async def favorite_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.args = []
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
    user_settings = get_user_settings(chat_id)
    current_key = user_settings.get("home_location_key", "home")

    if not context.args:
        current_location = get_location_by_key(current_key) or FAVORITE_LOCATIONS["home"]
        available_locations = build_location_options(current_key)

        await update.message.reply_text(
            f"🏠 Текущий home: {current_location['name']} ({current_key})\n\n"
            f"Выбери новую домашнюю локацию:\n"
            f"{available_locations}\n\n"
            f"Это меняет /home и /weather без аргументов только для тебя."
        )
        return

    location_key = context.args[0].lower()

    if location_key not in FAVORITE_LOCATIONS:
        available_locations = build_location_options(current_key)

        await update.message.reply_text(
            f"Такой избранной локации нет 😢\n\n"
            f"Доступные варианты:\n"
            f"{available_locations}"
        )
        return

    update_user_setting(chat_id, "home_location_key", location_key)
    location = FAVORITE_LOCATIONS[location_key]

    await update.message.reply_text(
        f"✅ Домашняя локация обновлена только для тебя.\n\n"
        f"🏠 Теперь твой home: {location['name']}, {location['country']}\n\n"
        f"Проверить: /home или /weather"
    )


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    profiles = get_user_profiles(chat_id)

    if not context.args:
        options_text = "\n".join(
            f"/profile {key} — {'выключить' if key in profiles else 'включить'}: {data['title']}"
            for key, data in PROFILE_OPTIONS.items()
        )

        await update.message.reply_text(
            f"👤 Твой профиль погоды\n\n"
            f"Сейчас: {build_profile_text(profiles)}\n\n"
            f"{options_text}\n"
            f"/profile reset — сбросить профиль"
        )
        return

    option = context.args[0].lower()

    if option == "reset":
        save_user_profiles(chat_id, [])
        await update.message.reply_text("✅ Профиль сброшен.")
        return

    if option not in PROFILE_OPTIONS:
        available = ", ".join(PROFILE_OPTIONS.keys())

        await update.message.reply_text(
            f"Такого профиля нет 😢\n\n"
            f"Доступные варианты: {available}\n"
            f"Например: /profile cold"
        )
        return

    if option in profiles:
        profiles.remove(option)
        action = "выключен"
    else:
        profiles.append(option)
        action = "включён"

    save_user_profiles(chat_id, profiles)

    await update.message.reply_text(
        f"✅ Профиль «{PROFILE_OPTIONS[option]['title']}» {action}.\n\n"
        f"Сейчас: {build_profile_text(profiles)}"
    )


async def morning_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    location_key = context.args[0].lower() if context.args else "home"

    if location_key not in FAVORITE_LOCATIONS:
        available_locations = build_location_options(command_prefix="/morning_on")

        await update.message.reply_text(
            f"Такой локации нет 😢\n\n"
            f"Доступные варианты:\n"
            f"{available_locations}"
        )
        return

    user_settings = get_user_settings(chat_id)
    morning_time = user_settings.get("morning_time", "08:00")
    subscribers = load_morning_subscribers()
    subscriber = find_morning_subscriber(subscribers, chat_id)

    if subscriber:
        subscriber["location_key"] = location_key
        subscriber["morning_time"] = morning_time
    else:
        subscribers.append({
            "chat_id": str(chat_id),
            "location_key": location_key,
            "morning_time": morning_time,
            "created_at": datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S"),
            "last_sent_date": "",
        })

    save_morning_subscribers(subscribers)

    location_text = "твой home" if location_key == "home" else FAVORITE_LOCATIONS[location_key]["name"]

    await update.message.reply_text(
        f"✅ Утренний брифинг включён.\n\n"
        f"📍 Локация: {location_text}\n"
        f"🕗 Время: {morning_time} по Москве\n\n"
        f"Изменить время: /morning_time 08:30\n"
        f"Проверить сейчас: /morning_now"
    )


async def morning_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers = load_morning_subscribers()
    updated_subscribers = [
        subscriber
        for subscriber in subscribers
        if str(subscriber.get("chat_id")) != str(chat_id)
    ]

    save_morning_subscribers(updated_subscribers)

    await update.message.reply_text("✅ Утренний брифинг выключен.")


async def morning_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not context.args:
        user_settings = get_user_settings(chat_id)
        current_time = user_settings.get("morning_time", "08:00")

        await update.message.reply_text(
            f"🕗 Текущее время утреннего брифинга: {current_time} по Москве\n\n"
            f"Изменить:\n"
            f"/morning_time 08:30"
        )
        return

    new_time = context.args[0]

    if not is_valid_time(new_time):
        await update.message.reply_text(
            "Время нужно указать в формате HH:MM.\n\n"
            "Например:\n"
            "/morning_time 08:30"
        )
        return

    update_user_setting(chat_id, "morning_time", new_time)

    subscribers = load_morning_subscribers()
    subscriber = find_morning_subscriber(subscribers, chat_id)

    if subscriber:
        subscriber["morning_time"] = new_time
        save_morning_subscribers(subscribers)

    await update.message.reply_text(
        f"✅ Время утреннего брифинга обновлено: {new_time} по Москве."
    )


async def morning_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers = load_morning_subscribers()
    subscriber = find_morning_subscriber(subscribers, chat_id)

    if subscriber:
        location = resolve_subscriber_location(subscriber)
    else:
        location = get_home_location_for_chat(chat_id)

    try:
        message = build_morning_briefing(location, chat_id)
    except Exception as e:
        await update.message.reply_text(f"Не смог собрать утренний брифинг: {e}")
        return

    await update.message.reply_text(message)


async def check_morning_alerts(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    today = now.strftime("%Y-%m-%d")
    current_minutes = now.hour * 60 + now.minute
    subscribers = load_morning_subscribers()
    changed = False

    for subscriber in subscribers:
        try:
            morning_time_value = subscriber.get("morning_time", "08:00")

            if not is_valid_time(morning_time_value):
                morning_time_value = "08:00"

            target_time = datetime.strptime(morning_time_value, "%H:%M")
            target_minutes = target_time.hour * 60 + target_time.minute

            if subscriber.get("last_sent_date") == today:
                continue

            if not 0 <= current_minutes - target_minutes < 3:
                continue

            location = resolve_subscriber_location(subscriber)
            message = build_morning_briefing(location, subscriber.get("chat_id"))

            await context.bot.send_message(
                chat_id=subscriber["chat_id"],
                text=message,
            )

            subscriber["last_sent_date"] = today
            changed = True

        except Exception as e:
            print("Morning alert error:", e)

    if changed:
        save_morning_subscribers(subscribers)


async def rain_alert_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    location_key = context.args[0].lower() if context.args else "home"

    if location_key not in FAVORITE_LOCATIONS:
        available_locations = build_location_options(command_prefix="/rain_alert_on")

        await update.message.reply_text(
            f"Такой локации нет 😢\n\n"
            f"Доступные варианты:\n"
            f"{available_locations}"
        )
        return

    subscribers = load_rain_alert_subscribers()
    subscriber = find_rain_alert_subscriber(subscribers, chat_id)

    if subscriber:
        subscriber["location_key"] = location_key
    else:
        subscribers.append({
            "chat_id": str(chat_id),
            "location_key": location_key,
            "created_at": datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S"),
            "last_signature": "",
        })

    save_rain_alert_subscribers(subscribers)

    location_text = "твой home" if location_key == "home" else FAVORITE_LOCATIONS[location_key]["name"]

    await update.message.reply_text(
        f"✅ Дождевые алерты включены.\n\n"
        f"📍 Локация: {location_text}\n"
        f"Бот будет проверять дождь примерно раз в 15 минут.\n"
        f"Проверить сейчас: /rain_alert_now"
    )


async def rain_alert_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers = load_rain_alert_subscribers()
    updated_subscribers = [
        subscriber
        for subscriber in subscribers
        if str(subscriber.get("chat_id")) != str(chat_id)
    ]

    save_rain_alert_subscribers(updated_subscribers)

    await update.message.reply_text("✅ Дождевые алерты выключены.")


async def rain_alert_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribers = load_rain_alert_subscribers()
    subscriber = find_rain_alert_subscriber(subscribers, chat_id)

    if subscriber:
        location = resolve_subscriber_location(subscriber)
    else:
        location = get_home_location_for_chat(chat_id)

    try:
        status = get_rain_alert_status(location)
    except Exception as e:
        await update.message.reply_text(f"Не смог проверить дождь: {e}")
        return

    next_lines = "\n".join(
        f"• {item['time']}: вероятность {item['probability']}%, осадки {item['precipitation']} мм"
        for item in status["next_items"]
    )

    alert_text = "есть риск дождя / дождь уже идёт" if status["should_alert"] else "существенного риска дождя нет"

    await update.message.reply_text(
        f"☔ Проверка дождя\n"
        f"📍 {location['name']}\n\n"
        f"Сейчас осадки: {status['current_precip']} мм\n"
        f"Макс. вероятность в ближайшие часы: {status['max_probability']}%\n"
        f"Макс. осадки в ближайшие часы: {status['max_precipitation']} мм\n"
        f"Итог: {alert_text}\n\n"
        f"{next_lines}"
    )


async def check_rain_alerts(context: ContextTypes.DEFAULT_TYPE):
    subscribers = load_rain_alert_subscribers()
    changed = False

    for subscriber in subscribers:
        try:
            location = resolve_subscriber_location(subscriber)
            status = get_rain_alert_status(location)

            if not status["should_alert"]:
                continue

            if subscriber.get("last_signature") == status["signature"]:
                continue

            await context.bot.send_message(
                chat_id=subscriber["chat_id"],
                text=(
                    f"☔ Дождевой алерт\n"
                    f"📍 {location['name']}\n\n"
                    f"Сейчас осадки: {status['current_precip']} мм\n"
                    f"Макс. вероятность в ближайшие часы: {status['max_probability']}%\n"
                    f"Макс. осадки в ближайшие часы: {status['max_precipitation']} мм\n\n"
                    f"Лучше взять зонт/дождевик."
                ),
            )

            subscriber["last_signature"] = status["signature"]
            changed = True

        except Exception as e:
            print("Rain alert error:", e)

    if changed:
        save_rain_alert_subscribers(subscribers)


async def learning_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    location_key = context.args[0].lower() if context.args else "home"

    if location_key not in FAVORITE_LOCATIONS:
        available_locations = build_location_options(command_prefix="/learning_on")

        await update.message.reply_text(
            f"Такой локации нет 😢\n\n"
            f"Доступные варианты:\n"
            f"{available_locations}"
        )
        return

    update_user_setting(chat_id, "learning_enabled", True)
    update_user_setting(chat_id, "learning_location_key", location_key)
    save_learning_locations(chat_id, [location_key])

    location_text = "твой home" if location_key == "home" else FAVORITE_LOCATIONS[location_key]["name"]

    await update.message.reply_text(
        f"✅ Auto-learning включён.\n\n"
        f"📍 Локация: {location_text}\n"
        f"🌅 Утром бот сохранит прогноз.\n"
        f"🌙 Вечером бот сравнит его с фактом и обновит веса.\n\n"
        f"Добавить ещё место: /learning_add moscow_ilya\n"
        f"Список мест: /learning_locations\n"
        f"Тест сейчас: /learning_now"
    )


async def learning_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    update_user_setting(chat_id, "learning_enabled", False)

    await update.message.reply_text("✅ Auto-learning выключен.")


async def learning_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not context.args:
        available_locations = build_location_options(command_prefix="/learning_add")

        await update.message.reply_text(
            f"Какую локацию добавить в auto-learning?\n\n"
            f"{available_locations}"
        )
        return

    location_key = context.args[0].lower()

    if location_key not in FAVORITE_LOCATIONS:
        available_locations = build_location_options(command_prefix="/learning_add")

        await update.message.reply_text(
            f"Такой локации нет 😢\n\n"
            f"Доступные варианты:\n"
            f"{available_locations}"
        )
        return

    settings = get_user_settings(chat_id)
    locations = get_learning_locations(settings)

    if location_key not in locations:
        locations.append(location_key)

    save_learning_locations(chat_id, locations)
    update_user_setting(chat_id, "learning_enabled", True)

    await update.message.reply_text(
        f"✅ Локация добавлена в auto-learning.\n\n"
        f"Теперь бот обучается по:\n"
        f"{build_learning_locations_text(locations)}"
    )


async def learning_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    settings = get_user_settings(chat_id)
    locations = get_learning_locations(settings)

    if not context.args:
        await update.message.reply_text(
            f"Какую локацию убрать из auto-learning?\n\n"
            f"{build_learning_locations_text(locations)}"
        )
        return

    location_key = context.args[0].lower()

    if location_key not in locations:
        await update.message.reply_text(
            f"Этой локации нет в списке auto-learning.\n\n"
            f"Сейчас выбраны:\n"
            f"{build_learning_locations_text(locations)}"
        )
        return

    locations.remove(location_key)
    save_learning_locations(chat_id, locations)

    await update.message.reply_text(
        f"✅ Локация убрана из auto-learning.\n\n"
        f"Остались:\n"
        f"{build_learning_locations_text(locations)}"
    )


async def learning_locations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    settings = get_user_settings(chat_id)
    locations = get_learning_locations(settings)

    await update.message.reply_text(
        f"🧪 Локации auto-learning\n\n"
        f"{build_learning_locations_text(locations)}\n\n"
        f"Добавить: /learning_add kalyazin\n"
        f"Убрать: /learning_remove kalyazin"
    )


async def learning_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_settings = get_user_settings(chat_id)
    forecasts = load_learning_forecasts()

    user_forecasts = [
        item for item in forecasts
        if str(item.get("chat_id")) == str(chat_id)
    ]
    verified_count = sum(1 for item in user_forecasts if item.get("verified"))
    pending_count = len(user_forecasts) - verified_count

    is_enabled = user_settings.get("learning_enabled", False)
    learning_locations = get_learning_locations(user_settings)

    await update.message.reply_text(
        f"🧪 Auto-learning status\n\n"
        f"Режим: {'включён' if is_enabled else 'выключен'}\n"
        f"Локации:\n"
        f"{build_learning_locations_text(learning_locations)}\n\n"
        f"Всего learning-записей: {len(user_forecasts)}\n"
        f"Проверено: {verified_count}\n"
        f"Ждут проверки: {pending_count}\n\n"
        f"Включить: /learning_on\n"
        f"Добавить место: /learning_add kalyazin\n"
        f"Убрать место: /learning_remove kalyazin\n"
        f"Сохранить тест: /learning_now\n"
        f"Проверить тест: /learning_verify_now"
    )


async def learning_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_settings = get_user_settings(chat_id)
    learning_locations = get_learning_locations(user_settings)
    saved = []
    skipped = []

    for location_key in learning_locations:
        try:
            forecast, created = save_learning_forecast(chat_id, location_key)

            if created:
                saved.append(f"• {forecast['location']}")
            else:
                skipped.append(f"• {forecast['location']}")
        except Exception as e:
            skipped.append(f"• {location_key}: ошибка {e}")

    saved_text = "\n".join(saved) if saved else "нет новых"
    skipped_text = "\n".join(skipped) if skipped else "нет"

    await update.message.reply_text(
        f"🧪 Learning-прогнозы\n\n"
        f"Сохранено:\n{saved_text}\n\n"
        f"Уже было/ошибки:\n{skipped_text}\n\n"
        f"Проверить вручную: /learning_verify_now"
    )


async def learning_verify_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        verified_items = verify_pending_learning_forecasts()
    except Exception as e:
        await update.message.reply_text(f"Не смог проверить learning-прогнозы: {e}")
        return

    if not verified_items:
        await update.message.reply_text("Нет learning-прогнозов, которые ждут проверки.")
        return

    last_item = verified_items[-1]

    await update.message.reply_text(
        f"✅ Learning-проверка выполнена.\n\n"
        f"Проверено записей: {len(verified_items)}\n"
        f"Последняя локация: {last_item['location']}\n"
        f"Факт температура: ~{last_item['factual_temperature']}°C\n"
        f"Факт rain score: ~{last_item['factual_rain_score']}\n"
        f"Лучшая модель температуры: {last_item['best_temperature_model']}"
    )


async def check_learning_schedule(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    today = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    all_settings = load_all_user_settings()

    if current_time == "08:05":
        for chat_id, settings in all_settings.items():
            if not settings.get("learning_enabled"):
                continue

            if settings.get("last_learning_forecast_date") == today:
                continue

            saved_locations = []

            try:
                for location_key in get_learning_locations(settings):
                    forecast, created = save_learning_forecast(chat_id, location_key)

                    if created:
                        saved_locations.append(forecast["location"])

                settings["last_learning_forecast_date"] = today

                if saved_locations:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🧪 Learning: утренние прогнозы сохранены.\n\n"
                            f"📍 Локации: {', '.join(saved_locations)}\n"
                            f"Вечером бот проверит факт и обновит веса."
                        ),
                    )

            except Exception as e:
                print("Learning forecast error:", e)

        save_user_settings(all_settings)

    if current_time == "21:00":
        verified_items = verify_pending_learning_forecasts()

        for chat_id, settings in all_settings.items():
            if not settings.get("learning_enabled"):
                continue

            if settings.get("last_learning_verify_date") == today:
                continue

            user_verified_count = sum(
                1 for item in verified_items
                if str(item.get("chat_id")) == str(chat_id)
            )

            settings["last_learning_verify_date"] = today

            if user_verified_count:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🧪 Learning: вечерняя проверка завершена.\n\n"
                        f"Проверено прогнозов: {user_verified_count}\n"
                        f"Веса моделей обновлены."
                    ),
                )

        save_user_settings(all_settings)


def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    app = ApplicationBuilder().token(TOKEN).post_init(setup_bot_commands).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("weather", weather))
    app.add_handler(CommandHandler("today_parts", today_parts))
    app.add_handler(CommandHandler("tomorrow_parts", tomorrow_parts))
    app.add_handler(CommandHandler("tomorrow", tomorrow))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("trip", trip))
    app.add_handler(CommandHandler("camping", camping))
    app.add_handler(CommandHandler("kayak", kayak))
    app.add_handler(CommandHandler("set_home", set_home))
    app.add_handler(CommandHandler("locations", locations))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("morning_on", morning_on))
    app.add_handler(CommandHandler("morning_off", morning_off))
    app.add_handler(CommandHandler("morning_time", morning_time))
    app.add_handler(CommandHandler("morning_now", morning_now))
    app.add_handler(CommandHandler("rain_alert_on", rain_alert_on))
    app.add_handler(CommandHandler("rain_alert_off", rain_alert_off))
    app.add_handler(CommandHandler("rain_alert_now", rain_alert_now))
    app.add_handler(CommandHandler("learning_on", learning_on))
    app.add_handler(CommandHandler("learning_off", learning_off))
    app.add_handler(CommandHandler("learning_status", learning_status))
    app.add_handler(CommandHandler("learning_add", learning_add))
    app.add_handler(CommandHandler("learning_remove", learning_remove))
    app.add_handler(CommandHandler("learning_locations", learning_locations))
    app.add_handler(CommandHandler("learning_now", learning_now))
    app.add_handler(CommandHandler("learning_verify_now", learning_verify_now))
    app.add_handler(CommandHandler("weekend", weekend))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(CommandHandler("verify", verify))
    app.add_handler(CommandHandler("verify_rain", verify_rain))
    app.add_handler(CommandHandler("scores", scores))
    app.add_handler(CommandHandler("rain_scores", rain_scores))
    app.add_handler(CommandHandler("adaptive", adaptive))
    app.add_handler(CommandHandler("export_all", export_all))
    app.add_handler(CommandHandler("export_learning_csv", export_learning_csv))
    app.add_handler(CommandHandler("learning_report", learning_report))

    app.add_handler(CommandHandler("home", favorite_home))
    app.add_handler(CommandHandler("moscow", lambda update, context: favorite_current(update, context, "moscow")))
    app.add_handler(CommandHandler("moscow_ilya", lambda update, context: favorite_current(update, context, "moscow_ilya")))
    app.add_handler(CommandHandler("sergiev", lambda update, context: favorite_current(update, context, "sergiev")))
    app.add_handler(CommandHandler("kalyazin", lambda update, context: favorite_current(update, context, "kalyazin")))
    app.add_handler(CommandHandler("khvoynaya", lambda update, context: favorite_current(update, context, "khvoynaya")))
    app.add_handler(CommandHandler("lyubytino", lambda update, context: favorite_current(update, context, "lyubytino")))

    if app.job_queue:
        app.job_queue.run_repeating(check_morning_alerts, interval=60, first=10)
        app.job_queue.run_repeating(check_rain_alerts, interval=900, first=30)
        app.job_queue.run_repeating(check_learning_schedule, interval=60, first=20)
    else:
        print("Job queue недоступен: расписания morning/learning не запущены")

    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
