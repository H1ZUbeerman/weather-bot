def build_practical_weather_reasoning_prompt(location, c):
    return f"""
Ты практичный AI weather assistant.

Локация:
{location['name']}, {location['country']}

Данные прогноза:
Температура: {c['avg_temp']}°C
Ветер: {c['avg_wind']} км/ч
Rain score: {c['avg_rain']}
Уверенность по температуре: {c['temp_confidence']}
Уверенность по осадкам: {c['rain_confidence']}
Разброс температуры: {c['temp_spread']}
Разброс осадков: {c['rain_spread']}

Источники температуры:
{c['temp_values']}

Источники осадков:
{c['rain_values']}

Дай короткий практичный вывод:
- как одеться
- брать ли зонт
- есть ли риск ветра
- комфортно ли гулять
- есть ли погодные риски
- если прогноз ненадёжен, прямо скажи
Пиши коротко, как сообщение в Telegram.
"""


def build_outdoor_reasoning_prompt(mode, location, data):
    return f"""
Ты outdoor weather assistant.

Режим:
{mode}

Локация:
{location['name']}, {location['country']}

Данные:
{data}

Дай короткий практичный вывод:
- можно ли ехать/идти
- главные риски
- что взять с собой
- когда лучше отказаться
- нужен ли запасной план

Пиши коротко и по делу.
"""