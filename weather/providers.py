import requests

from weather.consensus import rain_score_from_mm


def get_openmeteo_current(location):
    lat = location["latitude"]
    lon = location["longitude"]

    data = requests.get(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}"
        f"&longitude={lon}"
        "&current=temperature_2m,wind_speed_10m,precipitation",
        timeout=20,
    ).json()

    current = data["current"]

    return {
        "temperature": current["temperature_2m"],
        "wind": current["wind_speed_10m"],
        "rain": rain_score_from_mm(current.get("precipitation", 0)),
    }


def get_weatherapi_current(location, api_key):
    lat = location["latitude"]
    lon = location["longitude"]

    data = requests.get(
        "https://api.weatherapi.com/v1/current.json"
        f"?key={api_key}"
        f"&q={lat},{lon}&aqi=no",
        timeout=20,
    ).json()

    current = data["current"]

    return {
        "temperature": current["temp_c"],
        "wind": current["wind_kph"],
        "rain": rain_score_from_mm(current.get("precip_mm", 0)),
    }


def get_visualcrossing_current(location, api_key):
    lat = location["latitude"]
    lon = location["longitude"]

    data = requests.get(
        "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/"
        f"{lat},{lon}"
        f"?unitGroup=metric"
        f"&key={api_key}"
        "&include=current",
        timeout=20,
    ).json()

    current = data["currentConditions"]

    rain = current.get("precipprob")

    if rain is None:
        rain = rain_score_from_mm(current.get("precip", 0))

    return {
        "temperature": current["temp"],
        "wind": current["windspeed"],
        "rain": rain,
    }


def get_yr_current(location):
    lat = location["latitude"]
    lon = location["longitude"]

    url = (
        "https://api.met.no/weatherapi/locationforecast/2.0/compact"
        f"?lat={lat}&lon={lon}"
    )

    headers = {
        "User-Agent": "WeatherAnalystBot/1.0"
    }

    response = requests.get(url, headers=headers, timeout=20)

    if response.status_code != 200:
        raise ValueError("yr.no ошибка")

    data = response.json()

    item = data["properties"]["timeseries"][0]
    now = item["data"]["instant"]["details"]

    next_1h = item["data"].get("next_1_hours", {})
    precip_mm = next_1h.get("details", {}).get("precipitation_amount", 0)

    return {
        "temperature": now["air_temperature"],
        "wind": round(now["wind_speed"] * 3.6, 1),
        "rain": rain_score_from_mm(precip_mm),
        "raw": data,
    }


def get_meteosource_current(location, api_key):
    lat = location["latitude"]
    lon = location["longitude"]

    url = (
        "https://www.meteosource.com/api/v1/free/point"
        f"?lat={lat}"
        f"&lon={lon}"
        "&sections=current"
        "&timezone=auto"
        "&language=en"
        "&units=metric"
        f"&key={api_key}"
    )

    response = requests.get(url, timeout=20)

    if response.status_code != 200:
        raise ValueError("Meteosource ошибка")

    data = response.json()
    current = data.get("current", {})

    wind_data = current.get("wind", {})

    if isinstance(wind_data, dict):
        wind = wind_data.get("speed")
    else:
        wind = current.get("wind_speed")

    precipitation = current.get("precipitation", 0)

    if isinstance(precipitation, dict):
        rain = rain_score_from_mm(precipitation.get("total", 0))
    else:
        rain = rain_score_from_mm(precipitation)

    return {
        "temperature": current.get("temperature"),
        "wind": wind,
        "rain": rain,
    }
