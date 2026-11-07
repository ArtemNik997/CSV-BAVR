import os
import re
import sqlite3
import hashlib
from urllib.parse import quote
from flask import Flask, request, jsonify, g, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DB_PATH = os.path.join(BASE_DIR, "videos.db")

HOST = os.environ.get("FLASK_HOST", "0.0.0.0")
PORT = int(os.environ.get("FLASK_PORT", 8000))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

ALLOWED_EXTENSIONS = {'.webm', '.mp4', '.ogv'}

def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA journal_mode=WAL")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL UNIQUE,
            file_path TEXT NOT NULL,
            video_url TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT(datetime('now')),
            updated_at TEXT NOT NULL DEFAULT(datetime('now'))
        )
    """)
    
    conn.commit()
    conn.close()

def get_db():
    """Получает соединение с БД для текущего запроса."""
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=30)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    """Закрывает соединение с БД после завершения запроса."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def make_filename(company_name, original_filename):
    _, ext = os.path.splitext(original_filename)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        ext = '.webm'

    safe_name = re.sub(r'[^w.-]+', '_', company_name.strip()).strip('._')
    if not safe_name:
        safe_name = 'video'

    filename = f"{safe_name}{ext}"

    dest_path = os.path.join(UPLOAD_DIR, filename)
    if os.path.exists(dest_path):
        name_hash = hashlib.sha1(company_name.encode()).hexdigest()[:8]
        base, ext = os.path.splitext(filename)
        filename = f"{base}_{name_hash}{ext}"

    return filename

def build_video_url(filename):
    return f"{request.host_url}media/{quote(filename)}"


@app.route('/upload', methods=['POST'])
def upload_video():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    company_name = request.form.get('company_name', '').strip()

    if not company_name:
        return jsonify({"error": "Company name is required"}), 400

    if file.filename == '':
            return jsonify({"error": "Empty filename"}), 400

    filename = make_filename(company_name, file.filename)
    dest_path = os.path.join(UPLOAD_DIR, filename)

    temp_path = dest_path + '.part'
    try:
        file.save(temp_path)
        os.replace(temp_path, dest_path)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return jsonify({"error": f"Faild to save file: {str(e)}"}), 500

    video_url = build_video_url(filename)
    abs_file_path = os.path.abspath(dest_path)

    db = get_db()
    try:
        old_record = db.execute(
            "SELECT file_path FROM videos WHERE company_name = ?",
            (company_name,)
        ).fetchone()

        if old_record and old_record['file_path'] != abs_file_path:
            try:
                os.remove(old_record['file_path'])
            except OSError:
                pass

        db.execute("""
            INSERT INTO videos (company_name, file_path, video_url)
            VALUES (?, ?, ?)
            ON CONFLICT(company_name) DO UPDATE SET
                file_path = excluded.file_path,
                video_url = excluded.video_url,
                updated_at = datetime('now')
        """, (company_name, abs_file_path, video_url))

        db.commit
    except Exception as e:
        db.rollback()
        return jsonify({"error": f"Database error: {str(e)}"}), 500

    return jsonify({
        "company_name": company_name,
        "video_url": video_url
    }), 200


@app.route('/get_video_url', methods=['GET'])
def get_video_url():
    company_name = request.args.get('company_name', '').strip()

    if not company_name:
        return jsonify({"error": "Company name parameter is required"}), 400

    db = get_db()
    record = db.execute(
        "SELECT company_name, video_url FROM videos WHERE company_name = ?", 
        (company_name,)
    ).fetchone()

    if record:
        return jsonify({
            "company_name": record['company_name'],
            "video_url": record['video_url']
        }), 200
    else:
        return jsonify({"error": "Company not found"}), 404