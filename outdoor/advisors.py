def build_trip_prompt(location, tomorrow_c, tomorrow_score, saturday_c, saturday_score, sunday_c, sunday_score, week_items):
    return f"""
Локация:
{location['name']}

Анализ поездки.

Завтра:
temp {tomorrow_c['avg_temp']}
rain {tomorrow_c['avg_rain']}
wind {tomorrow_c['avg_wind']}
rain confidence {tomorrow_c['rain_confidence']}
score {tomorrow_score}

Суббота:
temp {saturday_c['avg_temp']}
rain {saturday_c['avg_rain']}
wind {saturday_c['avg_wind']}
rain confidence {saturday_c['rain_confidence']}
score {saturday_score}

Воскресенье:
temp {sunday_c['avg_temp']}
rain {sunday_c['avg_rain']}
wind {sunday_c['avg_wind']}
rain confidence {sunday_c['rain_confidence']}
score {sunday_score}

Неделя:
{week_items}

Дай понятный вывод:
- стоит ли ехать
- какой день лучший
- какой день хуже
- брать ли дождевик/зонт
- какие риски по ветру и дождю
- нужен ли запасной план
"""


def build_baidarka_prompt(location, part_candidates, best_window, worst_window, saturday_c, saturday_score, sunday_c, sunday_score):
    return f"""
Локация:
{location['name']}

Режим: байдарка.

Окна сегодня и завтра:
{part_candidates}

Лучшее окно:
{best_window}

Худшее окно:
{worst_window}

Суббота:
temp {saturday_c['avg_temp']}
rain {saturday_c['avg_rain']}
wind {saturday_c['avg_wind']}
rain confidence {saturday_c['rain_confidence']}
score {saturday_score}

Воскресенье:
temp {sunday_c['avg_temp']}
rain {sunday_c['avg_rain']}
wind {sunday_c['avg_wind']}
rain confidence {sunday_c['rain_confidence']}
score {sunday_score}

Дай практичный вывод для человека, который хочет выйти на воду на байдарке:
- можно ли идти
- когда лучшее окно
- главные риски
- что взять с собой
- когда лучше отказаться
"""


def build_camping_prompt(location, week_data, best_day, worst_day):
    return f"""
Локация:
{location['name']}

Camping analysis:
{week_data}

Лучший день:
{best_day}

Худший день:
{worst_day}

Дай практичный вывод для кемпинга:
- стоит ли ехать с палаткой
- насколько комфортна ночь
- риск дождя ночью
- риск ветра
- какие вещи взять
- лучший день недели для палатки
"""