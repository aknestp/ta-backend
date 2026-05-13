from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, text
import pandas as pd
import requests
import os
from datetime import datetime
import threading

# ======================================
# CONFIG
# ======================================
app = Flask(__name__)
CORS(app)

# Pastikan environment variable ini sudah di-set di Supabase / terminal kamu
# Atau ganti dengan string aslinya sementara untuk testing

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:JjwcORoMcbMipdgeYjsLFfzzBQirWmBG@postgres.railway.internal:5432/railway")
FONNTE_TOKEN = os.getenv("FONNTE_TOKEN", "EwbABvWy3izcwCFbByiC")

engine = create_engine(DATABASE_URL)

# ======================================
# CREATE TABLES (Biarkan seperti aslimu)
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
        conn.commit()

create_tables()

# ======================================
# WHATSAPP (Dengan Threading agar ESP32 tidak antre)
# ======================================
def kirim_whatsapp(nomor, pesan):
    try:
        requests.post(
            "https://api.fonnte.com/send",
            headers={"Authorization": FONNTE_TOKEN},
            data={"target": nomor, "message": pesan},
            timeout=10
        )
        print(f"[WA] Terkirim ke {nomor}")
    except Exception as e:
        print(f"[WA ERROR] {e}")

# Fungsi helper untuk broadcast WA ke semua user di background
def broadcast_wa_background(pesan):
    try:
        users = pd.read_sql("SELECT * FROM users", engine)
        for _, user in users.iterrows():
            # Jika nomor_wa ada, kirim
            if user["nomor_wa"]:
                kirim_whatsapp(user["nomor_wa"], pesan)
    except Exception as e:
        print(f"[BROADCAST ERROR] {e}")

# ======================================
# GET LAST STATUS
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
# ENDPOINT UNTUK ESP32 (MENERIMA DATA)
# ======================================
@app.route('/dataset', methods=['POST'])
def dataset():
    try:
        data = request.json
        rms = float(data.get("rms", 0))
        status = int(data.get("status", 0))
        now = datetime.now()
        
        last_status = get_last_status()

        # 1. SAVE SENSOR DATA
        df = pd.DataFrame([{
            "timestamp": now,
            "rms": rms,
            "status": status
        }])
        df.to_sql("sensor_data", engine, if_exists="append", index=False)

        # 2. LOGIKA AIR BARU DATANG (MATI -> NYALA)
        if status == 1 and last_status == 0:
            pesan = f"🚰 INFORMASI DISTRIBUSI AIR\n\nAir mulai mengalir\n\nHari:\n{now.strftime('%A')}\n\nPukul:\n{now.strftime('%H:%M:%S')} WIB"
            
            # Kirim WA di Background (tidak membebani ESP32)
            threading.Thread(target=broadcast_wa_background, args=(pesan,)).start()

            # Save History Start
            history_df = pd.DataFrame([{
                "tanggal": now.date(),
                "jam_mulai": now.strftime("%H:%M:%S"),
                "jam_selesai": "-",
                "durasi": "-",
                "status": "Mengalir"
            }])
            history_df.to_sql("distribution_history", engine, if_exists="append", index=False)

        # 3. LOGIKA AIR BERHENTI (NYALA -> MATI)
        elif status == 0 and last_status == 1:
            jam_selesai = now.strftime("%H:%M:%S")
            
            # Update data history terakhir yang belum selesai menggunakan SQLAlchemy
            with engine.connect() as conn:
                # Ambil data yang jam selesainya masih "-"
                query = text("""
                    SELECT id, jam_mulai FROM distribution_history 
                    WHERE jam_selesai = '-' ORDER BY id DESC LIMIT 1
                """)
                result = conn.execute(query).fetchone()
                
                if result:
                    history_id = result[0]
                    jam_mulai_str = result[1]
                    
                    # Hitung durasi (selisih waktu)
                    try:
                        format_jam = "%H:%M:%S"
                        waktu_mulai = datetime.strptime(jam_mulai_str, format_jam)
                        waktu_selesai = datetime.strptime(jam_selesai, format_jam)
                        durasi = str(waktu_selesai - waktu_mulai) # Contoh output: "02:15:30"
                    except:
                        durasi = "Selesai"

                    # Update database
                    update_query = text("""
                        UPDATE distribution_history 
                        SET jam_selesai = :jam_selesai, durasi = :durasi, status = 'Selesai' 
                        WHERE id = :id
                    """)
                    conn.execute(update_query, {"jam_selesai": jam_selesai, "durasi": durasi, "id": history_id})
                    conn.commit()

        return jsonify({"message": "data tersimpan"}), 200

    except Exception as e:
        print(f"[DATASET ERROR] {e}")
        return jsonify({"error": str(e)}), 500

# ======================================
# ENDPOINT UNTUK STREAMLIT (DASHBOARD)
# ======================================
@app.route('/api/dashboard_data', methods=['GET'])
def dashboard_data():
    try:
        # Data Grafik
        chart_df = pd.read_sql("SELECT rms FROM sensor_data ORDER BY id DESC LIMIT 20", engine)
        chart_data = chart_df.iloc[::-1].to_dict(orient='records') if not chart_df.empty else [{"rms": 0}]

        # Data Sensor Terakhir
        latest_df = pd.read_sql("SELECT * FROM sensor_data ORDER BY id DESC LIMIT 1", engine)
        if not latest_df.empty:
            latest = latest_df.iloc[0]
            status_angka = int(latest["status"])
            waktu_deteksi = str(latest["timestamp"])
            sensor_status = "Online"
            status_text = "🟢 AIR MENGALIR" if status_angka == 1 else "🔴 AIR TIDAK MENGALIR"
        else:
            waktu_deteksi = "-"
            sensor_status = "Offline"
            status_text = "🔴 AIR TIDAK MENGALIR"

        # Data History
        history_df = pd.read_sql("SELECT * FROM distribution_history ORDER BY id DESC LIMIT 10", engine)
        history_data = history_df.to_dict(orient='records') if not history_df.empty else []

        # Ringkasan Distribusi Terakhir
        last_water_time = "-"
        duration = "-"
        if not history_df.empty:
            latest_hist = history_df.iloc[0]
            last_water_time = f"{latest_hist['tanggal']} {latest_hist['jam_mulai']}"
            duration = latest_hist['durasi'] if latest_hist['durasi'] != "-" else "Sedang Mengalir..."

        return jsonify({
            "latest_data": {
                "time": waktu_deteksi,
                "last_water_time": last_water_time,
                "duration": duration
            },
            "history_data": history_data,
            "chart_data": chart_data,
            "status_text": status_text,
            "sensor_status": sensor_status
        }), 200

    except Exception as e:
        print(f"[DASHBOARD ERROR] {e}")
        return jsonify({"error": "Gagal mengambil data"}), 500

# ======================================
# SEND WARNING (TOMBOL STREAMLIT)
# ======================================
@app.route('/send_warning', methods=['POST'])
def send_warning():
    try:
        data = request.json
        message = data.get("message", "Distribusi air mengalami gangguan sementara")
        # Kirim WA di Background
        threading.Thread(target=broadcast_wa_background, args=(message,)).start()
        
        return jsonify({"message": "warning terkirim"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================
# HEALTH CHECK
# ======================================
@app.route('/')
def home():
    return jsonify({"message": "Backend aktif di Port 8000"})

# ======================================
# RUN (PORT 8000 AGAR COCOK DENGAN STREAMLIT)
# ======================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
