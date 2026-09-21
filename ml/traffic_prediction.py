"""
Model 2: Traffic Prediction

Input features : hour of day, location (zone name), weather condition
Output         : congestion level (Low / Moderate / High / Severe)
"""

import os
import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import OrdinalEncoder
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

from config import Config
from ml.generate_datasets import generate_traffic_history

CAT_FEATURES = ["location", "weather_condition"]
NUM_FEATURES = ["hour"]


def _encode(df, encoder=None, fit=False):
    if fit:
        encoder = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
        cat_encoded = encoder.fit_transform(df[CAT_FEATURES])
    else:
        cat_encoded = encoder.transform(df[CAT_FEATURES])
    cat_df = pd.DataFrame(cat_encoded, columns=CAT_FEATURES, index=df.index)
    return pd.concat([df[NUM_FEATURES], cat_df], axis=1), encoder


def train(save=True):
    if os.path.exists(Config.TRAFFIC_HISTORY_CSV):
        df = pd.read_csv(Config.TRAFFIC_HISTORY_CSV)
    else:
        df = generate_traffic_history()

    X_raw = df[NUM_FEATURES + CAT_FEATURES]
    y = df["congestion_level"]

    X, encoder = _encode(X_raw, fit=True)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=7, stratify=y
    )

    model = RandomForestClassifier(n_estimators=200, max_depth=8, random_state=7)
    model.fit(X_train, y_train)

    acc = accuracy_score(y_test, model.predict(X_test))
    print(f"[traffic_prediction] validation accuracy: {acc:.3f}")

    if save:
        os.makedirs(Config.MODELS_DIR, exist_ok=True)
        joblib.dump({"model": model, "encoder": encoder}, Config.TRAFFIC_MODEL_PATH)

    return model, encoder


_cached_bundle = None


def load_model():
    global _cached_bundle
    if _cached_bundle is not None:
        return _cached_bundle
    if os.path.exists(Config.TRAFFIC_MODEL_PATH):
        bundle = joblib.load(Config.TRAFFIC_MODEL_PATH)
        _cached_bundle = (bundle["model"], bundle["encoder"])
        return _cached_bundle
    _cached_bundle = train(save=True)
    return _cached_bundle


def predict(hour, location, weather_condition):
    model, encoder = load_model()
    row = pd.DataFrame([[hour, location, weather_condition]], columns=NUM_FEATURES + CAT_FEATURES)
    X, _ = _encode(row, encoder=encoder, fit=False)
    pred = model.predict(X)[0]
    proba = model.predict_proba(X)[0]
    confidence = round(float(max(proba)), 3)
    return {"congestion_level": pred, "confidence": confidence}


if __name__ == "__main__":
    train()
    print(predict(18, "Central District", "storm"))
