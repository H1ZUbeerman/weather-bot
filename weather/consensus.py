def weighted_average(values, weights):
    total = 0
    total_weight = 0

    for key, value in values.items():
        if value is not None:
            weight = weights.get(key, 0)
            total += value * weight
            total_weight += weight

    if total_weight == 0:
        return 0

    return round(total / total_weight, 1)


def calculate_confidence(spread, good_limit, medium_limit):
    if spread <= good_limit:
        return "высокая"

    if spread <= medium_limit:
        return "средняя"

    return "низкая"


def rain_score_from_mm(mm):
    if mm is None:
        return 0

    if mm >= 5:
        return 90

    if mm >= 2:
        return 70

    if mm >= 0.5:
        return 45

    if mm > 0:
        return 25

    return 0