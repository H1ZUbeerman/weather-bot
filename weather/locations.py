from geopy.geocoders import Nominatim

from core.config import FAVORITE_LOCATIONS
from storage.json_storage import load_json_file, save_json_file
from core.config import SETTINGS_FILE


def load_settings():
    default_settings = {
        "home_location_key": "home",
        "morning_time": "08:00",
        "timezone": "Europe/Moscow",
        "last_morning_sent_date": "",
        "learning_enabled": False,
        "last_learning_forecast_date": "",
        "last_learning_verify_date": "",
    }

    settings = load_json_file(SETTINGS_FILE, default_settings)

    for key, value in default_settings.items():
        if key not in settings:
            settings[key] = value

    return settings


def save_settings(settings):
    save_json_file(SETTINGS_FILE, settings)


def get_home_location():
    settings = load_settings()
    home_key = settings.get("home_location_key", "home")
    return FAVORITE_LOCATIONS.get(home_key, FAVORITE_LOCATIONS["home"])


def get_home_location_key():
    settings = load_settings()
    return settings.get("home_location_key", "home")


def get_location_by_key(key):
    return FAVORITE_LOCATIONS.get(key)


def get_location_by_name(name):
    for location in FAVORITE_LOCATIONS.values():
        if location["name"].lower() == name.lower():
            return location

    return None


def get_city_coordinates(city_name):
    geolocator = Nominatim(user_agent="weather_bot")

    location = geolocator.geocode(city_name)

    if not location:
        return None

    return {
        "name": city_name,
        "country": "Unknown",
        "latitude": location.latitude,
        "longitude": location.longitude,
        "region_type": "mixed",
    }


def get_location(context, default_key="home"):
    if not context.args:
        if default_key == "home":
            return get_home_location()

        return FAVORITE_LOCATIONS[default_key]

    key = context.args[0].lower()

    if key in FAVORITE_LOCATIONS:
        return FAVORITE_LOCATIONS[key]

    return get_city_coordinates(" ".join(context.args))