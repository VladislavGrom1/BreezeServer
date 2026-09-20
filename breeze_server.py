from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Optional

import requests
from fastapi import FastAPI, APIRouter, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(
    title="weather API",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter()

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

LOCATION_TTL_SECONDS = 60 * 60 * 6

class HourlyField(str, Enum):
    temperature_2m = "temperature_2m"
    relative_humidity_2m = "relative_humidity_2m"
    dew_point_2m = "dew_point_2m"
    apparent_temperature = "apparent_temperature"
    pressure_msl = "pressure_msl"
    surface_pressure = "surface_pressure"
    cloud_cover = "cloud_cover"
    wind_speed_10m = "wind_speed_10m"
    wind_direction_10m = "wind_direction_10m"
    wind_gusts_10m = "wind_gusts_10m"
    precipitation = "precipitation"
    precipitation_probability = "precipitation_probability"
    rain = "rain"
    showers = "showers"
    snowfall = "snowfall"
    weather_code = "weather_code"
    visibility = "visibility"
    uv_index = "uv_index"
    is_day = "is_day"


class DailyField(str, Enum):
    temperature_2m_max = "temperature_2m_max"
    temperature_2m_mean = "temperature_2m_mean"
    temperature_2m_min = "temperature_2m_min"
    apparent_temperature_max = "apparent_temperature_max"
    apparent_temperature_mean = "apparent_temperature_mean"
    apparent_temperature_min = "apparent_temperature_min"
    precipitation_sum = "precipitation_sum"
    rain_sum = "rain_sum"
    showers_sum = "showers_sum"
    snowfall_sum = "snowfall_sum"
    precipitation_hours = "precipitation_hours"
    precipitation_probability_max = "precipitation_probability_max"
    weather_code = "weather_code"
    sunrise = "sunrise"
    sunset = "sunset"
    sunshine_duration = "sunshine_duration"
    daylight_duration = "daylight_duration"
    wind_speed_10m_max = "wind_speed_10m_max"
    wind_gusts_10m_max = "wind_gusts_10m_max"
    wind_direction_10m_dominant = "wind_direction_10m_dominant"
    uv_index_max = "uv_index_max"


class CurrentField(str, Enum):
    temperature_2m = "temperature_2m"
    relative_humidity_2m = "relative_humidity_2m"
    apparent_temperature = "apparent_temperature"
    is_day = "is_day"
    precipitation = "precipitation"
    rain = "rain"
    showers = "showers"
    snowfall = "snowfall"
    weather_code = "weather_code"
    cloud_cover = "cloud_cover"
    pressure_msl = "pressure_msl"
    surface_pressure = "surface_pressure"
    wind_speed_10m = "wind_speed_10m"
    wind_direction_10m = "wind_direction_10m"
    wind_gusts_10m = "wind_gusts_10m"


class TemperatureUnit(str, Enum):
    celsius = "celsius"
    fahrenheit = "fahrenheit"


class WindSpeedUnit(str, Enum):
    kmh = "kmh"
    ms = "ms"
    mph = "mph"
    kn = "kn"

class PrecipitationUnit(str, Enum):
    mm = "mm"
    inch = "inch"

class LocationCreate(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    elevation: Optional[float] = None
    label: Optional[str] = Field(None, description="Произвольное имя, например 'Home' или 'Москва'")


class LocationOut(BaseModel):
    location_id: str
    latitude: float
    longitude: float
    elevation: Optional[float] = None
    timezone: Optional[str] = None
    timezone_abbreviation: Optional[str] = None
    label: Optional[str] = None
    created_at: float
    expires_at: float

class GeocodeResult(BaseModel):
    name: str
    latitude: float
    longitude: float
    country: Optional[str] = None
    admin1: Optional[str] = None
    timezone: Optional[str] = None
    population: Optional[int] = None

class LocationStore:
    def __init__(self) -> None:
        self._data: dict[str, dict] = {}

    def set(self, location_id: str, payload: dict) -> None:
        self._data[location_id] = payload

    def get(self, location_id: str) -> dict:
        item = self._data.get(location_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Локация не найдена")
        if item["expires_at"] < time.time():
            del self._data[location_id]
            raise HTTPException(status_code=410, detail="Локация истекла, зарегистрируй заново через POST /v1/locations")
        return item

    def delete(self, location_id: str) -> None:
        self._data.pop(location_id, None)

    def list(self) -> list[dict]:
        now = time.time()
        return [v for v in self._data.values() if v["expires_at"] >= now]


LOCATIONS = LocationStore()

def _call_open_meteo(url: str, params: dict) -> dict:
    try:
        resp = requests.get(url, params=params, timeout=15)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Ошибка запроса к Open-Meteo: {e}")

    try:
        data = resp.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail=f"Open-Meteo вернул не-JSON. status={resp.status_code}, body={resp.text[:200]!r}",
        )

    if resp.status_code != 200:
        reason = data.get("reason") if isinstance(data, dict) else resp.text
        raise HTTPException(status_code=resp.status_code, detail=reason)

    return data


def _units_params(
    temperature_unit: TemperatureUnit,
    wind_speed_unit: WindSpeedUnit,
    precipitation_unit: PrecipitationUnit,
) -> dict:
    return {
        "temperature_unit": temperature_unit.value,
        "wind_speed_unit": wind_speed_unit.value,
        "precipitation_unit": precipitation_unit.value,
    }

@api_router.get("/v1/geocode", response_model=list[GeocodeResult], status_code=200)
def geocode(
    name: str = Query(..., min_length=1, description="Название города/места"),
    count: int = Query(5, ge=1, le=20),
    language: str = Query("ru"),
):
    data = _call_open_meteo(
        OPEN_METEO_GEOCODING_URL,
        {"name": name, "count": count, "language": language, "format": "json"},
    )
    results = data.get("results") or []
    return [
        GeocodeResult(
            name=r["name"],
            latitude=r["latitude"],
            longitude=r["longitude"],
            country=r.get("country"),
            admin1=r.get("admin1"),
            timezone=r.get("timezone"),
            population=r.get("population"),
        )
        for r in results
    ]

@api_router.post("/v1/locations", response_model=LocationOut, status_code=201)
def create_location(payload: LocationCreate):
    params = {
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "current": "temperature_2m",
        "timezone": "auto",
    }
    if payload.elevation is not None:
        params["elevation"] = payload.elevation

    data = _call_open_meteo(OPEN_METEO_FORECAST_URL, params)

    location_id = str(uuid.uuid4())
    now = time.time()
    record = {
        "location_id": location_id,
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "elevation": data.get("elevation"),
        "timezone": data.get("timezone"),
        "timezone_abbreviation": data.get("timezone_abbreviation"),
        "label": payload.label,
        "created_at": now,
        "expires_at": now + LOCATION_TTL_SECONDS,
    }
    LOCATIONS.set(location_id, record)
    return LocationOut(**record)


@api_router.get("/v1/locations", response_model=list[LocationOut], status_code=200)
def list_locations():
    return [LocationOut(**item) for item in LOCATIONS.list()]


@api_router.get("/v1/locations/{location_id}", response_model=LocationOut, status_code=200)
def get_location(location_id: str):
    return LocationOut(**LOCATIONS.get(location_id))


@api_router.delete("/v1/locations/{location_id}", status_code=204)
def delete_location(location_id: str):
    LOCATIONS.delete(location_id)
    return None

@api_router.get("/v1/locations/{location_id}/current", status_code=200)
def get_current(
    location_id: str,
    fields: list[CurrentField] = Query(
        default=[CurrentField.temperature_2m, CurrentField.weather_code, CurrentField.wind_speed_10m],
    ),
    temperature_unit: TemperatureUnit = TemperatureUnit.celsius,
    wind_speed_unit: WindSpeedUnit = WindSpeedUnit.kmh,
    precipitation_unit: PrecipitationUnit = PrecipitationUnit.mm,
):
    loc = LOCATIONS.get(location_id)
    params = {
        "latitude": loc["latitude"],
        "longitude": loc["longitude"],
        "current": ",".join(f.value for f in fields),
        "timezone": "auto",
        **_units_params(temperature_unit, wind_speed_unit, precipitation_unit),
    }
    return _call_open_meteo(OPEN_METEO_FORECAST_URL, params)


@api_router.get("/v1/locations/{location_id}/hourly", status_code=200)
def get_hourly(
    location_id: str,
    fields: list[HourlyField] = Query(
        default=[HourlyField.temperature_2m, HourlyField.weather_code],
    ),
    forecast_days: int = Query(7, ge=0, le=16),
    past_days: int = Query(0, ge=0, le=92),
    start_date: Optional[str] = Query(None, description="yyyy-mm-dd"),
    end_date: Optional[str] = Query(None, description="yyyy-mm-dd"),
    temperature_unit: TemperatureUnit = TemperatureUnit.celsius,
    wind_speed_unit: WindSpeedUnit = WindSpeedUnit.kmh,
    precipitation_unit: PrecipitationUnit = PrecipitationUnit.mm,
):
    loc = LOCATIONS.get(location_id)
    params = {
        "latitude": loc["latitude"],
        "longitude": loc["longitude"],
        "hourly": ",".join(f.value for f in fields),
        "timezone": "auto",
        "forecast_days": forecast_days,
        "past_days": past_days,
        **_units_params(temperature_unit, wind_speed_unit, precipitation_unit),
    }
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    return _call_open_meteo(OPEN_METEO_FORECAST_URL, params)


@api_router.get("/v1/locations/{location_id}/daily", status_code=200)
def get_daily(
    location_id: str,
    fields: list[DailyField] = Query(
        default=[DailyField.temperature_2m_max, DailyField.temperature_2m_min, DailyField.weather_code],
    ),
    forecast_days: int = Query(7, ge=0, le=16),
    past_days: int = Query(0, ge=0, le=92),
    temperature_unit: TemperatureUnit = TemperatureUnit.celsius,
    wind_speed_unit: WindSpeedUnit = WindSpeedUnit.kmh,
    precipitation_unit: PrecipitationUnit = PrecipitationUnit.mm,
):
    loc = LOCATIONS.get(location_id)
    params = {
        "latitude": loc["latitude"],
        "longitude": loc["longitude"],
        "daily": ",".join(f.value for f in fields),
        "timezone": "auto",
        "forecast_days": forecast_days,
        "past_days": past_days,
        **_units_params(temperature_unit, wind_speed_unit, precipitation_unit),
    }
    return _call_open_meteo(OPEN_METEO_FORECAST_URL, params)

@api_router.get("/v1/forecast", status_code=200)
def get_weather_legacy(latitude: float, longitude: float):
    """Проксирует Open-Meteo и возвращает JSON как есть (без location_id-флоу)."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "temperature_2m,weathercode,relativehumidity_2m,windspeed_10m,pressure_msl",
    }
    return _call_open_meteo(OPEN_METEO_FORECAST_URL, params)


app.include_router(api_router)


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")