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
    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE sensor_data ADD COLUMN IF NOT EXISTS id SERIAL;"))
        except: pass
        try:
            conn.execute(text("ALTER TABLE distribution_history ADD COLUMN IF NOT EXISTS id SERIAL;"))
        except: pass

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
        requests.post(
            "https://api.fonnte.com/send",
            headers={"Authorization": FONNTE_TOKEN},
            data={"target": target, "message": pesan}
        )
    except Exception as e:
        print(f"Gagal kirim WA: {e}")

# ======================================
# MAIN LOGIC (SAVE DATA, ML, & HISTORY)
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

        # 1. PREDIKSI
        if MODEL_SIAP:
            prediksi = model_ml.predict([[ax, ay, az, mean, rms]])[0]
            status = int(prediksi)
        else:
            status = 0 

        tz_wib = pytz.timezone('Asia/Jakarta')
        now_wib = datetime.now(tz_wib)
        now = now_wib.replace(tzinfo=None) 

        # 2. SAVE SENSOR DATA (Disimpan terus untuk grafik)
        df = pd.DataFrame([{
            "timestamp": now, "ax": ax, "ay": ay, "az": az, "mean": mean, "rms": rms, "status": status
        }])
        df.to_sql("sensor_data", engine, if_exists="append", index=False)

        # 3. LOGIKA RIWAYAT (SATU MASA PENGALIRAN / ANTI-FLICKER)
        with engine.connect() as conn:
            hist_df = pd.read_sql("SELECT * FROM distribution_history ORDER BY id DESC LIMIT 1", conn)

        if hist_df.empty:
            last_hist_id = None
            last_hist_status = "Selesai"
            last_jam_selesai = "-"
            last_tanggal = None
        else:
            last_hist_id = hist_df.iloc[0]['id']
            last_hist_status = hist_df.iloc[0]['status']
            last_jam_selesai = hist_df.iloc[0]['jam_selesai']
            last_tanggal = hist_df.iloc[0]['tanggal']

        # KONDISI: AIR MENGALIR (1)
        if status == 1:
            if last_hist_status == "Selesai" or last_hist_id is None:
                is_flicker = False
                
                # Cek jika air mati lalu nyala lagi dalam waktu kurang dari 5 Menit
                if last_jam_selesai != "-" and last_tanggal is not None:
                    try:
                        dt_str = f"{last_tanggal} {last_jam_selesai}"
                        last_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                        last_dt = tz_wib.localize(last_dt)
                        selisih_detik = (now_wib - last_dt).total_seconds()
                        
                        if 0 <= selisih_detik <= 300: # 300 detik = 5 Menit
                            is_flicker = True
                    except:
                        pass
                
                if is_flicker:
                    # GABUNGKAN SESI: Buka kembali riwayat sebelumnya, JANGAN kirim WA
                    with engine.begin() as conn:
                        conn.execute(text(f"""
                            UPDATE distribution_history 
                            SET jam_selesai = '-', status = 'Mengalir'
                            WHERE id = {last_hist_id}
                        """))
                else:
                    # SESI BARU: Buat Riwayat Baru & Kirim Notifikasi WA
                    pesan = f"🚰 INFORMASI DISTRIBUSI AIR\n\nAir mulai mengalir di Desa Lubuk Raman.\n\nHari: {now_wib.strftime('%A')}\nPukul: {now_wib.strftime('%H:%M:%S')} WIB"
                    kirim_whatsapp(TARGET_GRUP, pesan)

                    new_hist = pd.DataFrame([{
                        "tanggal": now_wib.date(),
                        "jam_mulai": now_wib.strftime("%H:%M:%S"),
                        "jam_selesai": "-",
                        "durasi": "-",
                        "status": "Mengalir"
                    }])
                    new_hist.to_sql("distribution_history", engine, if_exists="append", index=False)

        # KONDISI: AIR BERHENTI (0)
        elif status == 0:
            if last_hist_status == "Mengalir" and last_hist_id is not None:
                jam_selesai_sekarang = now_wib.strftime("%H:%M:%S")
                with engine.begin() as conn:
                    conn.execute(text(f"""
                        UPDATE distribution_history 
                        SET jam_selesai = '{jam_selesai_sekarang}', status = 'Selesai'
                        WHERE id = {last_hist_id}
                    """))

        # 4. AUTO CLEANUP (Sisakan 10.000 data agar server tidak penuh)
        with engine.begin() as conn:
            conn.execute(text("""
                DELETE FROM sensor_data 
                WHERE id NOT IN (
                    SELECT id FROM sensor_data ORDER BY id DESC LIMIT 10000
                )
            """))

        pesan_status = "Air Mengalir" if status == 1 else "Pipa Kosong"
        return jsonify({"message": "Data berhasil diproses ML", "prediksi_status": status, "keterangan": pesan_status})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ======================================
# ROUTES LAINNYA
# ======================================
@app.route('/latest')
def latest():
    try:
        df = pd.read_sql("SELECT * FROM sensor_data ORDER BY id DESC LIMIT 1", engine)
        if len(df) == 0:
            return jsonify({"status": 0, "time": "-", "last_water_time": "-", "duration": "-"})
        latest = df.iloc[0]
        return jsonify({
            "status": int(latest["status"]), "time": str(latest["timestamp"]), 
            "last_water_time": str(latest["timestamp"]), "duration": "Aktif" if int(latest["status"]) == 1 else "Nonaktif"
        })
    except Exception as e: return jsonify({"error": str(e)})

@app.route('/chart')
def chart():
    try:
        df = pd.read_sql("SELECT * FROM sensor_data ORDER BY id DESC LIMIT 50", engine)
        if len(df) == 0: return jsonify([])
        df = df.sort_values("id")
        df['timestamp'] = df['timestamp'].astype(str)
        return jsonify(df.to_dict(orient='records'))
    except Exception as e: return jsonify([])

@app.route('/history')
def history():
    try:
        df = pd.read_sql("SELECT * FROM distribution_history ORDER BY id DESC LIMIT 20", engine)
        if len(df) == 0: return jsonify([])
        df['tanggal'] = df['tanggal'].astype(str)
        return jsonify(df.to_dict(orient='records'))
    except Exception as e: return jsonify([])

@app.route('/send_warning', methods=['POST'])
def send_warning():
    try:
        data = request.json
        message = data.get("message", "Distribusi air mengalami gangguan sementara")
        kirim_whatsapp(TARGET_GRUP, message)
        return jsonify({"message": "warning terkirim ke grup"})
    except Exception as e: return jsonify({"error": str(e)})

@app.route('/')
def home():
    status_ml = "Aktif" if MODEL_SIAP else "Error/Belum Dimuat"
    return jsonify({"message": "Backend Early Warning System Aktif", "status_machine_learning": status_ml})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
