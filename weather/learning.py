from storage.json_storage import load_json_file, save_json_file
from core.config import SCORES_FILE, RAIN_SCORES_FILE


def load_scores():
    default_scores = {
        "openmeteo": {"checks": 0, "total_error": 0, "wins": 0},
        "weatherapi": {"checks": 0, "total_error": 0, "wins": 0},
        "visualcrossing": {"checks": 0, "total_error": 0, "wins": 0},
        "yr": {"checks": 0, "total_error": 0, "wins": 0},
        "meteosource": {"checks": 0, "total_error": 0, "wins": 0},
        "consensus": {"checks": 0, "total_error": 0, "wins": 0},
    }

    scores = load_json_file(SCORES_FILE, default_scores)

    for key in default_scores:
        if key not in scores:
            scores[key] = default_scores[key]

    return scores


def save_scores(scores):
    save_json_file(SCORES_FILE, scores)


def update_model_scores(errors, consensus_error):
    scores = load_scores()

    all_errors = dict(errors)
    all_errors["consensus"] = consensus_error

    best_model = min(all_errors, key=all_errors.get)

    for model, error in all_errors.items():
        scores[model]["checks"] += 1
        scores[model]["total_error"] += error

        if model == best_model:
            scores[model]["wins"] += 1

    save_scores(scores)

    return best_model


def load_rain_scores():
    default_scores = {
        "openmeteo": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
        "weatherapi": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
        "visualcrossing": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
        "yr": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
        "meteosource": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
        "consensus": {"checks": 0, "correct": 0, "false_positive": 0, "missed": 0, "total_error": 0},
    }

    scores = load_json_file(RAIN_SCORES_FILE, default_scores)

    for key in default_scores:
        if key not in scores:
            scores[key] = default_scores[key]

    return scores


def save_rain_scores(scores):
    save_json_file(RAIN_SCORES_FILE, scores)


def update_rain_scores(predictions, factual_rain_score):
    scores = load_rain_scores()

    fact_is_rain = factual_rain_score >= 25

    for model, predicted_score in predictions.items():
        predicted_is_rain = predicted_score >= 30

        scores[model]["checks"] += 1
        scores[model]["total_error"] += abs(predicted_score - factual_rain_score)

        if predicted_is_rain == fact_is_rain:
            scores[model]["correct"] += 1
        elif predicted_is_rain and not fact_is_rain:
            scores[model]["false_positive"] += 1
        elif not predicted_is_rain and fact_is_rain:
            scores[model]["missed"] += 1

    save_rain_scores(scores)


def get_adaptive_weights_from_scores():
    scores_data = load_scores()

    source_scores = {
        k: v for k, v in scores_data.items()
        if k != "consensus"
    }

    if all(v["checks"] == 0 for v in source_scores.values()):
        return None

    quality = {}

    for model, data in source_scores.items():
        checks = data["checks"]
        total_error = data["total_error"]
        wins = data["wins"]

        if checks == 0:
            quality[model] = 0.01
            continue

        avg_error = total_error / checks
        win_rate = wins / checks

        quality[model] = (1 / (avg_error + 0.1)) + (win_rate * 0.3)

    total_quality = sum(quality.values())

    adaptive_weights = {
        model: round(score / total_quality, 2)
        for model, score in quality.items()
    }

    correction = round(1 - sum(adaptive_weights.values()), 2)

    if correction != 0:
        best_model = max(adaptive_weights, key=adaptive_weights.get)
        adaptive_weights[best_model] = round(
            adaptive_weights[best_model] + correction,
            2
        )

    return adaptive_weights