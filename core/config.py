# core/config.py

# ===== FILES =====

HISTORY_FILE = "weather_history.json"
SCORES_FILE = "model_scores.json"
RAIN_SCORES_FILE = "rain_scores.json"
SETTINGS_FILE = "settings.json"

MORNING_SUBSCRIBERS_FILE = "morning_subscribers.json"
LEARNING_FILE = "learning_forecasts.json"
DANGER_SUBSCRIBERS_FILE = "danger_subscribers.json"


# ===== WEATHER MODELS =====

WEATHER_MODELS = [
    "openmeteo",
    "weatherapi",
    "visualcrossing",
    "yr",
    "meteosource",
]


# ===== FAVORITE LOCATIONS =====

FAVORITE_LOCATIONS = {
    "home": {
        "name": "Дом",
        "country": "Россия",
        "latitude": 55.7558,
        "longitude": 37.6173,
        "region_type": "mixed",
    },

    "kalyazin": {
        "name": "Калязин",
        "country": "Россия",
        "latitude": 57.2400,
        "longitude": 37.8500,
        "region_type": "lake",
    },

    "khvoynaya": {
        "name": "Хвойная",
        "country": "Россия",
        "latitude": 58.9000,
        "longitude": 34.5330,
        "region_type": "forest",
    },

    "lyubytino": {
        "name": "Любытино",
        "country": "Россия",
        "latitude": 58.8130,
        "longitude": 33.3920,
        "region_type": "forest",
    },
}


# ===== TIMEZONE =====

DEFAULT_TIMEZONE = "Europe/Moscow"