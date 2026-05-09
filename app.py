from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import numpy as np
from datetime import datetime
import os

app = Flask(__name__)
CORS(app)

DATASET_FILE = "dataset.csv"

# =========================
# SIMPAN DATA SENSOR
# =========================
@app.route('/dataset', methods=['POST'])
def dataset():

    data = request.json

    rms = data.get("rms", 0)
    status = data.get("status", 0)

    now = datetime.now()

    row = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "rms": rms,
        "status": status
    }

    df = pd.DataFrame([row])

    if not os.path.exists(DATASET_FILE):
        df.to_csv(DATASET_FILE, index=False)
    else:
        df.to_csv(DATASET_FILE, mode='a', header=False, index=False)

    return jsonify({
        "message": "data tersimpan"
    })

# =========================
# STATUS TERBARU
# =========================
@app.route('/latest')
def latest():

    try:
        df = pd.read_csv(DATASET_FILE)

        latest = df.iloc[-1]

        return jsonify({
            "status": int(latest["status"]),
            "time": latest["timestamp"],
            "last_water_time": latest["timestamp"],
            "duration": "1 Jam"
        })

    except:
        return jsonify({
            "status": 0,
            "time": "-",
            "last_water_time": "-",
            "duration": "-"
        })

# =========================
# DATA GRAFIK
# =========================
@app.route('/chart')
def chart():

    try:
        df = pd.read_csv(DATASET_FILE)

        chart = df.tail(50)

        return jsonify(
            chart.to_dict(orient='records')
        )

    except:
        return jsonify([])

# =========================
# RIWAYAT DISTRIBUSI
# =========================
@app.route('/history')
def history():

    history_data = [
        {
            "Hari": "Senin",
            "Jam Mulai": "06:15",
            "Jam Selesai": "08:00",
            "Durasi": "1 Jam 45 Menit",
            "Status": "Selesai"
        }
    ]

    return jsonify(history_data)

# =========================
# WARNING OPERATOR
# =========================
@app.route('/send_warning', methods=['POST'])
def send_warning():

    data = request.json

    message = data.get("message")

    print("WARNING:", message)

    return jsonify({
        "message": "warning terkirim"
    })

# =========================
# RUN
# =========================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
