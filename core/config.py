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

    "sergiev": {
        "name": "Сергиев Посад",
        "country": "Россия",
        "latitude": 56.3153,
        "longitude": 38.1358,
        "region_type": "mixed",
    },

    "Moscow Ilya": {
        "name": "Москва Илья",
        "country": "Россия",
        "latitude": 55.873819,
        "longitude": 37.610251,
        "region_type": "mixed",
    },
    "moscow_center": {
        "name": "Москва Центр",
        "country": "Россия",
        "latitude": 55.7558,
        "longitude": 37.6176,
        "region_type": "urban",
    },

    "moscow_north": {
        "name": "Москва Север",
        "country": "Россия",
        "latitude": 55.8800,
        "longitude": 37.5500,
        "region_type": "urban",
    },

    "moscow_south": {
        "name": "Москва Юг",
        "country": "Россия",
        "latitude": 55.6200,
        "longitude": 37.6500,
        "region_type": "urban",
    },

    "moscow_west": {
        "name": "Москва Запад",
        "country": "Россия",
        "latitude": 55.7400,
        "longitude": 37.4200,
        "region_type": "urban",
    },

    "moscow_east": {
        "name": "Москва Восток",
        "country": "Россия",
        "latitude": 55.7800,
        "longitude": 37.8200,
        "region_type": "urban",
    },
}


# ===== TIMEZONE =====

DEFAULT_TIMEZONE = "Europe/Moscow"