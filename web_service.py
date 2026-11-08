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
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=30)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def make_filename(db, company_name, original_filename):
    _, ext = os.path.splitext(original_filename)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        ext = '.webm'

    safe_name = re.sub(r'[^\w.-]+', '_', company_name.strip()).strip('._')
    if not safe_name:
        safe_name = 'video'

    filename = f"{safe_name}{ext}"

    dest_path = os.path.abspath(os.path.join(UPLOAD_DIR, filename))
    owner = db.execute(
        "SELECT company_name FROM videos WHERE file_path = ?",
        (dest_path,)
    ).fetchone()

    if owner and owner['company_name'] != company_name:
        name_hash = hashlib.sha1(company_name.encode('utf-8')).hexdigest()[:8]
        filename = f"{safe_name}_{name_hash}{ext}"

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

    db = get_db()
    filename = make_filename(db, company_name, file.filename)
    dest_path = os.path.join(UPLOAD_DIR, filename)

    temp_path = dest_path + '.part'
    try:
        file.save(temp_path)
        os.replace(temp_path, dest_path)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return jsonify({"error": f"Failed to save file: {str(e)}"}), 500

    video_url = build_video_url(filename)
    abs_file_path = os.path.abspath(dest_path)

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

        db.commit()
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


@app.route('/media/<filename>', methods=['GET'])
def serve_media(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route('/videos', methods=['GET'])
def list_videos():
    db = get_db()
    records = db.execute("SELECT * FROM videos ORDER BY updated_at DESC")

    videos_list = []
    for record in records:
        videos_list.append({
            "id": record['id'],
            "company_name": record['company_name'],
            "file_path": record['file_path'],
            "video_url": record['video_url'],
            "created_at": record['created_at'],
            "updated_at": record['updated_at']
        })
        
    return jsonify(videos_list), 200

if __name__ == '__main__':
    init_db()
    app.run(host=HOST, port=PORT, threaded=True, debug=False)