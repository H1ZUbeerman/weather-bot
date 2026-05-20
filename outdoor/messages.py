def build_trip_message(
    location,
    tomorrow_date,
    tomorrow_c,
    tomorrow_score,
    saturday,
    saturday_c,
    saturday_score,
    sunday,
    sunday_c,
    sunday_score,
    best_week_day,
    worst_week_day,
    ai_summary,
    recommendation_fn,
):
    return (
        f"🚗 Trip mode\n"
        f"📍 {location['name']}, {location['country']}\n\n"

        f"📅 Завтра {tomorrow_date.strftime('%Y-%m-%d')}\n"
        f"🌡 ~{tomorrow_c['avg_temp']}°C\n"
        f"☔ ~{tomorrow_c['avg_rain']}%\n"
        f"💨 ~{tomorrow_c['avg_wind']} км/ч\n"
        f"✅ Rain: {tomorrow_c['rain_confidence']}\n"
        f"🏁 Trip score: {tomorrow_score}/100\n"
        f"{recommendation_fn(tomorrow_score)}\n\n"

        f"🏕 Ближайшие выходные\n"
        f"📅 Суббота {saturday.strftime('%Y-%m-%d')}: "
        f"{saturday_score}/100 — {recommendation_fn(saturday_score)}\n"
        f"🌡 ~{saturday_c['avg_temp']}°C, ☔ ~{saturday_c['avg_rain']}%, 💨 ~{saturday_c['avg_wind']} км/ч\n\n"

        f"📅 Воскресенье {sunday.strftime('%Y-%m-%d')}: "
        f"{sunday_score}/100 — {recommendation_fn(sunday_score)}\n"
        f"🌡 ~{sunday_c['avg_temp']}°C, ☔ ~{sunday_c['avg_rain']}%, 💨 ~{sunday_c['avg_wind']} км/ч\n\n"

        f"🏆 Лучший день недели:\n"
        f"{best_week_day['date']} ({best_week_day['weekday']}) — "
        f"{best_week_day['score']}/100, "
        f"🌡 ~{best_week_day['temp']}°C, "
        f"☔ ~{best_week_day['rain']}%, "
        f"💨 ~{best_week_day['wind']} км/ч\n\n"

        f"⚠️ Худший день недели:\n"
        f"{worst_week_day['date']} ({worst_week_day['weekday']}) — "
        f"{worst_week_day['score']}/100, "
        f"🌡 ~{worst_week_day['temp']}°C, "
        f"☔ ~{worst_week_day['rain']}%, "
        f"💨 ~{worst_week_day['wind']} км/ч\n\n"

        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )
def build_baidarka_message(
    location,
    best_window,
    worst_window,
    saturday,
    saturday_c,
    saturday_score,
    sunday,
    sunday_c,
    sunday_score,
    ai_summary,
    recommendation_fn,
):
    return (
        f"🚣 Байдарка mode\n"
        f"📍 {location['name']}, {location['country']}\n\n"

        f"🏆 Лучшее окно:\n"
        f"{best_window['day_label']} {best_window['date']} — {best_window['part_title']}\n"
        f"🌡 ~{best_window['temp']}°C\n"
        f"☔ ~{best_window['rain']}%\n"
        f"💨 до ~{best_window['wind']} км/ч\n"
        f"🏁 Score: {best_window['score']}/100\n"
        f"{best_window['recommendation']}\n\n"

        f"⚠️ Худшее окно:\n"
        f"{worst_window['day_label']} {worst_window['date']} — {worst_window['part_title']}\n"
        f"🌡 ~{worst_window['temp']}°C\n"
        f"☔ ~{worst_window['rain']}%\n"
        f"💨 до ~{worst_window['wind']} км/ч\n"
        f"🏁 Score: {worst_window['score']}/100\n"
        f"{worst_window['recommendation']}\n\n"

        f"🏕 Ближайшие выходные:\n"
        f"📅 Суббота {saturday.strftime('%Y-%m-%d')}: "
        f"{saturday_score}/100 — "
        f"{recommendation_fn(saturday_score, saturday_c['avg_wind'], saturday_c['avg_rain'])}\n"
        f"🌡 ~{saturday_c['avg_temp']}°C, ☔ ~{saturday_c['avg_rain']}%, 💨 ~{saturday_c['avg_wind']} км/ч\n\n"

        f"📅 Воскресенье {sunday.strftime('%Y-%m-%d')}: "
        f"{sunday_score}/100 — "
        f"{recommendation_fn(sunday_score, sunday_c['avg_wind'], sunday_c['avg_rain'])}\n"
        f"🌡 ~{sunday_c['avg_temp']}°C, ☔ ~{sunday_c['avg_rain']}%, 💨 ~{sunday_c['avg_wind']} км/ч\n\n"

        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )
def build_camping_message(
    location,
    best_day,
    worst_day,
    week_data,
    ai_summary,
):
    message = (
        f"🏕 Camping mode\n"
        f"📍 {location['name']}, {location['country']}\n\n"

        f"🏆 Лучший день:\n"
        f"{best_day['date']} ({best_day['weekday']})\n"
        f"🏁 Score: {best_day['score']}/100\n"
        f"{best_day['recommendation']}\n"
        f"🌡 День: ~{best_day['day_temp']}°C\n"
        f"🌙 Ночь: ~{best_day['night_temp']}°C\n"
        f"☔ ~{best_day['rain']}%\n"
        f"💨 до ~{best_day['wind']} км/ч\n\n"

        f"⚠️ Худший день:\n"
        f"{worst_day['date']} ({worst_day['weekday']})\n"
        f"🏁 Score: {worst_day['score']}/100\n"
        f"{worst_day['recommendation']}\n"
        f"🌡 День: ~{worst_day['day_temp']}°C\n"
        f"🌙 Ночь: ~{worst_day['night_temp']}°C\n"
        f"☔ ~{worst_day['rain']}%\n"
        f"💨 до ~{worst_day['wind']} км/ч\n\n"

        f"📆 Неделя:\n"
    )

    for item in week_data:
        message += (
            f"📅 {item['date']} ({item['weekday']}) — "
            f"{item['score']}/100\n"
            f"🌡 День ~{item['day_temp']}°C / Ночь ~{item['night_temp']}°C\n"
            f"☔ ~{item['rain']}% | 💨 ~{item['wind']} км/ч\n"
            f"{item['recommendation']}\n\n"
        )

    message += (
        f"🤖 AI-вывод:\n"
        f"{ai_summary}"
    )

    return message