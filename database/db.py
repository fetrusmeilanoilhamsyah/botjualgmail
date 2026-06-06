"""
database/db.py - Database Bot Jual Gmail
Schema: users, paket_gmail, stok_gmail, transaksi, pembelian, garansi, topup

Format stok Gmail (kolom data_extra):
  email|password|recovery_email|tanggal_buat|catatan
  Contoh: test@gmail.com|Pass123!|recover@gmail.com|2024-01-15|fresh-indonesia

Keamanan:
  - WAL mode untuk concurrent reads
  - Connection pool 16 koneksi
  - Semua transaksi keuangan ATOMIC (BEGIN/COMMIT)
  - Anti-race condition pada stok (UPDATE ... WHERE terjual=0)
"""
import sqlite3
import os
import queue
import threading
import logging
import copy
from contextlib import contextmanager
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "bot.db")

# ─── CONNECTION POOL ──────────────────────────────────────────────────────────
_conn_pool        = queue.Queue(maxsize=32)
_pool_initialized = False
_pool_lock        = threading.Lock()
_session_cache    = {}
_session_lock     = threading.Lock()


def _init_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA cache_size=-32000")   # 32MB cache
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_connection_pool():
    global _pool_initialized
    with _pool_lock:
        if _pool_initialized:
            return
        for _ in range(32):
            _conn_pool.put(_init_connection())
        _pool_initialized = True
        print("✅ [botjualgmail] DB connection pool ready (32 koneksi)")


@contextmanager
def get_connection():
    try:
        conn = _conn_pool.get(timeout=30)
    except queue.Empty:
        raise RuntimeError("DB pool exhausted – bot overloaded!")
    try:
        yield conn
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        _conn_pool.put(conn)


# ─── INIT DB ──────────────────────────────────────────────────────────────────

def init_db():
    """Buat semua tabel dan index, jalankan migrasi aman."""
    init_connection_pool()
    with get_connection() as conn:
        # ── users ──────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY,
                username        TEXT    DEFAULT '',
                full_name       TEXT    DEFAULT '',
                saldo           INTEGER DEFAULT 0,
                referral_by     INTEGER DEFAULT NULL,
                referral_count  INTEGER DEFAULT 0,
                referral_banned INTEGER DEFAULT 0,
                joined_at       TEXT    DEFAULT (datetime('now','localtime')),
                last_active     TEXT    DEFAULT (datetime('now','localtime'))
            )
        """)

        # ── paket_gmail ───────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paket_gmail (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                nama        TEXT    NOT NULL,
                kuantitas   INTEGER NOT NULL DEFAULT 1,
                deskripsi   TEXT    DEFAULT '',
                harga       INTEGER NOT NULL DEFAULT 0,
                aktif       INTEGER DEFAULT 1,
                urutan      INTEGER DEFAULT 0
            )
        """)

        # ── stok_gmail ────────────────────────────────────────────────────
        # Format data: "email|password|recovery|tgl_buat|catatan"
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stok_gmail (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                paket_id    INTEGER NOT NULL REFERENCES paket_gmail(id),
                email       TEXT    NOT NULL UNIQUE,
                password    TEXT    NOT NULL,
                recovery    TEXT    DEFAULT '',
                tgl_buat    TEXT    DEFAULT '',
                catatan     TEXT    DEFAULT '',
                terjual     INTEGER DEFAULT 0,
                terjual_at  TEXT    DEFAULT NULL,
                terjual_ke  INTEGER DEFAULT NULL
            )
        """)

        # ── transaksi (riwayat mutasi saldo) ──────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transaksi (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id),
                tipe            TEXT    NOT NULL,
                jumlah          INTEGER NOT NULL,
                saldo_sebelum   INTEGER NOT NULL DEFAULT 0,
                saldo_sesudah   INTEGER NOT NULL DEFAULT 0,
                keterangan      TEXT    DEFAULT '',
                ref_id          TEXT    DEFAULT NULL,
                created_at      TEXT    DEFAULT (datetime('now','localtime'))
            )
        """)

        # ── pembelian ─────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pembelian (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id),
                paket_id        INTEGER NOT NULL REFERENCES paket_gmail(id),
                harga_bayar     INTEGER NOT NULL,
                jumlah_akun     INTEGER NOT NULL DEFAULT 1,
                garansi_until   TEXT    NOT NULL,
                status          TEXT    DEFAULT 'aktif',
                created_at      TEXT    DEFAULT (datetime('now','localtime'))
            )
        """)

        # ── pembelian_detail (akun mana yang dibeli dalam satu pembelian) ─
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pembelian_detail (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                pembelian_id INTEGER NOT NULL REFERENCES pembelian(id),
                stok_id     INTEGER NOT NULL REFERENCES stok_gmail(id)
            )
        """)

        # ── garansi ───────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS garansi (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                pembelian_id        INTEGER NOT NULL REFERENCES pembelian(id),
                user_id             INTEGER NOT NULL,
                alasan              TEXT    DEFAULT '',
                status              TEXT    DEFAULT 'pending',
                stok_pengganti_ids  TEXT    DEFAULT NULL,
                admin_catatan       TEXT    DEFAULT '',
                created_at          TEXT    DEFAULT (datetime('now','localtime')),
                resolved_at         TEXT    DEFAULT NULL
            )
        """)

        # ── topup ─────────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS topup (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id),
                order_id        TEXT    NOT NULL UNIQUE,
                jumlah          INTEGER NOT NULL,
                status          TEXT    DEFAULT 'pending',
                qr_chat_id      INTEGER DEFAULT NULL,
                qr_message_id   INTEGER DEFAULT NULL,
                created_at      TEXT    DEFAULT (datetime('now','localtime')),
                completed_at    TEXT    DEFAULT NULL
            )
        """)

        # ── broadcast_log ─────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS broadcast_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id      INTEGER,
                pesan         TEXT,
                sukses        INTEGER DEFAULT 0,
                gagal         INTEGER DEFAULT 0,
                sent_at       TEXT    DEFAULT (datetime('now','localtime'))
            )
        """)

        # ── schema_version ────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version     INTEGER PRIMARY KEY,
                deskripsi   TEXT,
                applied_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # ── settings ──────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # ── referral_spam_log — atomic counter untuk anti-spam referral ──
        # (menggantikan JSON di settings table yang rawan race condition)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS referral_spam_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                ts          REAL    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ref_spam_log ON referral_spam_log(referrer_id, ts)")

        # ── Migrasi: tambah harga_beli ke topup jika belum ada (Bug #5 fix) ──
        try:
            conn.execute("ALTER TABLE topup ADD COLUMN harga_beli INTEGER DEFAULT NULL")
            print("✅ [botjualgmail] Migrasi: kolom harga_beli ditambahkan ke tabel topup")
        except Exception:
            pass  # kolom sudah ada

        # ── INDEXES ───────────────────────────────────────────────────────
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stok_paket   ON stok_gmail(paket_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stok_terjual ON stok_gmail(terjual)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trx_user     ON transaksi(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trx_tipe     ON transaksi(tipe)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_beli_user    ON pembelian(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_beli_user_date ON pembelian(user_id, created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trx_user_date  ON transaksi(user_id, created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_topup_order  ON topup(order_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_topup_user   ON topup(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_topup_status ON topup(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_garansi_beli ON garansi(pembelian_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_ref    ON users(referral_by)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_beli_created ON pembelian(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_topup_created ON topup(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trx_created ON transaksi(created_at)")

        # Version check
        ver = conn.execute("SELECT MAX(version) as v FROM schema_version").fetchone()["v"]
        if ver is None:
            conn.execute("INSERT INTO schema_version(version,deskripsi) VALUES(1,'Initial schema')")
            print("✅ [botjualgmail] Schema v1 dibuat")

        # Seed paket default jika belum ada
        cnt = conn.execute("SELECT COUNT(*) as c FROM paket_gmail").fetchone()["c"]
        if cnt == 0:
            _seed_paket_default(conn)

        # Pastikan paket Custom Order (ID 99) terdaftar untuk custom buy
        conn.execute("""
            INSERT OR IGNORE INTO paket_gmail(id, nama, kuantitas, deskripsi, harga, aktif, urutan)
            VALUES(99, 'Custom Order', 0, 'Pembelian Custom', 0, 0, 99)
        """)

        conn.commit()
    print("✅ [botjualgmail] Database siap")


def _seed_paket_default(conn):
    """Insert paket default pertama kali."""
    paket_default = [
        ("1 Akun Gmail",  1,  "Pembelian 1 akun Gmail fresh",  5000,  1, 1),
        ("5 Akun Gmail",  5,  "Pembelian 5 akun Gmail fresh",  25000, 1, 2),
        ("10 Akun Gmail", 10, "Pembelian 10 akun Gmail fresh", 50000, 1, 3),
        ("20 Akun Gmail", 20, "Pembelian 20 akun Gmail fresh", 100000, 1, 4),
        ("50 Akun Gmail", 50, "Pembelian 50 akun Gmail fresh", 250000, 1, 5),
        ("100 Akun Gmail", 100, "Pembelian 100 akun Gmail fresh", 500000, 1, 6),
    ]
    conn.executemany(
        "INSERT INTO paket_gmail(nama, kuantitas, deskripsi, harga, aktif, urutan) VALUES(?,?,?,?,?,?)",
        paket_default
    )
    print("✅ [botjualgmail] Paket default berhasil dibuat (harga bisa diedit via admin)")


# ═══════════════════════════════════════════════════════════════════════════════
# USERS
# ═══════════════════════════════════════════════════════════════════════════════

def upsert_user(user_id: int, username: str, full_name: str):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO users (id, username, full_name, last_active)
            VALUES (?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(id) DO UPDATE SET
                username    = excluded.username,
                full_name   = excluded.full_name,
                last_active = excluded.last_active
        """, (user_id, username or "", full_name or ""))
        conn.commit()


def get_user(user_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_saldo(user_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT saldo FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["saldo"] if row else 0


def get_all_user_ids() -> list:
    with get_connection() as conn:
        rows = conn.execute("SELECT id FROM users").fetchall()
        return [r["id"] for r in rows]


def get_total_users() -> int:
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]


# ═══════════════════════════════════════════════════════════════════════════════
# SALDO (ATOMIC)
# ═══════════════════════════════════════════════════════════════════════════════

def tambah_saldo(user_id: int, jumlah: int, tipe: str, keterangan: str, ref_id: str = None) -> dict:
    """
    Tambah saldo user secara ATOMIC. Return dict {saldo_sebelum, saldo_sesudah}.
    """
    with get_connection() as conn:
        conn.execute("BEGIN")
        row = conn.execute("SELECT saldo FROM users WHERE id = ?", (user_id,)).fetchone()
        saldo_sebelum = row["saldo"] if row else 0
        saldo_sesudah = saldo_sebelum + jumlah
        conn.execute("UPDATE users SET saldo = ? WHERE id = ?", (saldo_sesudah, user_id))
        conn.execute("""
            INSERT INTO transaksi(user_id, tipe, jumlah, saldo_sebelum, saldo_sesudah, keterangan, ref_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, tipe, jumlah, saldo_sebelum, saldo_sesudah, keterangan, ref_id))
        conn.execute("COMMIT")
    return {"saldo_sebelum": saldo_sebelum, "saldo_sesudah": saldo_sesudah}


def kurangi_saldo(user_id: int, jumlah: int, tipe: str, keterangan: str, ref_id: str = None) -> dict | None:
    """
    Kurangi saldo ATOMIC. Return None jika saldo tidak cukup.
    """
    with get_connection() as conn:
        conn.execute("BEGIN")
        row = conn.execute("SELECT saldo FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            return None
        saldo_sebelum = row["saldo"]
        if saldo_sebelum < jumlah:
            conn.execute("ROLLBACK")
            return None
        saldo_sesudah = saldo_sebelum - jumlah
        conn.execute("UPDATE users SET saldo = ? WHERE id = ?", (saldo_sesudah, user_id))
        conn.execute("""
            INSERT INTO transaksi(user_id, tipe, jumlah, saldo_sebelum, saldo_sesudah, keterangan, ref_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, tipe, -jumlah, saldo_sebelum, saldo_sesudah, keterangan, ref_id))
        conn.execute("COMMIT")
    return {"saldo_sebelum": saldo_sebelum, "saldo_sesudah": saldo_sesudah}


# ═══════════════════════════════════════════════════════════════════════════════
# TOPUP (PAKASIR)
# ═══════════════════════════════════════════════════════════════════════════════

def create_topup(user_id: int, order_id: str, jumlah: int,
                 qr_chat_id: int = None, qr_message_id: int = None) -> bool:
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO topup(user_id, order_id, jumlah, status, qr_chat_id, qr_message_id)
                VALUES (?, ?, ?, 'pending', ?, ?)
            """, (user_id, order_id, jumlah, qr_chat_id, qr_message_id))
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        logger.error("[DB] create_topup: order_id duplikat %s", order_id)
        return False


def get_topup(order_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM topup WHERE order_id = ?", (order_id,)).fetchone()
        return dict(row) if row else None


def complete_topup_if_pending(order_id: str, completed_at: str = None) -> bool:
    """
    ATOMIC: tandai topup selesai HANYA jika masih 'pending' atau 'expired'.
    Return True jika berhasil update (pertama kali), False jika sudah diproses.
    """
    completed_at = completed_at or datetime.now().isoformat()
    with get_connection() as conn:
        result = conn.execute("""
            UPDATE topup SET status='completed', completed_at=?
            WHERE order_id=? AND status IN ('pending', 'expired')
        """, (completed_at, order_id))
        conn.commit()
        return result.rowcount > 0


def update_topup_status(order_id: str, status: str) -> bool:
    with get_connection() as conn:
        conn.execute("UPDATE topup SET status=? WHERE order_id=?", (status, order_id))
        conn.commit()
    return True


def get_and_expire_old_pending_topups(minutes: int = 5) -> list:
    """
    ATOMIC: Memperbarui status topup pending yang kedaluwarsa menjadi 'expired'
    dan mengembalikan detail baris yang di-update secara atomic menggunakan RETURNING clause (SQLite 3.35+).
    Mencegah race condition double-notification.
    Returns: list of dict berisi topup yang di-expire oleh query ini saja.
    """
    time_clause = f"-{minutes} minutes"
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                """UPDATE topup
                   SET status = 'expired'
                   WHERE status = 'pending' AND created_at < datetime('now', 'localtime', ?)
                   RETURNING id, user_id, order_id, jumlah, qr_chat_id, qr_message_id""",
                (time_clause,)
            )
            rows = cursor.fetchall()
            conn.commit()
            
            expired_list = [dict(r) for r in rows]
            if expired_list:
                logger.info("[DB] get_and_expire_old_pending_topups: %d topup di-expire", len(expired_list))
            return expired_list
    except Exception as exc:
        logger.error("[DB] get_and_expire_old_pending_topups error: %s", exc)
        return []



def get_user_topups(user_id: int, limit: int = 10) -> list:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM topup WHERE user_id=? ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# PAKET GMAIL
# ═══════════════════════════════════════════════════════════════════════════════

def get_paket_aktif() -> list:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.* FROM paket_gmail p
            WHERE p.aktif=1
            ORDER BY p.urutan ASC
        """).fetchall()
        res = []
        global_stok = conn.execute("SELECT COUNT(*) FROM stok_gmail WHERE terjual=0").fetchone()[0]
        for r in rows:
            d = dict(r)
            d["stok_tersedia"] = global_stok
            res.append(d)
        return res


def get_paket_by_id(paket_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT p.* FROM paket_gmail p WHERE p.id=?", (paket_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["stok_tersedia"] = conn.execute("SELECT COUNT(*) FROM stok_gmail WHERE terjual=0").fetchone()[0]
        return d


def get_all_paket() -> list:
    """Untuk admin: tampilkan semua paket termasuk nonaktif."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.*, COUNT(s.id) as stok_total
            FROM paket_gmail p
            LEFT JOIN stok_gmail s ON s.paket_id=p.id
            GROUP BY p.id
            ORDER BY p.urutan ASC
        """).fetchall()
        res = []
        global_stok = conn.execute("SELECT COUNT(*) FROM stok_gmail WHERE terjual=0").fetchone()[0]
        for r in rows:
            d = dict(r)
            d["stok_tersedia"] = global_stok
            res.append(d)
        return res


def update_paket_harga(paket_id: int, harga: int) -> bool:
    with get_connection() as conn:
        conn.execute("UPDATE paket_gmail SET harga=? WHERE id=?", (harga, paket_id))
        conn.commit()
    return True


def get_harga_satuan() -> int:
    """Mendapatkan harga per 1 Gmail."""
    with get_connection() as conn:
        row = conn.execute("SELECT harga FROM paket_gmail WHERE kuantitas = 1").fetchone()
        return row["harga"] if row else 5000


def update_harga_satuan(harga_satuan: int) -> bool:
    """Mengubah harga satuan dan mengalikan harga semua paket secara otomatis."""
    with get_connection() as conn:
        conn.execute("BEGIN")
        conn.execute("UPDATE paket_gmail SET harga = kuantitas * ?", (harga_satuan,))
        conn.execute("COMMIT")  # BUKAN conn.commit() — isolation_level=None = autocommit, conn.commit() adalah no-op
    return True


def toggle_paket_aktif(paket_id: int) -> bool:
    with get_connection() as conn:
        conn.execute("UPDATE paket_gmail SET aktif=CASE WHEN aktif=1 THEN 0 ELSE 1 END WHERE id=?", (paket_id,))
        conn.commit()
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# STOK GMAIL
# ═══════════════════════════════════════════════════════════════════════════════

def add_stok_gmail(paket_id: int, email: str, password: str,
                   recovery: str = "", tgl_buat: str = "", catatan: str = "") -> bool:
    """Tambah 1 akun ke stok."""
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO stok_gmail(paket_id, email, password, recovery, tgl_buat, catatan)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (paket_id, email.strip(), password.strip(), recovery.strip(), tgl_buat.strip(), catatan.strip()))
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        logger.warning("[DB] add_stok: email duplikat %s", email)
        return False


def bulk_add_stok(paket_id: int, lines: list) -> tuple[int, int]:
    """
    Bulk insert stok dari list string.
    Format tiap baris: email|password|recovery|tgl_buat|catatan
    Fields minimal: email|password (sisanya opsional)
    Return: (berhasil, duplikat)
    """
    ok = 0
    dup = 0
    with get_connection() as conn:
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < 2:
                continue
            email    = parts[0].strip()
            password = parts[1].strip()
            recovery = parts[2].strip() if len(parts) > 2 else ""
            tgl_buat = parts[3].strip() if len(parts) > 3 else ""
            catatan  = parts[4].strip() if len(parts) > 4 else ""
            if not email or not password:
                continue
            try:
                conn.execute("""
                    INSERT INTO stok_gmail(paket_id, email, password, recovery, tgl_buat, catatan)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (paket_id, email, password, recovery, tgl_buat, catatan))
                ok += 1
            except sqlite3.IntegrityError:
                dup += 1
        conn.commit()
    return ok, dup

def ambil_stok(jumlah: int, paket_id: int = None) -> list | None:
    """
    ATOMIC: Ambil N akun dari stok secara global (paket_id diabaikan dalam filter pencarian).
    """
    with get_connection() as conn:
        conn.execute("BEGIN")
        now = datetime.now().isoformat()
        rows = conn.execute("""
            UPDATE stok_gmail SET terjual=1, terjual_at=?
            WHERE id IN (
                SELECT id FROM stok_gmail
                WHERE terjual=0 LIMIT ?
            )
            RETURNING id, email, password, recovery, tgl_buat, catatan
        """, (now, jumlah)).fetchall()

        if len(rows) < jumlah:
            conn.execute("ROLLBACK")
            return None

        # Jika paket_id diberikan, update paket_id di DB agar history transaksi konsisten
        if paket_id is not None:
            stok_ids = [r["id"] for r in rows]
            for sid in stok_ids:
                conn.execute("UPDATE stok_gmail SET paket_id=? WHERE id=?", (paket_id, sid))

        conn.execute("COMMIT")
    return [dict(r) for r in rows]


def get_stok_count(paket_id: int = None) -> int:
    """Mendapatkan total stok global yang belum terjual."""
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM stok_gmail WHERE terjual=0").fetchone()
        return row["c"] if row else 0


def get_stok_summary() -> list:
    """Ringkasan stok per paket untuk admin."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.id, p.nama, p.harga, p.aktif,
                   COUNT(CASE WHEN s.terjual=0 THEN 1 END) as tersedia,
                   COUNT(CASE WHEN s.terjual=1 THEN 1 END) as terjual
            FROM paket_gmail p
            LEFT JOIN stok_gmail s ON s.paket_id=p.id
            GROUP BY p.id
            ORDER BY p.urutan
        """).fetchall()
        return [dict(r) for r in rows]


def tandai_stok_terjual_ke(stok_ids: list, user_id: int):
    """Tandai stok_id → terjual_ke user_id (dipakai setelah ambil_stok)."""
    with get_connection() as conn:
        for sid in stok_ids:
            conn.execute("UPDATE stok_gmail SET terjual_ke=? WHERE id=?", (user_id, sid))
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# PEMBELIAN
# ═══════════════════════════════════════════════════════════════════════════════

def create_pembelian(user_id: int, paket_id: int, harga_bayar: int,
                     jumlah_akun: int, stok_ids: list) -> int:
    """
    Catat pembelian + detail akun yang dibeli.
    Return pembelian_id.
    """
    from config import GARANSI_JAM
    garansi_until = (datetime.now() + timedelta(hours=GARANSI_JAM)).isoformat()
    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO pembelian(user_id, paket_id, harga_bayar, jumlah_akun, garansi_until)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, paket_id, harga_bayar, jumlah_akun, garansi_until))
        pembelian_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO pembelian_detail(pembelian_id, stok_id) VALUES (?,?)",
            [(pembelian_id, sid) for sid in stok_ids]
        )
        conn.commit()
    return pembelian_id


def get_riwayat_beli(user_id: int, limit: int = 20) -> list:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT b.id, b.paket_id, p.nama as paket_nama, b.harga_bayar,
                   b.jumlah_akun, b.garansi_until, b.status, b.created_at
            FROM pembelian b
            JOIN paket_gmail p ON p.id=b.paket_id
            WHERE b.user_id=?
            ORDER BY b.created_at DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()
        return [dict(r) for r in rows]


def get_detail_pembelian(pembelian_id: int, user_id: int) -> dict | None:
    """Ambil detail pembelian + daftar akun."""
    with get_connection() as conn:
        beli = conn.execute("""
            SELECT b.*, p.nama as paket_nama
            FROM pembelian b
            JOIN paket_gmail p ON p.id=b.paket_id
            WHERE b.id=? AND b.user_id=?
        """, (pembelian_id, user_id)).fetchone()
        if not beli:
            return None
        akun = conn.execute("""
            SELECT s.email, s.password, s.recovery, s.tgl_buat, s.catatan
            FROM pembelian_detail d
            JOIN stok_gmail s ON s.id=d.stok_id
            WHERE d.pembelian_id=?
        """, (pembelian_id,)).fetchall()
        result = dict(beli)
        result["akun_list"] = [dict(a) for a in akun]
    return result


def get_pembelian_by_id(pembelian_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM pembelian WHERE id=?", (pembelian_id,)).fetchone()
        return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════════════════
# GARANSI
# ═══════════════════════════════════════════════════════════════════════════════

def create_garansi(pembelian_id: int, user_id: int, alasan: str) -> int | None:
    """
    Buat klaim garansi. Return garansi_id atau None jika garansi sudah habis.
    Cek: (1) pembelian milik user, (2) garansi belum kadaluarsa, (3) belum ada klaim aktif.
    """
    with get_connection() as conn:
        beli = conn.execute(
            "SELECT id, garansi_until, status FROM pembelian WHERE id=? AND user_id=?",
            (pembelian_id, user_id)
        ).fetchone()
        if not beli:
            return None  # bukan punya dia
        if beli["status"] != "aktif":
            return None  # sudah klaim garansi sebelumnya
        now = datetime.now().isoformat()
        if beli["garansi_until"] < now:
            return None  # kadaluarsa

        # Cek duplikat klaim aktif
        existing = conn.execute(
            "SELECT id FROM garansi WHERE pembelian_id=? AND status IN ('pending','diproses')",
            (pembelian_id,)
        ).fetchone()
        if existing:
            return None

        cur = conn.execute("""
            INSERT INTO garansi(pembelian_id, user_id, alasan)
            VALUES (?, ?, ?)
        """, (pembelian_id, user_id, alasan))
        conn.execute("UPDATE pembelian SET status='klaim_garansi' WHERE id=?", (pembelian_id,))
        conn.commit()
        return cur.lastrowid


def get_garansi_pending() -> list:
    """Untuk admin: daftar klaim garansi yang belum diproses."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT g.*, u.username, u.full_name,
                   p.id as pembelian_id, pk.nama as paket_nama
            FROM garansi g
            JOIN users u ON u.id=g.user_id
            JOIN pembelian p ON p.id=g.pembelian_id
            JOIN paket_gmail pk ON pk.id=p.paket_id
            WHERE g.status IN ('pending','diproses')
            ORDER BY g.created_at ASC
        """).fetchall()
        return [dict(r) for r in rows]


def resolve_garansi(garansi_id: int, stok_pengganti_ids: list, admin_catatan: str = "") -> bool:
    """Admin selesaikan garansi: kirim akun pengganti."""
    ids_str = ",".join(str(i) for i in stok_pengganti_ids)
    with get_connection() as conn:
        conn.execute("""
            UPDATE garansi SET status='selesai', stok_pengganti_ids=?,
            admin_catatan=?, resolved_at=datetime('now','localtime')
            WHERE id=?
        """, (ids_str, admin_catatan, garansi_id))
        # Update status pembelian kembali ke 'selesai'
        row = conn.execute("SELECT pembelian_id FROM garansi WHERE id=?", (garansi_id,)).fetchone()
        if row:
            conn.execute("UPDATE pembelian SET status='selesai' WHERE id=?", (row["pembelian_id"],))
        conn.commit()
    return True


def tolak_garansi(garansi_id: int, admin_catatan: str = "") -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT pembelian_id FROM garansi WHERE id=?", (garansi_id,)).fetchone()
        conn.execute("""
            UPDATE garansi SET status='ditolak', admin_catatan=?,
            resolved_at=datetime('now','localtime') WHERE id=?
        """, (admin_catatan, garansi_id))
        if row:
            conn.execute("UPDATE pembelian SET status='aktif' WHERE id=?", (row["pembelian_id"],))
        conn.commit()
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# REFERRAL
# ═══════════════════════════════════════════════════════════════════════════════

def set_referral(user_id: int, referrer_id: int) -> bool:
    """Set referrer hanya sekali. Return True jika berhasil."""
    if user_id == referrer_id:
        return False
    with get_connection() as conn:
        r = conn.execute(
            "UPDATE users SET referral_by=? WHERE id=? AND referral_by IS NULL",
            (referrer_id, user_id)
        )
        conn.commit()
        return r.rowcount > 0


def get_referral_stats(user_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT referral_count, referral_banned FROM users WHERE id=?",
            (user_id,)
        ).fetchone()
        return dict(row) if row else {"referral_count": 0, "referral_banned": 0}


def increment_referral_count(referrer_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET referral_count=referral_count+1 WHERE id=?",
            (referrer_id,)
        )
        conn.commit()


def ban_referral(user_id: int):
    """Ban fitur referral user karena terdeteksi bot/spam."""
    with get_connection() as conn:
        conn.execute("UPDATE users SET referral_banned=1 WHERE id=?", (user_id,))
        conn.commit()


def is_referral_banned(user_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT referral_banned FROM users WHERE id=?", (user_id,)).fetchone()
        return bool(row["referral_banned"]) if row else False


# ═══════════════════════════════════════════════════════════════════════════════
# TRANSAKSI (RIWAYAT MUTASI)
# ═══════════════════════════════════════════════════════════════════════════════

def get_riwayat_mutasi(user_id: int, limit: int = 30) -> list:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM transaksi WHERE user_id=?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit)).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# BROADCAST
# ═══════════════════════════════════════════════════════════════════════════════

def log_broadcast(admin_id: int, pesan: str, sukses: int, gagal: int):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO broadcast_log(admin_id, pesan, sukses, gagal)
            VALUES (?, ?, ?, ?)
        """, (admin_id, pesan, sukses, gagal))
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# STATISTIK ADMIN
# ═══════════════════════════════════════════════════════════════════════════════

import time

_admin_stats_cache = {
    "data": None,
    "last_updated": 0
}
_admin_stats_lock = threading.Lock()

def get_admin_stats() -> dict:
    now = time.time()
    # Fast path: return cached data without acquiring lock
    if _admin_stats_cache["data"] is not None and now - _admin_stats_cache["last_updated"] < 5:
        return _admin_stats_cache["data"]

    with _admin_stats_lock:
        # Re-check after acquiring lock (another thread may have updated)
        now = time.time()
        if _admin_stats_cache["data"] is not None and now - _admin_stats_cache["last_updated"] < 5:
            return _admin_stats_cache["data"]

        with get_connection() as conn:
            total_user   = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
            total_saldo  = conn.execute("SELECT COALESCE(SUM(saldo),0) as s FROM users").fetchone()["s"]
            total_trx    = conn.execute("SELECT COUNT(*) as c FROM pembelian").fetchone()["c"]
            
            # Gunakan range query agar terindeks (cepat) dibanding menggunakan fungsi date(created_at)
            today_start  = datetime.now().strftime("%Y-%m-%d") + " 00:00:00"
            
            trx_hari_ini = conn.execute("""
                SELECT COUNT(*) as c FROM pembelian
                WHERE created_at >= ?
            """, (today_start,)).fetchone()["c"]
            
            omset_total = conn.execute(
                "SELECT COALESCE(SUM(harga_bayar),0) as s FROM pembelian"
            ).fetchone()["s"]
            
            omset_hari  = conn.execute("""
                SELECT COALESCE(SUM(harga_bayar),0) as s FROM pembelian
                WHERE created_at >= ?
            """, (today_start,)).fetchone()["s"]
            
            garansi_pending = conn.execute(
                "SELECT COUNT(*) as c FROM garansi WHERE status IN ('pending','diproses')"
            ).fetchone()["c"]
            
            topup_hari = conn.execute("""
                SELECT COALESCE(SUM(jumlah),0) as s FROM topup
                WHERE status='completed' AND created_at >= ?
            """, (today_start,)).fetchone()["s"]
            
            stok_tersedia = conn.execute("SELECT COUNT(*) FROM stok_gmail WHERE terjual=0").fetchone()[0]

        res = {
            "total_user":      total_user,
            "total_saldo":     total_saldo,
            "total_trx":       total_trx,
            "trx_hari_ini":    trx_hari_ini,
            "omset_total":     omset_total,
            "omset_hari_ini":  omset_hari,
            "garansi_pending": garansi_pending,
            "topup_hari_ini":  topup_hari,
            "stok_tersedia":   stok_tersedia,
        }
        _admin_stats_cache["data"] = res
        _admin_stats_cache["last_updated"] = now
        return res


_store_stats_cache = {
    "data": None,
    "last_updated": 0
}
_store_stats_lock = threading.Lock()

def get_store_stats() -> dict:
    """Ambil statistik penjualan dan stok toko untuk home menu."""
    now = time.time()
    # Fast path: return cached data without acquiring lock
    if _store_stats_cache["data"] is not None and now - _store_stats_cache["last_updated"] < 15:
        return _store_stats_cache["data"]

    with _store_stats_lock:
        # Re-check after lock (another thread may have refreshed while we waited)
        now = time.time()
        if _store_stats_cache["data"] is not None and now - _store_stats_cache["last_updated"] < 15:
            return _store_stats_cache["data"]

        with get_connection() as conn:
            stok_tersedia = conn.execute("SELECT COUNT(*) FROM stok_gmail WHERE terjual=0").fetchone()[0]
            akun_terjual  = conn.execute("SELECT COUNT(*) FROM stok_gmail WHERE terjual=1").fetchone()[0]
            total_user    = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            total_trx     = conn.execute("SELECT COUNT(*) FROM pembelian").fetchone()[0]

        res = {
            "stok_tersedia": stok_tersedia,
            "akun_terjual":  akun_terjual,
            "total_user":    total_user,
            "total_trx":     total_trx
        }
        _store_stats_cache["data"] = res
        _store_stats_cache["last_updated"] = now
        return res


# ═══════════════════════════════════════════════════════════════════════════════
# SESSION IN-MEMORY
# ═══════════════════════════════════════════════════════════════════════════════

def get_session(user_id: int) -> dict:
    with _session_lock:
        cached = _session_cache.get(user_id, {"state": None, "data": {}})
        return {"state": cached["state"], "data": copy.deepcopy(cached["data"])}


def set_session(user_id: int, state: str, data: dict):
    from config import SESSION_CACHE_MAX_SIZE
    with _session_lock:
        if len(_session_cache) >= SESSION_CACHE_MAX_SIZE and user_id not in _session_cache:
            evict = max(1, SESSION_CACHE_MAX_SIZE // 10)
            for k in list(_session_cache.keys())[:evict]:
                _session_cache.pop(k, None)
        _session_cache[user_id] = {"state": state, "data": copy.deepcopy(data)}


def clear_session(user_id: int):
    with _session_lock:
        _session_cache.pop(user_id, None)


# ═══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

def get_setting(key: str, default=None) -> str | None:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, str(value)))
        conn.commit()


def rollback_stok(stok_ids: list):
    """Rollback status terjual stok (untuk fail-safe/retry)"""
    try:
        with get_connection() as conn:
            placeholders = ",".join("?" for _ in stok_ids)
            conn.execute(
                f"UPDATE stok_gmail SET terjual=0, terjual_at=NULL, terjual_ke=NULL WHERE id IN ({placeholders})",
                stok_ids
            )
            conn.commit()
        logger.info(f"[DB] Rolled back stock for IDs: {stok_ids}")
        return True
    except Exception as e:
        logger.error(f"[DB] Rollback stock failed: {e}")
        return False


def get_garansi_user_id(garansi_id: int):
    """Mendapatkan user_id dari data garansi berdasarkan id"""
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT user_id FROM garansi WHERE id=?", (garansi_id,)).fetchone()
            return row["user_id"] if row else None
    except Exception as e:
        logger.error(f"[DB] get_garansi_user_id exception: {e}")
        return None


def get_stok_totals():
    """Mendapatkan total stok tersedia dan terjual secara global"""
    try:
        with get_connection() as conn:
            total_tersedia = conn.execute("SELECT COUNT(*) FROM stok_gmail WHERE terjual=0").fetchone()[0]
            total_terjual  = conn.execute("SELECT COUNT(*) FROM stok_gmail WHERE terjual=1").fetchone()[0]
            return {"tersedia": total_tersedia, "terjual": total_terjual}
    except Exception as e:
        logger.error(f"[DB] get_stok_totals exception: {e}")
        return {"tersedia": 0, "terjual": 0}

def add_referral_timestamp_and_count(referrer_id: int, now: float, window_start: float) -> int:
    """
    Atomic: tambahkan timestamp referral baru dan hitung jumlah referral dalam window.
    Menggantikan pola JSON read-modify-write yang rawan race condition.
    Menggunakan transaksi SQLite eksplisit (BEGIN/COMMIT) karena pool berjalan dalam mode autocommit.
    Return jumlah referral dalam window setelah insert.
    """
    with get_connection() as conn:
        conn.execute("BEGIN")
        try:
            # Hapus entri lama (di luar window) untuk menjaga tabel tetap ramping
            conn.execute(
                "DELETE FROM referral_spam_log WHERE referrer_id=? AND ts < ?",
                (referrer_id, window_start)
            )
            # Insert entri baru
            conn.execute(
                "INSERT INTO referral_spam_log(referrer_id, ts) VALUES (?, ?)",
                (referrer_id, now)
            )
            # Hitung total dalam window (sudah termasuk yang baru diinsert)
            row = conn.execute(
                "SELECT COUNT(*) as c FROM referral_spam_log WHERE referrer_id=? AND ts >= ?",
                (referrer_id, window_start)
            ).fetchone()
            conn.execute("COMMIT")
            return row["c"] if row else 1
        except Exception as e:
            conn.execute("ROLLBACK")
            logger.error("[DB] add_referral_timestamp_and_count failed, rolled back: %s", e)
            raise e

