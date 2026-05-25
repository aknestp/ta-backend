from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine
import pandas as pd
import requests
import os
from datetime import datetime
import pytz
import joblib  # Tambahan untuk memuat model Machine Learning

# ======================================
# CONFIG
# ======================================
app = Flask(__name__)
CORS(app)

DATABASE_URL = os.getenv("DATABASE_URL")
FONNTE_TOKEN = os.getenv("FONNTE_TOKEN")

engine = create_engine(DATABASE_URL)

# ======================================
# LOAD MACHINE LEARNING MODEL
# ======================================
# Flask akan memuat model ini satu kali saat server pertama kali menyala.
# Ganti 'model_getaran.pkl' dengan nama file model aslimu!
try:
    model_ml = joblib.load('model_getaran.pkl')
    MODEL_SIAP = True
    print("Model Machine Learning berhasil dimuat!")
except Exception as e:
    print(f"Gagal memuat model ML: {e}")
    MODEL_SIAP = False

# ======================================
# CREATE TABLES
# ======================================
def create_tables():
    with engine.connect() as conn:
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP,
            ax FLOAT,
            ay FLOAT,
            az FLOAT,
            mean FLOAT,
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
# SAVE SENSOR DATA & ML PREDICTION
# ======================================
@app.route('/dataset', methods=['POST'])
def dataset():
    try:
        data = request.json

        # 1. Menangkap data mentah dari ESP32 (tanpa status)
        ax = float(data.get("ax", 0))
        ay = float(data.get("ay", 0))
        az = float(data.get("az", 0))
        mean = float(data.get("mean", 0))
        rms = float(data.get("rms", 0))

        # 2. PREDIKSI MACHINE LEARNING
        if MODEL_SIAP:
            # Model menerima 5 fitur sesuai urutan saat kamu melatihnya
            prediksi = model_ml.predict([[ax, ay, az, mean, rms]])[0]
            status = int(prediksi)
        else:
            # Fallback jika model gagal diload agar server tidak crash
            status = 0 

        # 3. Penyesuaian Zona Waktu ke WIB
        tz_wib = pytz.timezone('Asia/Jakarta')
        now_wib = datetime.now(tz_wib)
        now = now_wib.replace(tzinfo=None) 

        last_status = get_last_status()

        # =========================
        # SAVE SENSOR DATA
        # =========================
        df = pd.DataFrame([{
            "timestamp": now,
            "ax": ax,
            "ay": ay,
            "az": az,
            "mean": mean,
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
        # AIR BARU DATANG (0 -> 1)
        # =========================
        if status == 1 and last_status == 0:
            users = pd.read_sql("SELECT * FROM users", engine)

            pesan = f"""🚰 INFORMASI DISTRIBUSI AIR

Air mulai mengalir di Desa Lubuk Raman.

Hari:
{now_wib.strftime('%A')}

Pukul:
{now_wib.strftime('%H:%M:%S')} WIB
"""
            for _, user in users.iterrows():
                kirim_whatsapp(user["nomor_wa"], pesan)

            # SAVE HISTORY START
            history_df = pd.DataFrame([{
                "tanggal": now_wib.date(),
                "jam_mulai": now_wib.strftime("%H:%M:%S"),
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

        # =========================
        # AIR BERHENTI (1 -> 0)
        # =========================
        elif status == 0 and last_status == 1:
            jam_selesai_sekarang = now_wib.strftime("%H:%M:%S")
            
            with engine.begin() as conn:
                conn.exec_driver_sql(f"""
                    UPDATE distribution_history 
                    SET jam_selesai = '{jam_selesai_sekarang}', status = 'Selesai'
                    WHERE id = (SELECT id FROM distribution_history ORDER BY id DESC LIMIT 1)
                """)
            
            users = pd.read_sql("SELECT * FROM users", engine)
            pesan_berhenti = f"""🚰 INFORMASI DISTRIBUSI AIR

Distribusi air di Desa Lubuk Raman telah berhenti mengalir.

Pukul:
{jam_selesai_sekarang} WIB
"""
            for _, user in users.iterrows():
                kirim_whatsapp(user["nomor_wa"], pesan_berhenti)

        # 4. KIRIM JAWABAN BALIK KE ESP32
        # Ini yang akan ditangkap oleh http.getString() di Serial Monitor ESP32
        pesan_status = "Air Mengalir" if status == 1 else "Pipa Kosong"
        return jsonify({
            "message": "Data berhasil diproses ML",
            "prediksi_status": status,
            "keterangan": pesan_status
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
            "duration": "Aktif" if int(latest["status"]) == 1 else "Nonaktif"
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
        df['timestamp'] = df['timestamp'].astype(str)

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
        
        df['tanggal'] = df['tanggal'].astype(str)

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
    status_ml = "Aktif" if MODEL_SIAP else "Error/Belum Dimuat"
    return jsonify({
        "message": "Backend Early Warning System Aktif",
        "status_machine_learning": status_ml
    })

# ======================================
# RUN
# ======================================
if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=5000
    )
