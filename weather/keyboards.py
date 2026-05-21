from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core.config import FAVORITE_LOCATIONS


def build_home_keyboard():
    buttons = []

    for key, location in FAVORITE_LOCATIONS.items():
        buttons.append([
            InlineKeyboardButton(
                text=f"📍 {location['name']}",
                callback_data=f"set_home:{key}",
            )
        ])

    return InlineKeyboardMarkup(buttons)