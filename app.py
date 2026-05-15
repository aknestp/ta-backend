from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, text
import pandas as pd
import requests
import os
from datetime import datetime
import threading
import joblib  # <--- IMPORT JOBLIB UNTUK MACHINE LEARNING

# ======================================
# FLASK CONFIG
# ======================================
app = Flask(__name__)
CORS(app)

# ======================================
# DATABASE & API CONFIG
# ======================================
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
FONNTE_TOKEN = os.getenv("FONNTE_TOKEN")

# ======================================
# LOAD MACHINE LEARNING MODEL
# ======================================
# Ganti 'model_air.pkl' dengan nama file model aslimu!
MODEL_PATH = "model_air.pkl" 

try:
    model_ml = joblib.load(MODEL_PATH)
    print(f"[SISTEM] Model Machine Learning '{MODEL_PATH}' berhasil dimuat!")
except Exception as e:
    print(f"[ERROR] Gagal memuat model ML: {e}")
    model_ml = None

# ======================================
# CREATE TABLE IF NOT EXISTS
# ======================================
def create_tables():
    with engine.connect() as conn:
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id SERIAL PRIMARY KEY,
            waktu TIMESTAMP,
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
            nomor_wa VARCHAR(20),
            jalur VARCHAR(50)
        );
        """)
        conn.commit()

create_tables()

# ======================================
# SEND WHATSAPP (DENGAN THREADING)
# ======================================
def kirim_whatsapp(nomor, pesan):
    try:
        requests.post(
            "https://api.fonnte.com/send",
            headers={"Authorization": FONNTE_TOKEN},
            data={"target": nomor, "message": pesan},
            timeout=10
        )
    except Exception as e:
        print("ERROR WHATSAPP:", e)

def broadcast_whatsapp_background(pesan):
    try:
        users = pd.read_sql("SELECT * FROM users", engine)
        for _, user in users.iterrows():
            nomor = user["nomor_wa"]
            if nomor:
                kirim_whatsapp(nomor, pesan)
    except Exception as e:
        print("ERROR BROADCAST:", e)

# ======================================
# STATUS SEBELUMNYA
# ======================================
def get_last_status():
    try:
        df = pd.read_sql("SELECT status FROM sensor_data ORDER BY id DESC LIMIT 1", engine)
        if len(df) == 0:
            return 0
        return int(df.iloc[0]["status"])
    except:
        return 0

# ======================================
# SIMPAN DATA SENSOR & PREDIKSI ML
# ======================================
@app.route('/dataset', methods=['POST'])
def dataset():
    try:
        data = request.json
        
        # ESP32 HANYA PERLU MENGIRIM RMS SAJA
        rms = float(data.get("rms", 0))
        
        # ==========================================
        # MACHINE LEARNING INFERENCE
        # ==========================================
        if model_ml is not None:
            # Scikit-learn membutuhkan input berupa array 2 Dimensi, contoh: [[0.45]]
            prediksi = model_ml.predict([[rms]])
            status = int(prediksi[0])  # Hasilnya 1 (Mengalir) atau 0 (Mati)
        else:
            # Fallback (cadangan) jika file .pkl gagal dimuat / tidak ditemukan
            status = int(data.get("status", 0))

        timestamp = datetime.now()
        last_status = get_last_status()

        # 1. SIMPAN KE DATABASE
        df = pd.DataFrame([{
            "waktu": timestamp,
            "rms": rms,
            "status": status
        }])
        df.to_sql("sensor_data", engine, if_exists="append", index=False)

        # 2. LOGIKA AIR BARU DATANG (MATI -> MENGALIR)
        if status == 1 and last_status == 0:
            pesan = f"""🚰 INFORMASI DISTRIBUSI AIR

Air mulai mengalir
Hari: {timestamp.strftime('%A')}
Pukul: {timestamp.strftime('%H:%M:%S')} WIB

Pantau dashboard:
https://ta-dashboard-production.up.railway.app
"""
            # Kirim WA di Background
            threading.Thread(target=broadcast_whatsapp_background, args=(pesan,)).start()

            # Catat Jam Mulai
            history_df = pd.DataFrame([{
                "tanggal": timestamp.date(),
                "jam_mulai": timestamp.strftime("%H:%M:%S"),
                "jam_selesai": "-",
                "durasi": "-",
                "status": "Mengalir"
            }])
            history_df.to_sql("distribution_history", engine, if_exists="append", index=False)

        # 3. LOGIKA AIR BERHENTI (MENGALIR -> MATI)
        elif status == 0 and last_status == 1:
            jam_selesai = timestamp.strftime("%H:%M:%S")
            
            with engine.connect() as conn:
                res = conn.execute(text("SELECT id, jam_mulai FROM distribution_history WHERE jam_selesai = '-' ORDER BY id DESC LIMIT 1")).fetchone()
                if res:
                    hist_id, j_mulai = res[0], res[1]
                    try:
                        fmt = "%H:%M:%S"
                        t_delta = datetime.strptime(jam_selesai, fmt) - datetime.strptime(j_mulai, fmt)
                        durasi = str(t_delta)
                    except:
                        durasi = "Selesai"
                    
                    conn.execute(text("UPDATE distribution_history SET jam_selesai=:js, durasi=:dur, status='Berhenti' WHERE id=:id"), 
                                 {"js": jam_selesai, "dur": durasi, "id": hist_id})
                    conn.commit()

        return jsonify({"message": "data tersimpan, diprediksi oleh ML", "prediksi_status": status})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================
# DATA STATUS TERBARU
# ======================================
@app.route('/latest')
def latest():
    try:
        df = pd.read_sql("SELECT * FROM sensor_data ORDER BY id DESC LIMIT 1", engine)
        if len(df) == 0:
            return jsonify({"status": 0, "time": "-", "last_water_time": "-", "duration": "-"})
        latest = df.iloc[0]

        hist_df = pd.read_sql("SELECT * FROM distribution_history ORDER BY id DESC LIMIT 1", engine)
        last_water_time = "-"
        duration = "-"
        
        if len(hist_df) > 0:
            hist = hist_df.iloc[0]
            last_water_time = f"{hist['tanggal']} {hist['jam_mulai']}"
            duration = hist['durasi'] if hist['durasi'] != "-" else "Sedang Mengalir"

        return jsonify({
            "status": int(latest["status"]),
            "time": str(latest["waktu"]),
            "last_water_time": last_water_time,
            "duration": duration
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================
# DATA GRAFIK RMS
# ======================================
@app.route('/chart')
def chart():
    try:
        df = pd.read_sql("SELECT rms FROM sensor_data ORDER BY id DESC LIMIT 50", engine)
        df = df.sort_index(ascending=False) 
        return jsonify(df.to_dict(orient='records'))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================
# RIWAYAT DISTRIBUSI
# ======================================
@app.route('/history')
def history():
    try:
        df = pd.read_sql("SELECT * FROM distribution_history ORDER BY id DESC LIMIT 20", engine)
        history_data = []
        for _, row in df.iterrows():
            history_data.append({
                "Tanggal": str(row["tanggal"]),
                "Jam Mulai": row["jam_mulai"],
                "Jam Selesai": row["jam_selesai"],
                "Durasi": row["durasi"],
                "Status": row["status"]
            })
        return jsonify(history_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================
# WARNING MANUAL OPERATOR
# ======================================
@app.route('/send_warning', methods=['POST'])
def send_warning():
    try:
        data = request.json
        message = data.get("message", "Distribusi air mengalami gangguan sementara")
        threading.Thread(target=broadcast_whatsapp_background, args=(message,)).start()
        return jsonify({"message": "warning terkirim"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================
# TAMBAH USER
# ======================================
@app.route('/add_user', methods=['POST'])
def add_user():
    try:
        data = request.json
        df = pd.DataFrame([{
            "nama": data.get("nama"),
            "nomor_wa": data.get("nomor_wa"),
            "jalur": data.get("jalur")
        }])
        df.to_sql("users", engine, if_exists="append", index=False)
        return jsonify({"message": "user berhasil ditambahkan"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================
# ROOT
# ======================================
@app.route('/')
def home():
    return jsonify({"message": "Backend Early Warning System (dengan Machine Learning) Aktif"})

# ======================================
# RUN
# ======================================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
