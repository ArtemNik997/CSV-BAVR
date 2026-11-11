import os
import re
import time
import hashlib
from urllib.parse import quote

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, g, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

HOST = os.environ.get("FLASK_HOST", "0.0.0.0")
PORT = int(os.environ.get("FLASK_PORT", 8000))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

ALLOWED_EXTENSIONS = {".webm", ".mp4", ".ogv"}


def _db_params():
    try:
        return dict(
            host=os.environ["DB_HOST"],
            port=int(os.environ.get("DB_PORT", "5432")),
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            connect_timeout=5,
        )
    except KeyError as e:
        raise RuntimeError(f"Environment variable {e.args[0]} is not set")


def connect():
    return psycopg2.connect(cursor_factory=RealDictCursor, **_db_params())


def init_db(retries=60, delay=2):
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            conn = connect()
            break
        except psycopg2.OperationalError as e:
            last_error = e
            print(f"[init_db] DB not ready ({attempt}/{retries}): {e}", flush=True)
            time.sleep(delay)
    else:
        raise RuntimeError("Database is not available") from last_error

    try:
        with conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS videos (
                    id SERIAL PRIMARY KEY,
                    company_name TEXT NOT NULL UNIQUE,
                    file_path TEXT NOT NULL,
                    video_url TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
    finally:
        conn.close()


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def fetch_one(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def make_filename(db, company_name, original_filename):
    _, ext = os.path.splitext(original_filename)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        ext = ".webm"

    safe_name = re.sub(r"[^\w.-]+", "_", company_name.strip()).strip("._")
    if not safe_name:
        safe_name = "video"

    filename = f"{safe_name}{ext}"

    dest_path = os.path.abspath(os.path.join(UPLOAD_DIR, filename))
    owner = fetch_one(
        db, "SELECT company_name FROM videos WHERE file_path = %s", (dest_path,)
    )

    if owner and owner["company_name"] != company_name:
        name_hash = hashlib.sha1(company_name.encode("utf-8")).hexdigest()[:8]
        filename = f"{safe_name}_{name_hash}{ext}"

    return filename


def build_video_url(filename):
    return f"{request.host_url}media/{quote(filename)}"


@app.route("/upload", methods=["POST"])
def upload_video():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    company_name = request.form.get("company_name", "").strip()

    if not company_name:
        return jsonify({"error": "Company name is required"}), 400

    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    db = get_db()
    filename = make_filename(db, company_name, file.filename)
    dest_path = os.path.join(UPLOAD_DIR, filename)

    temp_path = dest_path + ".part"
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
        old_record = fetch_one(
            db, "SELECT file_path FROM videos WHERE company_name = %s", (company_name,)
        )

        if old_record and old_record["file_path"] != abs_file_path:
            try:
                os.remove(old_record["file_path"])
            except OSError:
                pass

        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO videos (company_name, file_path, video_url)
                VALUES (%s, %s, %s)
                ON CONFLICT(company_name) DO UPDATE SET
                    file_path = EXCLUDED.file_path,
                    video_url = EXCLUDED.video_url,
                    updated_at = NOW()
                """,
                (company_name, abs_file_path, video_url),
            )

        db.commit()
    except Exception as e:
        db.rollback()
        return jsonify({"error": f"Database error: {str(e)}"}), 500

    return jsonify({"company_name": company_name, "video_url": video_url}), 200


@app.route("/get_video_url", methods=["GET"])
def get_video_url():
    company_name = request.args.get("company_name", "").strip()

    if not company_name:
        return jsonify({"error": "Company name parameter is required"}), 400

    db = get_db()
    record = fetch_one(
        db,
        "SELECT company_name, video_url FROM videos WHERE company_name = %s",
        (company_name,),
    )

    if record:
        return (
            jsonify(
                {
                    "company_name": record["company_name"],
                    "video_url": record["video_url"],
                }
            ),
            200,
        )
    else:
        return jsonify({"error": "Company not found"}), 404


@app.route("/media/<filename>", methods=["GET"])
def serve_media(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/videos", methods=["GET"])
def list_videos():
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT * FROM videos ORDER BY updated_at DESC")
        records = cur.fetchall()

    videos_list = []
    for record in records:
        videos_list.append(
            {
                "id": record["id"],
                "company_name": record["company_name"],
                "file_path": record["file_path"],
                "video_url": record["video_url"],
                "created_at": record["created_at"].isoformat(),
                "updated_at": record["updated_at"].isoformat(),
            }
        )

    return jsonify(videos_list), 200


if __name__ == "__main__":
    init_db()
    app.run(host=HOST, port=PORT, threaded=True, debug=False)
