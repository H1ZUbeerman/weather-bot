def calculate_trip_score(avg_temp, avg_rain, avg_wind, rain_spread=0):
    score = 100

    score -= avg_rain * 0.8
    score -= max(0, avg_wind - 15) * 1.8
    score -= abs(avg_temp - 20) * 1.5
    score -= rain_spread * 0.3

    return round(max(score, 0), 1)


def trip_recommendation_from_score(score):
    if score >= 75:
        return "🟢 Можно ехать"
    if score >= 55:
        return "🟡 Можно ехать, но с оговорками"
    if score >= 35:
        return "🟠 Лучше подумать / нужен запасной план"
    return "🔴 Лучше не планировать активную поездку"


def calculate_baidarka_score(temp, rain, wind, rain_spread=0):
    score = 100

    score -= max(0, wind - 10) * 3.0
    score -= rain * 0.7
    score -= abs(temp - 20) * 1.4
    score -= rain_spread * 0.4

    return round(max(score, 0), 1)


def baidarka_recommendation(score, wind, rain):
    if wind >= 35:
        return "🔴 Не рекомендую: слишком сильный ветер для байдарки"
    if rain >= 75:
        return "🔴 Не рекомендую: высокий риск дождя"
    if score >= 75:
        return "🟢 Хорошие условия для байдарки"
    if score >= 55:
        return "🟡 Можно, но следить за ветром и дождём"
    if score >= 35:
        return "🟠 Условия спорные, нужен запасной план"
    return "🔴 Лучше не выходить на воду"


def calculate_camping_score(temp, rain, wind, rain_spread=0, night=False):
    score = 100

    score -= rain * 0.9

    if night:
        score -= max(0, wind - 8) * 3.2
    else:
        score -= max(0, wind - 12) * 2.0

    score -= abs(temp - 18) * 1.8
    score -= rain_spread * 0.5

    if night and temp <= 7:
        score -= 20

    return round(max(score, 0), 1)


def camping_recommendation(score, rain, wind, night_temp):
    if rain >= 80:
        return "🔴 Очень высокий риск дождя для палатки"
    if wind >= 35:
        return "🔴 Слишком сильный ветер для комфортного кемпинга"
    if night_temp <= 5:
        return "🟠 Ночью будет очень холодно"
    if score >= 75:
        return "🟢 Отличные условия для палатки"
    if score >= 55:
        return "🟡 Нормально, но есть погодные риски"
    if score >= 35:
        return "🟠 Спорные условия для кемпинга"
    return "🔴 Лучше выбрать другой день"