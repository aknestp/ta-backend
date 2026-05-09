from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine
import pandas as pd
import requests
import os
from datetime import datetime

# ======================================
# FLASK CONFIG
# ======================================
app = Flask(__name__)
CORS(app)

# ======================================
# DATABASE CONFIG
# ======================================
DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)

# ======================================
# FONNTE WHATSAPP CONFIG
# ======================================
FONNTE_TOKEN = os.getenv("FONNTE_TOKEN")

# ======================================
# CREATE TABLE IF NOT EXISTS
# ======================================
def create_tables():

    with engine.connect() as conn:

        # =========================
        # SENSOR DATA
        # =========================
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP,
            rms FLOAT,
            status INTEGER
        );
        """)

        # =========================
        # RIWAYAT DISTRIBUSI
        # =========================
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

create_tables()

# ======================================
# SEND WHATSAPP
# ======================================
def kirim_whatsapp(nomor, pesan):

    try:

        response = requests.post(
            "https://api.fonnte.com/send",
            headers={
                "Authorization": FONNTE_TOKEN
            },
            data={
                "target": nomor,
                "message": pesan
            }
        )

        return response.status_code

    except Exception as e:
        print("ERROR WHATSAPP:", e)
        return 500

# ======================================
# STATUS SEBELUMNYA
# ======================================
def get_last_status():

    try:

        query = """
        SELECT status
        FROM sensor_data
        ORDER BY id DESC
        LIMIT 1
        """

        df = pd.read_sql(query, engine)

        if len(df) == 0:
            return 0

        return int(df.iloc[0]["status"])

    except:
        return 0

# ======================================
# SIMPAN DATA SENSOR
# ======================================
@app.route('/dataset', methods=['POST'])
def dataset():

    try:

        data = request.json

        rms = float(data.get("rms", 0))
        status = int(data.get("status", 0))

        timestamp = datetime.now()

        last_status = get_last_status()

        # =========================
        # SIMPAN KE DATABASE
        # =========================
        df = pd.DataFrame([{
            "timestamp": timestamp,
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
        # EARLY WARNING
        # =========================
        if status == 1 and last_status == 0:

            users = pd.read_sql(
                "SELECT * FROM users",
                engine
            )

            pesan = f"""
🚰 INFORMASI DISTRIBUSI AIR

Air mulai mengalir
Hari: {timestamp.strftime('%A')}
Pukul: {timestamp.strftime('%H:%M:%S')} WIB

Pantau dashboard:
https://ta-dashboard-production.up.railway.app
"""

            for _, user in users.iterrows():

                nomor = user["nomor_wa"]

                kirim_whatsapp(
                    nomor,
                    pesan
                )

        return jsonify({
            "message": "data tersimpan"
        })

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

# ======================================
# DATA STATUS TERBARU
# ======================================
@app.route('/latest')
def latest():

    try:

        query = """
        SELECT *
        FROM sensor_data
        ORDER BY id DESC
        LIMIT 1
        """

        df = pd.read_sql(query, engine)

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
        }), 500

# ======================================
# DATA GRAFIK RMS
# ======================================
@app.route('/chart')
def chart():

    try:

        query = """
        SELECT *
        FROM sensor_data
        ORDER BY id DESC
        LIMIT 50
        """

        df = pd.read_sql(query, engine)

        df = df.sort_values("id")

        return jsonify(
            df.to_dict(orient='records')
        )

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

# ======================================
# RIWAYAT DISTRIBUSI
# ======================================
@app.route('/history')
def history():

    try:

        query = """
        SELECT *
        FROM sensor_data
        WHERE status = 1
        ORDER BY id DESC
        LIMIT 20
        """

        df = pd.read_sql(query, engine)

        history_data = []

        for _, row in df.iterrows():

            waktu = pd.to_datetime(row["timestamp"])

            history_data.append({
                "Hari": waktu.strftime("%A"),
                "Jam Mulai": waktu.strftime("%H:%M:%S"),
                "Jam Selesai": "-",
                "Durasi": "-",
                "Status": "Mengalir"
            })

        return jsonify(history_data)

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

# ======================================
# WARNING MANUAL OPERATOR
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

            nomor = user["nomor_wa"]

            kirim_whatsapp(
                nomor,
                message
            )

        return jsonify({
            "message": "warning terkirim"
        })

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

# ======================================
# TAMBAH USER
# ======================================
@app.route('/add_user', methods=['POST'])
def add_user():

    try:

        data = request.json

        nama = data.get("nama")
        nomor_wa = data.get("nomor_wa")
        jalur = data.get("jalur")

        df = pd.DataFrame([{
            "nama": nama,
            "nomor_wa": nomor_wa,
            "jalur": jalur
        }])

        df.to_sql(
            "users",
            engine,
            if_exists="append",
            index=False
        )

        return jsonify({
            "message": "user berhasil ditambahkan"
        })

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500

# ======================================
# ROOT
# ======================================
@app.route('/')
def home():

    return jsonify({
        "message": "Backend Early Warning System Aktif"
    })

# ======================================
# RUN
# ======================================
if __name__ == '__main__':

    app.run(
        host='0.0.0.0',
        port=5000
    )
