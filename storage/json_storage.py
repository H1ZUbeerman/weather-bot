import json
from pathlib import Path


def load_json_file(filename, default):
    path = Path(filename)

    if not path.exists():
        return default

    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json_file(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)