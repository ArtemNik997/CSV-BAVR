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