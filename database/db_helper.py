import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from config.settings import settings

class DatabaseHelper:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pin_id TEXT UNIQUE,
                    original_url TEXT,
                    local_path TEXT,
                    image_hash TEXT,
                    category TEXT,
                    download_status TEXT,
                    upload_status TEXT,
                    gdrive_file_id TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    category TEXT,
                    images_found INTEGER DEFAULT 0,
                    images_downloaded INTEGER DEFAULT 0,
                    duplicates_skipped INTEGER DEFAULT 0,
                    uploads_successful INTEGER DEFAULT 0,
                    uploads_failed INTEGER DEFAULT 0,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ended_at TIMESTAMP,
                    status TEXT
                )
            """)
            conn.commit()

    def insert_pending_image(self, pin_id: str, original_url: str, category: str) -> bool:
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO images (pin_id, original_url, category, download_status, upload_status)
                    VALUES (?, ?, ?, 'PENDING', 'PENDING')
                    ON CONFLICT(pin_id) DO UPDATE SET
                        category = excluded.category,
                        original_url = excluded.original_url,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (pin_id, original_url, category)
                )
                conn.commit()
                return True
        except Exception:
            return False

    def check_pin_exists(self, pin_id: str) -> bool:
        with self._get_connection() as conn:
            row = conn.execute("SELECT 1 FROM images WHERE pin_id = ?", (pin_id,)).fetchone()
            return row is not None

    def get_pin_status(self, pin_id: str) -> Optional[str]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT download_status FROM images WHERE pin_id = ?", (pin_id,)).fetchone()
            return row["download_status"] if row else None

    def check_hash_exists(self, image_hash: str, threshold: int = 4) -> Optional[dict]:
        with self._get_connection() as conn:
            # 1. Fast path: exact match
            row = conn.execute(
                "SELECT * FROM images WHERE image_hash = ? AND download_status = 'DOWNLOADED'", 
                (image_hash,)
            ).fetchone()
            if row:
                return dict(row)
                
            # 2. Perceptual check: compute Hamming distance against all downloaded hashes
            rows = conn.execute(
                "SELECT * FROM images WHERE download_status = 'DOWNLOADED' AND image_hash IS NOT NULL"
            ).fetchall()
            
            if not rows:
                return None
                
            import imagehash
            try:
                target_hash = imagehash.hex_to_hash(image_hash)
                for r in rows:
                    if r["image_hash"]:
                        db_hash = imagehash.hex_to_hash(r["image_hash"])
                        if target_hash - db_hash <= threshold:
                            return dict(r)
            except Exception:
                pass
                
            return None

    def update_download_status(self, pin_id: str, status: str, local_path: Optional[str] = None, image_hash: Optional[str] = None, error_message: Optional[str] = None):
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE images 
                    SET download_status = ?, 
                        local_path = COALESCE(?, local_path), 
                        image_hash = COALESCE(?, image_hash),
                        error_message = COALESCE(?, error_message),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE pin_id = ?
                    """,
                    (status, local_path, image_hash, error_message, pin_id)
                )
                conn.commit()
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e) and "image_hash" in str(e):
                try:
                    with self._get_connection() as conn:
                        conn.execute(
                            """
                            UPDATE images 
                            SET download_status = ?, 
                                local_path = COALESCE(?, local_path), 
                                error_message = COALESCE(?, error_message),
                                updated_at = CURRENT_TIMESTAMP
                            WHERE pin_id = ?
                            """,
                            (status, local_path, error_message, pin_id)
                        )
                        conn.commit()
                except Exception as inner_error:
                    logger.error(f"Fallback DB update failed for {pin_id}: {inner_error}")
            else:
                raise

    def update_upload_status(self, pin_id: str, status: str, gdrive_file_id: Optional[str] = None, error_message: Optional[str] = None):
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE images 
                SET upload_status = ?, 
                    gdrive_file_id = COALESCE(?, gdrive_file_id),
                    error_message = COALESCE(?, error_message),
                    updated_at = CURRENT_TIMESTAMP
                WHERE pin_id = ?
                """,
                (status, gdrive_file_id, error_message, pin_id)
            )
            conn.commit()

    def get_pending_uploads(self) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM images WHERE download_status = 'DOWNLOADED' AND upload_status != 'UPLOADED'"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_images(self, limit: int = 100, offset: int = 0, category: Optional[str] = None) -> List[dict]:
        with self._get_connection() as conn:
            query = "SELECT * FROM images"
            params = []
            if category:
                query += " WHERE category = ?"
                params.append(category)
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_statistics(self) -> dict:
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            downloaded = conn.execute("SELECT COUNT(*) FROM images WHERE download_status = 'DOWNLOADED'").fetchone()[0]
            failed_download = conn.execute("SELECT COUNT(*) FROM images WHERE download_status = 'FAILED'").fetchone()[0]
            duplicates = conn.execute("SELECT COUNT(*) FROM images WHERE download_status = 'DUPLICATE'").fetchone()[0]
            uploaded = conn.execute("SELECT COUNT(*) FROM images WHERE upload_status = 'UPLOADED'").fetchone()[0]
            pending_upload = conn.execute("SELECT COUNT(*) FROM images WHERE download_status = 'DOWNLOADED' AND upload_status != 'UPLOADED'").fetchone()[0]
            
            # Categories breakdown
            cat_rows = conn.execute(
                "SELECT category, COUNT(*) as cnt, SUM(CASE WHEN download_status='DOWNLOADED' THEN 1 ELSE 0 END) as dl_cnt FROM images GROUP BY category"
            ).fetchall()
            categories = {r["category"]: {"total": r["cnt"], "downloaded": r["dl_cnt"]} for r in cat_rows}

            return {
                "total": total,
                "downloaded": downloaded,
                "failed_download": failed_download,
                "duplicates": duplicates,
                "uploaded": uploaded,
                "pending_upload": pending_upload,
                "categories": categories
            }

    def start_sync_run(self, run_id: str, category: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sync_history (run_id, category, started_at, status)
                VALUES (?, ?, CURRENT_TIMESTAMP, 'RUNNING')
                """,
                (run_id, category)
            )
            conn.commit()
            return cursor.lastrowid

    def update_sync_progress(self, run_id: str, images_found: int = 0, images_downloaded: int = 0, duplicates_skipped: int = 0, uploads_successful: int = 0, uploads_failed: int = 0):
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE sync_history
                SET images_found = images_found + ?,
                    images_downloaded = images_downloaded + ?,
                    duplicates_skipped = duplicates_skipped + ?,
                    uploads_successful = uploads_successful + ?,
                    uploads_failed = uploads_failed + ?
                WHERE run_id = ?
                """,
                (images_found, images_downloaded, duplicates_skipped, uploads_successful, uploads_failed, run_id)
            )
            conn.commit()

    def end_sync_run(self, run_id: str, status: str = 'COMPLETED'):
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE sync_history
                SET ended_at = CURRENT_TIMESTAMP,
                    status = ?
                WHERE run_id = ?
                """,
                (status, run_id)
            )
            conn.commit()

    def get_sync_history(self, limit: int = 50) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_history ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

db = DatabaseHelper()
