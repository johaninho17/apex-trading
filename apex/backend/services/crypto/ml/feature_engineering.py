import glob
import os

import numpy as np
import pandas as pd

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "ai_training"))


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    macd = ema_fast - ema_slow
    macd_signal = _ema(macd, signal)
    macd_hist = macd - macd_signal
    return pd.DataFrame({
        f"MACD_{fast}_{slow}_{signal}": macd,
        f"MACDh_{fast}_{slow}_{signal}": macd_hist,
        f"MACDs_{fast}_{slow}_{signal}": macd_signal,
    })


def _bbands(series: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    mid = series.rolling(length).mean()
    dev = series.rolling(length).std(ddof=0)
    upper = mid + (std * dev)
    lower = mid - (std * dev)
    bandwidth = ((upper - lower) / mid.replace(0.0, np.nan)) * 100.0
    percent = (series - lower) / (upper - lower).replace(0.0, np.nan)
    std_label = f"{std:.1f}"
    return pd.DataFrame({
        f"BBL_{length}_{std_label}": lower,
        f"BBM_{length}_{std_label}": mid,
        f"BBU_{length}_{std_label}": upper,
        f"BBB_{length}_{std_label}": bandwidth,
        f"BBP_{length}_{std_label}": percent,
    })


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Inject quantitative features into raw OHLCV data without pandas_ta/numba."""
    frame = df.copy()

    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    frame["hour_of_day"] = frame["timestamp"].dt.hour
    frame["day_of_week"] = frame["timestamp"].dt.dayofweek

    frame["hour_sin"] = np.sin(frame["hour_of_day"] * (2.0 * np.pi / 24.0))
    frame["hour_cos"] = np.cos(frame["hour_of_day"] * (2.0 * np.pi / 24.0))
    frame["day_sin"] = np.sin(frame["day_of_week"] * (2.0 * np.pi / 7.0))
    frame["day_cos"] = np.cos(frame["day_of_week"] * (2.0 * np.pi / 7.0))

    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)

    frame["rsi_14"] = _rsi(close, length=14)
    frame = pd.concat([frame, _macd(close, fast=12, slow=26, signal=9)], axis=1)
    frame = pd.concat([frame, _bbands(close, length=20, std=2.0)], axis=1)
    frame["atr_14"] = _atr(high, low, close, length=14)

    frame["log_return_1"] = np.log(close / close.shift(1))
    frame["log_return_5"] = np.log(close / close.shift(5))
    frame["gemini_macro"] = 1.0

    frame = frame.dropna().reset_index(drop=True)
    frame = frame.drop(columns=["hour_of_day", "day_of_week"])
    return frame


def main():
    print("Starting Quant Feature Engineering Pipeline...")
    raw_files = glob.glob(os.path.join(DATA_DIR, "*_15Min_train.csv"))
    if not raw_files:
        print("No raw 15Min CSV files found. Please run download_training_data.py first.")
        return

    for file in raw_files:
        if "_engineered.csv" in file:
            continue
        ticker = os.path.basename(file).split("_15Min")[0]
        print(f"Engineering features for {ticker}...")
        df = pd.read_csv(file)
        df = engineer_features(df)
        out_path = os.path.join(DATA_DIR, f"{ticker}_15Min_engineered.csv")
        df.to_csv(out_path, index=False)
        print(f"Saved engineered dataset: {len(df.columns)} Features | {len(df)} Timesteps")


if __name__ == "__main__":
    main()
