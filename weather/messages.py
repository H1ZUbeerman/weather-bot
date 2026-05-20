def build_weather_prompt(location, c):
    return f"""
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
"""


def build_weather_message(location, c, ai_summary):
    return (
        f"📍 {location['name']}, {location['country']}\n\n"
        f"🌡 Температура:\n"
        f"🌦 Open-Meteo: {c['temp_values']['openmeteo']}°C\n"
        f"🌤 WeatherAPI: {c['temp_values']['weatherapi']}°C\n"
        f"🌍 Visual Crossing: {c['temp_values']['visualcrossing']}°C\n"
        f"🇳🇴 yr.no: {c['temp_values']['yr']}°C\n"
        f"🌐 Meteosource: {c['temp_values']['meteosource']}°C\n\n"

        f"☔ Осадки / rain score:\n"
        f"🌦 Open-Meteo: {c['rain_values']['openmeteo']}\n"
        f"🌤 WeatherAPI: {c['rain_values']['weatherapi']}\n"
        f"🌍 Visual Crossing: {c['rain_values']['visualcrossing']}\n"
        f"🇳🇴 yr.no: {c['rain_values']['yr']}\n"
        f"🌐 Meteosource: {c['rain_values']['meteosource']}\n\n"

        f"🧠 Weighted Consensus:\n"
        f"🌡 ~{c['avg_temp']}°C\n"
        f"💨 ~{c['avg_wind']} км/ч\n"
        f"☔ Rain Consensus: ~{c['avg_rain']}\n"
        f"📊 Temp Spread: {c['temp_spread']}°C\n"
        f"📊 Rain Spread: {c['rain_spread']}\n"
        f"✅ Temp confidence: {c['temp_confidence']}\n"
        f"✅ Rain confidence: {c['rain_confidence']}\n"
        f"⚙️ Весовой режим: {c['weights_mode']}\n\n"

        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )
def build_morning_prompt(location, current_consensus, morning_part, day_part, evening_part, night_part):
    return f"""
Локация:
{location['name']}

Утренний погодный брифинг.

Сейчас:
Температура {current_consensus['avg_temp']}
Ветер {current_consensus['avg_wind']}
Осадки {current_consensus['avg_rain']}
Уверенность по температуре {current_consensus['temp_confidence']}
Уверенность по дождю {current_consensus['rain_confidence']}

Сегодня по частям дня:
Утро: {morning_part}
День: {day_part}
Вечер: {evening_part}
Ночь: {night_part}

Сделай короткий утренний брифинг:
- как одеться
- брать ли зонт
- когда лучше выходить
- есть ли риск дождя вечером
- стоит ли планировать прогулку/дела на улице
"""


def build_morning_message(location, current_consensus, morning_part, day_part, evening_part, night_part, ai_summary):
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