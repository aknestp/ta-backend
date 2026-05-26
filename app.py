from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine
import pandas as pd
import requests
import os
from datetime import datetime
import pytz
import joblib

# ======================================
# CONFIG
# ======================================
app = Flask(__name__)
CORS(app)

DATABASE_URL = os.getenv("DATABASE_URL")
FONNTE_TOKEN = os.getenv("FONNTE_TOKEN")
TARGET_GRUP = "120363408496098642@g.us" 

engine = create_engine(DATABASE_URL)

# ======================================
# LOAD MACHINE LEARNING MODEL
# ======================================
try:
    model_ml = joblib.load('model_rf.pkl') 
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

create_tables()

# ======================================
# WHATSAPP (KE GRUP)
# ======================================
def kirim_whatsapp(target, pesan):
    try:
        requests.post(
            "https://api.fonnte.com/send",
            headers={
                "Authorization": FONNTE_TOKEN
            },
            data={
                "target": target,
                "message": pesan
            }
        )
    except Exception as e:
        print(f"Gagal kirim WA: {e}")

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

        ax = float(data.get("ax", 0))
        ay = float(data.get("ay", 0))
        az = float(data.get("az", 0))
        mean = float(data.get("mean", 0))
        rms = float(data.get("rms", 0))

        # PREDIKSI MACHINE LEARNING
        if MODEL_SIAP:
            prediksi = model_ml.predict([[ax, ay, az, mean, rms]])[0]
            status = int(prediksi)
        else:
            status = 0 

        # WAKTU WIB
        tz_wib = pytz.timezone('Asia/Jakarta')
        now_wib = datetime.now(tz_wib)
        now = now_wib.replace(tzinfo=None) 

        last_status = get_last_status()

        # =========================
        # 1. SAVE SENSOR DATA
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

        df.to_sql("sensor_data", engine, if_exists="append", index=False)

        # =========================
        # 2. AIR BARU DATANG (0 -> 1)
        # =========================
        if status == 1 and last_status == 0:
            pesan = f"""🚰 INFORMASI DISTRIBUSI AIR

Air mulai mengalir di Desa Lubuk Raman.

Hari: {now_wib.strftime('%A')}
Pukul: {now_wib.strftime('%H:%M:%S')} WIB
"""
            kirim_whatsapp(TARGET_GRUP, pesan)

            history_df = pd.DataFrame([{
                "tanggal": now_wib.date(),
                "jam_mulai": now_wib.strftime("%H:%M:%S"),
                "jam_selesai": "-",
                "durasi": "-",
                "status": "Mengalir"
            }])

            history_df.to_sql("distribution_history", engine, if_exists="append", index=False)

        # =========================
        # 3. AIR BERHENTI (1 -> 0)
        # =========================
        elif status == 0 and last_status == 1:
            jam_selesai_sekarang = now_wib.strftime("%H:%M:%S")
            
            with engine.begin() as conn:
                conn.exec_driver_sql(f"""
                    UPDATE distribution_history 
                    SET jam_selesai = '{jam_selesai_sekarang}', status = 'Selesai'
                    WHERE id = (SELECT id FROM distribution_history ORDER BY id DESC LIMIT 1)
                """)
            
            pesan_berhenti = f"""🚰 INFORMASI DISTRIBUSI AIR

Distribusi air di Desa Lubuk Raman telah berhenti mengalir.

Pukul: {jam_selesai_sekarang} WIB
"""
            kirim_whatsapp(TARGET_GRUP, pesan_berhenti)

        # =========================
        # 4. AUTO CLEANUP (MENCEGAH DATABASE PENUH)
        # =========================
        with engine.begin() as conn:
            conn.exec_driver_sql("""
                DELETE FROM sensor_data 
                WHERE id NOT IN (
                    SELECT id FROM sensor_data ORDER BY id DESC LIMIT 10000
                )
            """)

        # RESPONS KE ESP32
        pesan_status = "Air Mengalir" if status == 1 else "Pipa Kosong"
        return jsonify({
            "message": "Data berhasil diproses ML",
            "prediksi_status": status,
            "keterangan": pesan_status
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================
# LATEST STATUS
# ======================================
@app.route('/latest')
def latest():
    try:
        df = pd.read_sql("SELECT * FROM sensor_data ORDER BY id DESC LIMIT 1", engine)
        if len(df) == 0:
            return jsonify({"status": 0, "time": "-", "last_water_time": "-", "duration": "-"})

        latest = df.iloc[0]
        return jsonify({
            "status": int(latest["status"]),
            "time": str(latest["timestamp"]),
            "last_water_time": str(latest["timestamp"]),
            "duration": "Aktif" if int(latest["status"]) == 1 else "Nonaktif"
        })
    except Exception as e:
        return jsonify({"error": str(e)})

# ======================================
# CHART DATA
# ======================================
@app.route('/chart')
def chart():
    try:
        df = pd.read_sql("SELECT * FROM sensor_data ORDER BY id DESC LIMIT 50", engine)
        if len(df) == 0:
            return jsonify([])
        df = df.sort_values("id")
        df['timestamp'] = df['timestamp'].astype(str)
        return jsonify(df.to_dict(orient='records'))
    except Exception as e:
        return jsonify([])

# ======================================
# HISTORY
# ======================================
@app.route('/history')
def history():
    try:
        df = pd.read_sql("SELECT * FROM distribution_history ORDER BY id DESC LIMIT 20", engine)
        if len(df) == 0:
            return jsonify([])
        df['tanggal'] = df['tanggal'].astype(str)
        return jsonify(df.to_dict(orient='records'))
    except Exception as e:
        return jsonify([])

# ======================================
# SEND WARNING (MANUAL DARI STREAMLIT)
# ======================================
@app.route('/send_warning', methods=['POST'])
def send_warning():
    try:
        data = request.json
        message = data.get("message", "Distribusi air mengalami gangguan sementara")
        kirim_whatsapp(TARGET_GRUP, message)
        return jsonify({"message": "warning terkirim ke grup"})
    except Exception as e:
        return jsonify({"error": str(e)})

# ======================================
# HOME
# ======================================
@app.route('/')
def home():
    status_ml = "Aktif" if MODEL_SIAP else "Error/Belum Dimuat"
    return jsonify({
        "message": "Backend Early Warning System Aktif",
        "status_machine_learning": status_ml,
        "database_limit": "10000 records max"
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
