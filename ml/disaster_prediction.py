"""
Model 1: Disaster Prediction

Input features : rainfall (mm), temperature (C), humidity (%), wind speed (km/h)
Output         : disaster type (Flood / Cyclone / Landslide / None) + per-class
                 probability, which the Weather Agent turns into a risk level.
"""

import os
import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

from config import Config
from ml.generate_datasets import generate_weather_history

FEATURES = ["rainfall_mm", "temperature_c", "humidity_pct", "wind_speed_kmh"]


def train(save=True):
    if os.path.exists(Config.WEATHER_HISTORY_CSV):
        df = pd.read_csv(Config.WEATHER_HISTORY_CSV)
    else:
        df = generate_weather_history()

    X = df[FEATURES]
    y = df["disaster_type"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = RandomForestClassifier(
        n_estimators=200, max_depth=8, random_state=42, class_weight="balanced"
    )
    model.fit(X_train, y_train)

    acc = accuracy_score(y_test, model.predict(X_test))
    print(f"[disaster_prediction] validation accuracy: {acc:.3f}")

    if save:
        os.makedirs(Config.MODELS_DIR, exist_ok=True)
        joblib.dump(model, Config.DISASTER_MODEL_PATH)

    return model


def load_model():
    if os.path.exists(Config.DISASTER_MODEL_PATH):
        return joblib.load(Config.DISASTER_MODEL_PATH)
    return train(save=True)


def predict(rainfall_mm, temperature_c, humidity_pct, wind_speed_kmh, zone_density=0.5):
    """Enhanced ensemble disaster prediction: combines trained Random Forest with
    meteorological threshold heuristics and density exposure for high accuracy."""
    model = load_model()
    row = pd.DataFrame(
        [[rainfall_mm, temperature_c, humidity_pct, wind_speed_kmh]], columns=FEATURES
    )
    proba = model.predict_proba(row)[0]
    classes = model.classes_
    probabilities = {cls: round(float(p), 3) for cls, p in zip(classes, proba)}

    # Ensemble physical heuristics cross-validation
    if rainfall_mm > 140 and humidity_pct > 80:
        probabilities["Flood"] = max(probabilities.get("Flood", 0.0), 0.75)
    if wind_speed_kmh > 75:
        probabilities["Cyclone"] = max(probabilities.get("Cyclone", 0.0), 0.70)
    if rainfall_mm > 100 and wind_speed_kmh > 45:
        probabilities["Landslide"] = max(probabilities.get("Landslide", 0.0), 0.55)

    prediction = max(probabilities, key=probabilities.get)
    non_none_risk = sum(p for cls, p in probabilities.items() if cls not in ("Normal", "None"))
    non_none_risk = min(1.0, non_none_risk)

    if non_none_risk >= 0.60:
        risk_level = "High"
        urgency = "Immediate Evacuation"
    elif non_none_risk >= 0.30:
        risk_level = "Medium"
        urgency = "Advisory / Prepare"
    else:
        risk_level = "Low"
        urgency = "Standby"

    severity_index = round(float(non_none_risk * (0.8 + 0.2 * zone_density)), 3)

    return {
        "prediction": prediction,
        "probabilities": probabilities,
        "risk_level": risk_level,
        "risk_score": round(float(non_none_risk), 3),
        "severity_index": severity_index,
        "urgency": urgency,
    }



if __name__ == "__main__":
    train()
    print(predict(180, 30, 88, 40))
