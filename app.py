from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, text
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
    model_ml = joblib.load('model_rf (1).pkl') 
    MODEL_SIAP = True
    print("Model Machine Learning berhasil dimuat!")
except Exception as e:
    print(f"Gagal memuat model ML: {e}")
    MODEL_SIAP = False

# ======================================
# CREATE TABLES (VERSI ANTI-ERROR)
# ======================================
def create_tables():
    with engine.begin() as conn:
        # Buat tabel jika belum ada
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS sensor_data (
            id SERIAL PRIMARY KEY, timestamp TIMESTAMP, ax FLOAT, ay FLOAT, az FLOAT, mean FLOAT, rms FLOAT, status INTEGER
        );
        """))
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS distribution_history (
            id SERIAL PRIMARY KEY, tanggal DATE, jam_mulai VARCHAR(20), jam_selesai VARCHAR(20), durasi VARCHAR(50), status VARCHAR(20)
        );
        """))
create_tables()

# ======================================
# WHATSAPP (KE GRUP)
# ======================================
def kirim_whatsapp(target, pesan):
    try:
        requests.post("https://api.fonnte.com/send", 
                      headers={"Authorization": FONNTE_TOKEN}, 
                      data={"target": target, "message": pesan})
    except Exception as e:
        print(f"Gagal kirim WA: {e}")

# ======================================
# MAIN LOGIC: DATASET & ML
# ======================================
@app.route('/dataset', methods=['POST'])
def dataset():
    try:
        data = request.json
        ax, ay, az = float(data.get("ax", 0)), float(data.get("ay", 0)), float(data.get("az", 0))
        mean, rms = float(data.get("mean", 0)), float(data.get("rms", 0))

        # 1. PREDIKSI ML
        status = int(model_ml.predict([[ax, ay, az, mean, rms]])[0]) if MODEL_SIAP else 0

        tz_wib = pytz.timezone('Asia/Jakarta')
        now_wib = datetime.now(tz_wib)
        now = now_wib.replace(tzinfo=None) 

        # 2. SAVE DATA SENSOR
        df = pd.DataFrame([{"timestamp": now, "ax": ax, "ay": ay, "az": az, "mean": mean, "rms": rms, "status": status}])
        df.to_sql("sensor_data", engine, if_exists="append", index=False)

        # 3. LOGIKA RIWAYAT (ANTI-FLICKER)
        with engine.connect() as conn:
            hist = pd.read_sql("SELECT * FROM distribution_history ORDER BY id DESC LIMIT 1", conn)

        if status == 1: # AIR MENGALIR
            if hist.empty or hist.iloc[0]['status'] == "Selesai":
                # CEK FLICKER (Jika mati < 5 menit, buka sesi lama)
                is_flicker = False
                if not hist.empty and hist.iloc[0]['status'] == "Selesai":
                    last_jam = hist.iloc[0]['jam_selesai']
                    last_tgl = str(hist.iloc[0]['tanggal'])
                    try:
                        last_dt = datetime.strptime(f"{last_tgl} {last_jam}", "%Y-%m-%d %H:%M:%S")
                        last_dt = tz_wib.localize(last_dt)
                        if (now_wib - last_dt).total_seconds() <= 120:
                            is_flicker = True
                    except: pass
                
                if is_flicker:
                    with engine.begin() as conn:
                        conn.execute(text(f"UPDATE distribution_history SET jam_selesai = '-', status = 'Mengalir' WHERE id = {hist.iloc[0]['id']}"))
                else:
                    pesan = f"🚰 INFORMASI DISTRIBUSI AIR\n\nAir mulai mengalir di Desa Lubuk Raman.\n{now_wib.strftime('%A, %H:%M:%S')} WIB. \n Pantau Distribusi: https://dashboard-serverair.up.railway.app/"
                    kirim_whatsapp(TARGET_GRUP, pesan)
                    pd.DataFrame([{"tanggal": now_wib.date(), "jam_mulai": now_wib.strftime("%H:%M:%S"), "jam_selesai": "-", "durasi": "-", "status": "Mengalir"}]).to_sql("distribution_history", engine, if_exists="append", index=False)

        elif status == 0 and not hist.empty and hist.iloc[0]['status'] == "Mengalir": # AIR BERHENTI
            jam_selesai_sekarang = now_wib.strftime("%H:%M:%S")
            
            # HITUNG DURASI
            fmt = "%H:%M:%S"
            mulai = datetime.strptime(hist.iloc[0]['jam_mulai'], fmt)
            selesai = datetime.strptime(jam_selesai_sekarang, fmt)
            durasi_detik = (selesai - mulai).total_seconds()
            # Mengonversi detik ke format HH:MM:SS
            durasi_str = str(pd.to_timedelta(durasi_detik, unit='s')).split()[-1] 
            
            with engine.begin() as conn:
                conn.execute(text(f"""
                    UPDATE distribution_history 
                    SET jam_selesai = '{jam_selesai_sekarang}', 
                        durasi = '{durasi_str}', 
                        status = 'Selesai' 
                    WHERE id = {hist.iloc[0]['id']}
                """))
                
        # 4. AUTO CLEANUP (LIMIT 10.000)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM sensor_data WHERE id NOT IN (SELECT id FROM sensor_data ORDER BY id DESC LIMIT 10000)"))

        return jsonify({"message": "OK", "status": status})
    except Exception as e: return jsonify({"error": str(e)}), 500

# ======================================
# ROUTES LAINNYA
# ======================================
@app.route('/latest')
def latest():
    try:
        df = pd.read_sql("SELECT * FROM sensor_data ORDER BY id DESC LIMIT 1", engine)
        if df.empty: return jsonify({"status": 0, "time": "-", "last_water_time": "-", "duration": "-"})
        latest = df.iloc[0]
        return jsonify({"status": int(latest["status"]), "time": str(latest["timestamp"]), "last_water_time": str(latest["timestamp"]), "duration": "Aktif" if int(latest["status"]) == 1 else "Nonaktif"})
    except: return jsonify({"error": "DB Error"})

@app.route('/chart')
def chart():
    try:
        df = pd.read_sql("SELECT * FROM sensor_data ORDER BY id DESC LIMIT 50", engine)
        if df.empty: return jsonify([])
        df = df.sort_values("id")
        df['timestamp'] = df['timestamp'].astype(str)
        return jsonify(df.to_dict(orient='records'))
    except: return jsonify([])

@app.route('/history')
def history():
    try:
        df = pd.read_sql("SELECT * FROM distribution_history ORDER BY id DESC LIMIT 20", engine)
        if df.empty: return jsonify([])
        df['tanggal'] = df['tanggal'].astype(str)
        return jsonify(df.to_dict(orient='records'))
    except: return jsonify([])

@app.route('/send_warning', methods=['POST'])
def send_warning():
    try:
        msg = request.json.get("message", "Gangguan sementara")
        kirim_whatsapp(TARGET_GRUP, msg)
        return jsonify({"message": "OK"})
    except: return jsonify({"error": "Gagal"})

@app.route('/')
def home():
    return jsonify({"message": "Backend OK"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
