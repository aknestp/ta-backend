from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine
import pandas as pd
import requests
import os
from datetime import datetime

# ======================================
# CONFIG
# ======================================
app = Flask(__name__)
CORS(app)

DATABASE_URL = os.getenv("DATABASE_URL")
FONNTE_TOKEN = os.getenv("FONNTE_TOKEN")

engine = create_engine(DATABASE_URL)

# ======================================
# CREATE TABLES
# ======================================
def create_tables():

    with engine.connect() as conn:

        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP,
            rms FLOAT,
            status INTEGER
        );
        """)

        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS distribution_history (
            id SERIAL PRIMARY KEY,
            tanggal DATE,
            jam_mulai VARCHAR(20),
            jam_selesai VARCHAR(20),
            durasi VARCHAR(50),
            status VARCHAR(20)
        );
        """)

        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            nama VARCHAR(100),
            nomor_wa VARCHAR(20)
        );
        """)

create_tables()

# ======================================
# WHATSAPP
# ======================================
def kirim_whatsapp(nomor, pesan):

    try:

        requests.post(
            "https://api.fonnte.com/send",
            headers={
                "Authorization": FONNTE_TOKEN
            },
            data={
                "target": nomor,
                "message": pesan
            }
        )

    except Exception as e:
        print(e)

# ======================================
# GET LAST STATUS
# ======================================
def get_last_status():

    try:

        df = pd.read_sql("""
        SELECT *
        FROM sensor_data
        ORDER BY id DESC
        LIMIT 1
        """, engine)

        if len(df) == 0:
            return 0

        return int(df.iloc[0]["status"])

    except:
        return 0

# ======================================
# SAVE SENSOR DATA
# ======================================
@app.route('/dataset', methods=['POST'])
def dataset():

    try:

        data = request.json

        rms = float(data.get("rms", 0))
        status = int(data.get("status", 0))

        now = datetime.now()

        last_status = get_last_status()

        # =========================
        # SAVE SENSOR DATA
        # =========================
        df = pd.DataFrame([{
            "timestamp": now,
            "rms": rms,
            "status": status
        }])

        df.to_sql(
            "sensor_data",
            engine,
            if_exists="append",
            index=False
        )

        # =========================
        # AIR BARU DATANG
        # =========================
        if status == 1 and last_status == 0:

            users = pd.read_sql(
                "SELECT * FROM users",
                engine
            )

            pesan = f"""
🚰 INFORMASI DISTRIBUSI AIR

Air mulai mengalir

Hari:
{now.strftime('%A')}

Pukul:
{now.strftime('%H:%M:%S')} WIB
"""

            for _, user in users.iterrows():

                kirim_whatsapp(
                    user["nomor_wa"],
                    pesan
                )

            # =========================
            # SAVE HISTORY START
            # =========================
            history_df = pd.DataFrame([{
                "tanggal": now.date(),
                "jam_mulai": now.strftime("%H:%M:%S"),
                "jam_selesai": "-",
                "durasi": "-",
                "status": "Mengalir"
            }])

            history_df.to_sql(
                "distribution_history",
                engine,
                if_exists="append",
                index=False
            )

        return jsonify({
            "message": "data tersimpan"
        })

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

# ======================================
# LATEST STATUS
# ======================================
@app.route('/latest')
def latest():

    try:

        df = pd.read_sql("""
        SELECT *
        FROM sensor_data
        ORDER BY id DESC
        LIMIT 1
        """, engine)

        if len(df) == 0:

            return jsonify({
                "status": 0,
                "time": "-",
                "last_water_time": "-",
                "duration": "-"
            })

        latest = df.iloc[0]

        return jsonify({
            "status": int(latest["status"]),
            "time": str(latest["timestamp"]),
            "last_water_time": str(latest["timestamp"]),
            "duration": "Aktif"
        })

    except Exception as e:

        return jsonify({
            "error": str(e)
        })

# ======================================
# CHART DATA
# ======================================
@app.route('/chart')
def chart():

    try:

        df = pd.read_sql("""
        SELECT *
        FROM sensor_data
        ORDER BY id DESC
        LIMIT 50
        """, engine)

        if len(df) == 0:
            return jsonify([])

        df = df.sort_values("id")

        return jsonify(
            df.to_dict(orient='records')
        )

    except Exception as e:

        return jsonify([])

# ======================================
# HISTORY
# ======================================
@app.route('/history')
def history():

    try:

        df = pd.read_sql("""
        SELECT *
        FROM distribution_history
        ORDER BY id DESC
        LIMIT 20
        """, engine)

        if len(df) == 0:
            return jsonify([])

        return jsonify(
            df.to_dict(orient='records')
        )

    except Exception as e:

        return jsonify([])

# ======================================
# SEND WARNING
# ======================================
@app.route('/send_warning', methods=['POST'])
def send_warning():

    try:

        data = request.json

        message = data.get(
            "message",
            "Distribusi air mengalami gangguan sementara"
        )

        users = pd.read_sql(
            "SELECT * FROM users",
            engine
        )

        for _, user in users.iterrows():

            kirim_whatsapp(
                user["nomor_wa"],
                message
            )

        return jsonify({
            "message": "warning terkirim"
        })

    except Exception as e:

        return jsonify({
            "error": str(e)
        })

# ======================================
# HOME
# ======================================
@app.route('/')
def home():

    return jsonify({
        "message": "Backend aktif"
    })

# ======================================
# RUN
# ======================================
if __name__ == '__main__':

    app.run(
        host='0.0.0.0',
        port=5000
    )
