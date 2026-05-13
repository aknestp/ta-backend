from flask import Flask, request, jsonify
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import threading

app = Flask(__name__)

# ==========================================
# KONFIGURASI
# ==========================================
FONNTE_TOKEN = "EwbABvWy3izcwCFbByiC"

# Ganti dengan Connection String PostgreSQL dari Supabase
DATABASE_URL = "postgresql://postgres:JjwcORoMcbMipdgeYjsLFfzzBQirWmBG@postgres.railway.internal:5432/railway"

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# ==========================================
# FUNGSI BACKGROUND: WHATSAPP BROADCAST
# ==========================================
def broadcast_whatsapp(pesan: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT nomor_wa FROM users")
        users = cursor.fetchall()
        conn.close()

        if not users:
            print("[SISTEM] Tidak ada nomor warga di tabel users.")
            return

        nomor_warga = [user['nomor_wa'] for user in users if user['nomor_wa']]
        target_numbers = ",".join(nomor_warga)

        url = "https://api.fonnte.com/send"
        headers = {"Authorization": FONNTE_TOKEN}
        data = {
            "target": target_numbers,
            "message": pesan
        }

        response = requests.post(url, headers=headers, data=data, timeout=10)
        print(f"[FONNTE] WA Terkirim ke {len(nomor_warga)} warga.")

    except Exception as e:
        print(f"[ERROR DB/WA] Gagal broadcast: {e}")

# ==========================================
# ENDPOINTS (API)
# ==========================================

# 1. Endpoint untuk Dashboard Streamlit
@app.route("/api/dashboard_data", methods=["GET"])
def get_dashboard_data():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. Ambil Data Grafik (20 Terakhir)
        cursor.execute("SELECT rms FROM sensor_data ORDER BY waktu DESC LIMIT 20")
        chart_data = cursor.fetchall()
        chart_data.reverse()

        # 2. Ambil Status Sensor Terakhir
        cursor.execute("SELECT waktu, status FROM sensor_data ORDER BY waktu DESC LIMIT 1")
        latest_sensor = cursor.fetchone()

        # 3. Ambil Riwayat Distribusi
        cursor.execute("SELECT tanggal, jam_mulai, jam_selesai, durasi, status FROM distribution_history ORDER BY id DESC LIMIT 10")
        history_data = cursor.fetchall()

        conn.close()

        # Pemrosesan Data untuk Dashboard
        sensor_status = "Offline"
        status_text = "🔴 AIR TIDAK MENGALIR"
        waktu_deteksi = "-"
        
        if latest_sensor:
            sensor_status = "Online"
            waktu_deteksi = latest_sensor['waktu'].strftime("%Y-%m-%d %H:%M:%S") if isinstance(latest_sensor['waktu'], datetime) else str(latest_sensor['waktu'])
            if latest_sensor['status'] == 'Mengalir':
                status_text = "🟢 AIR MENGALIR"

        # Mengambil info distribusi terakhir dari tabel history
        last_water_time = "-"
        duration = "-"
        if history_data:
            latest_history = history_data[0] # Data paling atas
            last_water_time = f"{latest_history['tanggal']} {latest_history['jam_mulai']}"
            duration = latest_history['durasi'] if latest_history['durasi'] else "Sedang Mengalir..."

        # Flask menggunakan jsonify untuk mengembalikan data berupa JSON
        return jsonify({
            "latest_data": {
                "time": waktu_deteksi,
                "last_water_time": last_water_time,
                "duration": duration
            },
            "history_data": history_data,
            "chart_data": chart_data if chart_data else [{"rms": 0}],
            "status_text": status_text,
            "sensor_status": sensor_status
        }), 200

    except Exception as e:
        print(f"[ERROR FETCH DB] {e}")
        return jsonify({"error": "Gagal mengambil data dari database"}), 500

# 2. Endpoint untuk menerima data dari ESP32 (SIM800L)
@app.route("/api/update_sensor", methods=["POST"])
def update_sensor():
    # Mengambil data JSON yang dikirim oleh ESP32
    data = request.json
    
    # Validasi data sederhana
    if not data or 'rms' not in data or 'is_flowing' not in data:
        return jsonify({"status": "error", "message": "Format data tidak valid"}), 400

    rms_value = data['rms']
    is_flowing = data['is_flowing']
    
    now = datetime.now()
    tanggal_sekarang = now.strftime("%Y-%m-%d")
    jam_sekarang = now.strftime("%H:%M:%S")
    
    status_sensor = "Mengalir" if is_flowing else "Tidak Mengalir"

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Cek status terakhir sebelum insert data baru
        cursor.execute("SELECT status FROM sensor_data ORDER BY waktu DESC LIMIT 1")
        last_row = cursor.fetchone()
        was_flowing = True if (last_row and last_row['status'] == "Mengalir") else False

        # 1. Masukkan data ke sensor_data
        cursor.execute(
            "INSERT INTO sensor_data (waktu, rms, status) VALUES (%s, %s, %s)",
            (now, rms_value, status_sensor)
        )

        # 2. LOGIKA HISTORY: Jika air BARU SAJA menyala (MATI -> NYALA)
        if is_flowing and not was_flowing:
            cursor.execute(
                "INSERT INTO distribution_history (tanggal, jam_mulai, status) VALUES (%s, %s, %s)",
                (tanggal_sekarang, jam_sekarang, 'Berjalan')
            )
            
            pesan_wa = f"💧 *Pemberitahuan Sistem*\n\nAir distribusi saat ini *SUDAH MENGALIR*.\nWaktu deteksi: {jam_sekarang}\n\nSilakan tampung air seperlunya."
            
            # Gunakan Threading sebagai pengganti BackgroundTasks di FastAPI
            threading.Thread(target=broadcast_whatsapp, args=(pesan_wa,)).start()

        # 3. LOGIKA HISTORY: Jika air BARU SAJA mati (NYALA -> MATI)
        elif not is_flowing and was_flowing:
            cursor.execute("SELECT id FROM distribution_history WHERE status = 'Berjalan' ORDER BY id DESC LIMIT 1")
            active_history = cursor.fetchone()
            
            if active_history:
                cursor.execute("""
                    UPDATE distribution_history 
                    SET jam_selesai = %s, status = 'Selesai' 
                    WHERE id = %s
                """, (jam_sekarang, active_history['id']))
                
                cursor.execute("""
                    UPDATE distribution_history 
                    SET durasi = (jam_selesai::time - jam_mulai::time)::text
                    WHERE id = %s
                """, (active_history['id'],))

        conn.commit()
        conn.close()
        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"[ERROR INSERT DB] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    # Menjalankan server Flask di port 8000
    app.run(host="0.0.0.0", port=8000, debug=True)
