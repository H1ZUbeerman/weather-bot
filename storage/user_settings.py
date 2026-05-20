from storage.json_storage import load_json_file, save_json_file

USER_SETTINGS_FILE = "user_settings.json"


def load_user_settings():
    return load_json_file(USER_SETTINGS_FILE, {})


def save_user_settings(data):
    save_json_file(USER_SETTINGS_FILE, data)


def get_user_settings(chat_id):
    data = load_user_settings()
    chat_id = str(chat_id)

    if chat_id not in data:
        data[chat_id] = {
            "home_location_key": "home",
            "morning_time": "08:00",
            "timezone": "Europe/Moscow",
        }
        save_user_settings(data)

    return data[chat_id]


def update_user_setting(chat_id, key, value):
    data = load_user_settings()
    chat_id = str(chat_id)

    if chat_id not in data:
        data[chat_id] = {
            "home_location_key": "home",
            "morning_time": "08:00",
            "timezone": "Europe/Moscow",
        }

    data[chat_id][key] = value
    save_user_settings(data)