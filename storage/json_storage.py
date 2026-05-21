import json
import os
from pathlib import Path


def get_data_dir():
    return Path(os.getenv("DATA_DIR", "."))


def get_data_path(filename):
    path = Path(filename)

    if path.is_absolute():
        return path

    return get_data_dir() / path


def load_json_file(filename, default):
    path = get_data_path(filename)

    if not path.exists():
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json_file(filename, data):
    get_data_dir().mkdir(parents=True, exist_ok=True)

    with open(get_data_path(filename), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
