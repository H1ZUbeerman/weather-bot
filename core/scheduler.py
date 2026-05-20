from datetime import datetime

from weather.locations import load_settings
from weather.alerts import (
    load_danger_subscribers,
    detect_danger_events,
    build_danger_signature,
)

from storage.json_storage import (
    load_json_file,
    save_json_file,
)


LAST_ALERT_SIGNATURES_FILE = "last_alert_signatures.json"


def load_last_alert_signatures():
    return load_json_file(LAST_ALERT_SIGNATURES_FILE, {})


def save_last_alert_signatures(data):
    save_json_file(LAST_ALERT_SIGNATURES_FILE, data)


async def check_danger_alerts_schedule(app):
    subscribers = load_danger_subscribers()

    if not subscribers:
        return

    last_signatures = load_last_alert_signatures()

    for subscriber in subscribers:
        try:
            chat_id = subscriber["chat_id"]
            location = subscriber["location"]

            events = detect_danger_events(location)

            if not events:
                continue

            signature = build_danger_signature(events)

            previous_signature = last_signatures.get(str(chat_id))

            if signature == previous_signature:
                continue

            lines = []

            for event in events[:8]:
                lines.append(event["text"])

            message = (
                f"🚨 Опасные погодные явления\n\n"
                + "\n".join(lines)
            )

            await app.bot.send_message(
                chat_id=chat_id,
                text=message,
            )

            last_signatures[str(chat_id)] = signature

        except Exception as e:
            print("Danger alerts scheduler error:", e)

    save_last_alert_signatures(last_signatures)


async def check_morning_schedule(app):
    settings = load_settings()

    morning_time = settings.get("morning_time", "08:00")

    now = datetime.now().strftime("%H:%M")

    if now != morning_time:
        return

    print("Morning scheduler triggered")