import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import bcrypt


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("djibshop")

ROOT_DIR = Path(__file__).resolve().parent.parent
PUBLIC_DIR = ROOT_DIR / "public"
PUBLIC_MEUBLES_DIR = ROOT_DIR / "public_meubles"
UPLOADS_DIR = PUBLIC_DIR / "uploads"
UPLOADS_MEUBLES_DIR = PUBLIC_MEUBLES_DIR / "uploads"
DATABASE_DIR = ROOT_DIR / "database"
DB_PATH = DATABASE_DIR / "djibshop.db"
SESSION_COOKIE = "djibshop_session"
SESSION_TTL_SECONDS = 60 * 60 * 12

# --- Credentials obligatoires (pas de valeurs par défaut en clair) ---
ADMIN_USERNAME = os.environ.get("DJIBSHOP_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("DJIBSHOP_ADMIN_PASSWORD")
SECRET_KEY = os.environ.get("DJIBSHOP_SECRET_KEY")

if not ADMIN_PASSWORD:
    raise RuntimeError(
        "Variable d'environnement DJIBSHOP_ADMIN_PASSWORD non définie. "
        "Définissez-la avant de lancer le serveur."
    )
if not SECRET_KEY:
    raise RuntimeError(
        "Variable d'environnement DJIBSHOP_SECRET_KEY non définie. "
        "Générez une clé forte (ex: python -c \"import secrets; print(secrets.token_hex(32))\")."
    )
if len(SECRET_KEY) < 32:
    raise RuntimeError("DJIBSHOP_SECRET_KEY doit faire au moins 32 caractères.")

# --- CORS ---
_raw_origins = os.environ.get("DJIBSHOP_ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = set(o.strip() for o in _raw_origins.split(",") if o.strip())

# --- Production mode ---
IS_PRODUCTION = os.environ.get("ENV", "").lower() == "production"

# --- Rate limiting login (en mémoire) ---
_login_attempts: dict[str, tuple[int, float]] = {}
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 300


def _check_login_rate_limit(ip: str) -> bool:
    now = time.monotonic()
    count, first = _login_attempts.get(ip, (0, now))
    if now - first > LOCKOUT_SECONDS:
        _login_attempts[ip] = (1, now)
        return True
    if count >= MAX_LOGIN_ATTEMPTS:
        return False
    _login_attempts[ip] = (count + 1, first)
    return True


def _reset_login_attempts(ip: str) -> None:
    _login_attempts.pop(ip, None)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_response(handler, payload, status=HTTPStatus.OK):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def create_session_token(username: str) -> str:
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    nonce = secrets.token_hex(12)
    payload = f"{username}:{expires_at}:{nonce}"
    signature = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def parse_session_token(token: str):
    try:
        username, expires_at, nonce, signature = token.split(":", 3)
        payload = f"{username}:{expires_at}:{nonce}"
    except ValueError:
        return None
    expected = hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    if int(expires_at) < int(time.time()):
        return None
    return {"username": username, "expires_at": int(expires_at)}


def get_cookie_session(handler):
    raw_cookie = handler.headers.get("Cookie")
    if not raw_cookie:
        return None
    cookies = SimpleCookie()
    cookies.load(raw_cookie)
    morsel = cookies.get(SESSION_COOKIE)
    if not morsel:
        return None
    return parse_session_token(morsel.value)


def _session_cookie_flags() -> str:
    base = f"HttpOnly; Path=/; Max-Age={SESSION_TTL_SECONDS}; SameSite=Lax"
    if IS_PRODUCTION:
        base += "; Secure"
    return base


def set_session_cookie(handler, token: str) -> None:
    handler.send_header("Set-Cookie", f"{SESSION_COOKIE}={token}; {_session_cookie_flags()}")


def clear_session_cookie(handler) -> None:
    handler.send_header("Set-Cookie", f"{SESSION_COOKIE}=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax")


def db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def table_columns(conn: sqlite3.Connection, table_name: str) -> set:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    if column_name in table_columns(conn, table_name):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def ensure_site_settings(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS site_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            home_background_url TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )
    ensure_column(conn, "site_settings", "home_background_url_meubles", "TEXT NOT NULL DEFAULT ''")
    existing = conn.execute("SELECT id FROM site_settings WHERE id = 1").fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO site_settings (id, home_background_url, home_background_url_meubles, updated_at) VALUES (1, '', '', ?)",
            (now_iso(),),
        )
        conn.commit()


def to_public_product(row) -> dict:
    return {
        "id": row["id"],
        "shop": row["shop"],
        "name": row["name"],
        "category": row["category"],
        "mattress_type": row["mattress_type"],
        "size_label": row["size_label"],
        "dimensions": row["dimensions"],
        "price_gnf": row["price_gnf"],
        "formatted_price": f"{row['price_gnf']:,}".replace(",", " ") + " GNF",
        "description": row["description"],
        "image_url": row["image_url"],
        "rating": row["rating"],
        "review_count": row["review_count"],
        "stock_status": row["stock_status"],
        "featured": bool(row["featured"]),
    }


def to_admin_order(row) -> dict:
    return {
        "id": row["id"],
        "shop": row["shop"],
        "customer_name": row["customer_name"],
        "phone": row["phone"],
        "address": row["address"],
        "city": row["city"],
        "payment_method": row["payment_method"],
        "status": row["status"],
        "notes": row["notes"],
        "product_name": row["product_name"],
        "product_id": row["product_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def to_admin_contact(row) -> dict:
    return {
        "id": row["id"],
        "shop": row["shop"],
        "full_name": row["full_name"],
        "phone": row["phone"],
        "message": row["message"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _detect_image_extension(file_bytes: bytes) -> str | None:
    """Vérifie les magic bytes réels du fichier image."""
    if file_bytes[:4] == b"\x89PNG":
        return ".png"
    if file_bytes[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if file_bytes[:4] == b"RIFF" and file_bytes[8:12] == b"WEBP":
        return ".webp"
    return None


def _delete_upload(url: str, shop: str = "matelas") -> None:
    """Supprime un fichier uploadé à partir de son URL relative (/uploads/...)."""
    if not url:
        return
    base_dir = PUBLIC_MEUBLES_DIR if shop == "meubles" else PUBLIC_DIR
    path = base_dir / url.lstrip("/")
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Impossible de supprimer le fichier %s : %s", path, exc)


def save_base64_image(image_data: str, filename_prefix: str, shop: str = "matelas") -> str:
    if not image_data:
        return ""
    if "," not in image_data:
        raise ValueError("Format image invalide.")

    _, encoded = image_data.split(",", 1)
    try:
        file_bytes = base64.b64decode(encoded, validate=True)
    except Exception:
        raise ValueError("Données image corrompues (base64 invalide).")

    if len(file_bytes) > 5 * 1024 * 1024:
        raise ValueError("L'image dépasse 5 MB.")

    extension = _detect_image_extension(file_bytes)
    if extension is None:
        raise ValueError("Type de fichier non autorisé. Formats acceptés : PNG, JPEG, WebP.")

    filename = f"{filename_prefix}-{int(time.time())}-{secrets.token_hex(4)}{extension}"
    target_dir = UPLOADS_MEUBLES_DIR if shop == "meubles" else UPLOADS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / filename
    output_path.write_bytes(file_bytes)
    return f"/uploads/{filename}"


def init_database() -> None:
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_MEUBLES_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_MEUBLES_DIR.mkdir(parents=True, exist_ok=True)

    conn = db_connection()
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop TEXT NOT NULL DEFAULT 'matelas',
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            mattress_type TEXT NOT NULL,
            size_label TEXT NOT NULL,
            dimensions TEXT NOT NULL,
            price_gnf INTEGER NOT NULL,
            description TEXT NOT NULL,
            image_url TEXT NOT NULL DEFAULT '',
            rating REAL NOT NULL DEFAULT 0,
            review_count INTEGER NOT NULL DEFAULT 0,
            stock_status TEXT NOT NULL DEFAULT 'En stock',
            featured INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop TEXT NOT NULL DEFAULT 'matelas',
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            city TEXT NOT NULL,
            payment_method TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'attente',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products (id)
        );

        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop TEXT NOT NULL DEFAULT 'matelas',
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'nouveau',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS site_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            home_background_url TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        """
    )

    ensure_column(conn, "orders", "updated_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "contacts", "status", "TEXT NOT NULL DEFAULT 'nouveau'")
    ensure_column(conn, "contacts", "updated_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "products", "shop", "TEXT NOT NULL DEFAULT 'matelas'")
    ensure_column(conn, "orders", "shop", "TEXT NOT NULL DEFAULT 'matelas'")
    ensure_column(conn, "contacts", "shop", "TEXT NOT NULL DEFAULT 'matelas'")
    conn.execute("UPDATE orders SET updated_at = created_at WHERE updated_at = '' OR updated_at IS NULL")
    conn.execute("UPDATE contacts SET status = 'nouveau' WHERE status = '' OR status IS NULL")
    conn.execute("UPDATE contacts SET updated_at = created_at WHERE updated_at = '' OR updated_at IS NULL")
    conn.execute("UPDATE products SET shop = 'matelas' WHERE shop = '' OR shop IS NULL")
    conn.execute("UPDATE orders SET shop = 'matelas' WHERE shop = '' OR shop IS NULL")
    conn.execute("UPDATE contacts SET shop = 'matelas' WHERE shop = '' OR shop IS NULL")

    ensure_site_settings(conn)

    existing_admin = cur.execute(
        "SELECT id, password_hash FROM admin_users WHERE username = ?", (ADMIN_USERNAME,)
    ).fetchone()
    if not existing_admin:
        cur.execute(
            "INSERT INTO admin_users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (ADMIN_USERNAME, hash_password(ADMIN_PASSWORD), now_iso()),
        )
    elif not existing_admin["password_hash"].startswith("$2b$"):
        logger.info("[SÉCURITÉ] Migration du hash admin de SHA-256 vers bcrypt...")
        cur.execute(
            "UPDATE admin_users SET password_hash = ? WHERE username = ?",
            (hash_password(ADMIN_PASSWORD), ADMIN_USERNAME),
        )

    product_count = cur.execute("SELECT COUNT(*) AS count FROM products").fetchone()["count"]
    if product_count == 0:
        seed_products = [
            (
                "Matelas Confort 160", "neuf", "ressort", "2 places", "160 x 200 cm", 1800000,
                "Matelas neuf en rouleau avec ressorts ensachés, soutien équilibré et accueil moelleux.",
                "", 4.5, 12, "En stock", 1,
            ),
            (
                "Bruxelles Queen", "occasion", "peu de ressort", "2 places", "160 x 190 cm", 900000,
                "Matelas d'occasion importé, confortable et soigneusement sélectionné pour un bon rapport qualité-prix.",
                "", 4.0, 8, "Stock limité", 1,
            ),
            (
                "Matelas Simple 90", "neuf", "sans ressort", "1 place", "90 x 190 cm", 650000,
                "Matelas mousse haute densité, pratique pour chambre d'enfant, étudiant ou lit simple.",
                "", 4.2, 5, "En stock", 0,
            ),
            (
                "Grand Confort Family", "neuf", "ressort", "4 places", "200 x 200 cm", 3200000,
                "Grand format premium pour un couchage spacieux et une excellente tenue dans le temps.",
                "", 5.0, 3, "Sur commande", 1,
            ),
        ]
        for item in seed_products:
            cur.execute(
                """
                INSERT INTO products (
                    name, category, mattress_type, size_label, dimensions, price_gnf, description,
                    image_url, rating, review_count, stock_status, featured, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*item, now_iso(), now_iso()),
            )

    conn.commit()
    conn.close()


def resolve_shop_and_path(path: str) -> tuple[str, str]:
    """Détecte la boutique (matelas/meubles) à partir du chemin API et retourne
    (shop, chemin_normalise) où chemin_normalise n'a plus le préfixe /meubles."""
    if path.startswith("/api/meubles/"):
        return "meubles", "/api/" + path[len("/api/meubles/"):]
    return "matelas", path


class DjibShopHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    def log_error(self, fmt, *args):
        logger.error("%s - %s", self.address_string(), fmt % args)

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if ALLOWED_ORIGINS and origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        elif not ALLOWED_ORIGINS:
            # Mode développement : pas d'ALLOWED_ORIGINS configuré
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Vary", "Origin")

    def _check_csrf(self) -> bool:
        """Vérifie que la requête provient bien du même site via l'en-tête Origin/Referer."""
        origin = self.headers.get("Origin", "")
        referer = self.headers.get("Referer", "")

        if not origin and not referer:
            return True

        if origin:
            if ALLOWED_ORIGINS:
                return origin in ALLOWED_ORIGINS
            host = self.headers.get("Host", "")
            return not origin or origin.endswith(host)

        if referer:
            host = self.headers.get("Host", "")
            ref_parsed = urlparse(referer)
            return ref_parsed.netloc == host

        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return self.handle_api_get(parsed)
        if parsed.path in ("/meubles", "/meubles/"):
            self.directory = str(PUBLIC_MEUBLES_DIR)
            self.path = "/index.html"
            return SimpleHTTPRequestHandler.do_GET(self)
        if parsed.path.startswith("/meubles/"):
            self.directory = str(PUBLIC_MEUBLES_DIR)
            self.path = parsed.path[len("/meubles"):]
            return SimpleHTTPRequestHandler.do_GET(self)
        self.directory = str(PUBLIC_DIR)
        if parsed.path in ("/", "/matelas", "/matelas/"):
            self.path = "/index.html"
        return SimpleHTTPRequestHandler.do_GET(self)

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return json_response(self, {"error": "Route introuvable."}, HTTPStatus.NOT_FOUND)
        return self.handle_api_post(parsed)

    def do_PUT(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return json_response(self, {"error": "Route introuvable."}, HTTPStatus.NOT_FOUND)
        return self.handle_api_put(parsed)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return json_response(self, {"error": "Route introuvable."}, HTTPStatus.NOT_FOUND)
        return self.handle_api_delete(parsed)

    def parse_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise ValueError("Corps JSON invalide.")

    def require_admin(self):
        session = get_cookie_session(self)
        if not session:
            json_response(self, {"error": "Authentification requise."}, HTTPStatus.UNAUTHORIZED)
            return None
        return session

    def handle_api_get(self, parsed):
        shop, path = resolve_shop_and_path(parsed.path)
        if path == "/api/public/products":
            return self.public_products(shop)
        if path == "/api/public/site":
            return self.public_site_info(shop)
        if path == "/api/admin/dashboard":
            if not self.require_admin():
                return
            return self.admin_dashboard(shop)
        if path == "/api/admin/products":
            if not self.require_admin():
                return
            return self.admin_products(shop)
        if path == "/api/admin/orders":
            if not self.require_admin():
                return
            return self.admin_orders(shop)
        if path == "/api/admin/contacts":
            if not self.require_admin():
                return
            return self.admin_contacts(shop)
        return json_response(self, {"error": "Route API introuvable."}, HTTPStatus.NOT_FOUND)

    def handle_api_post(self, parsed):
        shop, path = resolve_shop_and_path(parsed.path)
        if path == "/api/public/orders":
            return self.create_order(shop)
        if path == "/api/public/contact":
            return self.create_contact(shop)
        if path == "/api/admin/login":
            return self.admin_login()
        if path == "/api/admin/logout":
            self.send_response(HTTPStatus.OK)
            self._cors_headers()
            clear_session_cookie(self)
            body = json.dumps({"success": True}, ensure_ascii=False).encode("utf-8")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/admin/products":
            if not self.require_admin():
                return
            if not self._check_csrf():
                return json_response(self, {"error": "Requête non autorisée."}, HTTPStatus.FORBIDDEN)
            return self.create_product(shop)
        return json_response(self, {"error": "Route API introuvable."}, HTTPStatus.NOT_FOUND)

    def handle_api_put(self, parsed):
        shop, path = resolve_shop_and_path(parsed.path)
        if path.startswith("/api/admin/products/"):
            if not self.require_admin():
                return
            if not self._check_csrf():
                return json_response(self, {"error": "Requête non autorisée."}, HTTPStatus.FORBIDDEN)
            product_id = path.rsplit("/", 1)[-1]
            return self.update_product(product_id, shop)
        if path.startswith("/api/admin/orders/"):
            if not self.require_admin():
                return
            if not self._check_csrf():
                return json_response(self, {"error": "Requête non autorisée."}, HTTPStatus.FORBIDDEN)
            order_id = path.rsplit("/", 1)[-1]
            return self.update_order(order_id, shop)
        if path.startswith("/api/admin/contacts/"):
            if not self.require_admin():
                return
            if not self._check_csrf():
                return json_response(self, {"error": "Requête non autorisée."}, HTTPStatus.FORBIDDEN)
            contact_id = path.rsplit("/", 1)[-1]
            return self.update_contact(contact_id, shop)
        if path == "/api/admin/site":
            if not self.require_admin():
                return
            if not self._check_csrf():
                return json_response(self, {"error": "Requête non autorisée."}, HTTPStatus.FORBIDDEN)
            return self.update_site_settings(shop)
        return json_response(self, {"error": "Route API introuvable."}, HTTPStatus.NOT_FOUND)

    def handle_api_delete(self, parsed):
        shop, path = resolve_shop_and_path(parsed.path)
        if path.startswith("/api/admin/products/"):
            if not self.require_admin():
                return
            if not self._check_csrf():
                return json_response(self, {"error": "Requête non autorisée."}, HTTPStatus.FORBIDDEN)
            product_id = path.rsplit("/", 1)[-1]
            return self.delete_product(product_id, shop)
        if path.startswith("/api/admin/orders/"):
            if not self.require_admin():
                return
            if not self._check_csrf():
                return json_response(self, {"error": "Requête non autorisée."}, HTTPStatus.FORBIDDEN)
            order_id = path.rsplit("/", 1)[-1]
            return self.delete_order(order_id, shop)
        if path.startswith("/api/admin/contacts/"):
            if not self.require_admin():
                return
            if not self._check_csrf():
                return json_response(self, {"error": "Requête non autorisée."}, HTTPStatus.FORBIDDEN)
            contact_id = path.rsplit("/", 1)[-1]
            return self.delete_contact(contact_id, shop)
        return json_response(self, {"error": "Route API introuvable."}, HTTPStatus.NOT_FOUND)

    def public_site_info(self, shop="matelas"):
        conn = db_connection()
        ensure_site_settings(conn)
        column = "home_background_url" if shop == "matelas" else "home_background_url_meubles"
        settings = conn.execute(f"SELECT {column} AS bg FROM site_settings WHERE id = 1").fetchone()
        conn.close()
        if shop == "meubles":
            payload = {
                "brand": "DjibShop",
                "headline": "Meubles de qualité livrés partout en Guinée",
                "subheadline": "Des meubles neufs sélectionnés pour le confort et le style, livrés avec accompagnement avant et après commande.",
                "phone_primary": "+224 610 49 23 45",
                "phone_secondary": "+224 620 74 47 03",
                "whatsapp_number": "224610492345",
                "city": "Conakry",
                "delivery_area": "Partout en Guinée",
                "home_background_url": settings["bg"] if settings else "",
                "about": {
                    "title": "À propos de DjibShop Meubles",
                    "body": "DjibShop Meubles vous propose des lits, armoires, coiffeuses et étagères neufs pour équiper votre maison en Guinée.",
                },
            }
        else:
            payload = {
                "brand": "DjibShop",
                "headline": "Matelas de qualité livrés partout en Guinée",
                "subheadline": "Des matelas neufs et d'occasion, sélectionnés pour le confort, la durabilité et le bon prix.",
                "phone_primary": "+224 610 49 23 45",
                "phone_secondary": "+224 620 74 47 03",
                "whatsapp_number": "224610492345",
                "city": "Conakry",
                "delivery_area": "Partout en Guinée",
                "home_background_url": settings["bg"] if settings else "",
                "about": {
                    "title": "À propos de DjibShop",
                    "body": "DjibShop accompagne les familles, hôtels et particuliers dans le choix de matelas adaptés à leur budget et à leur confort. Nous privilégions la transparence, le conseil et la livraison rapide.",
                },
            }
        return json_response(self, payload)

    def public_products(self, shop="matelas"):
        conn = db_connection()
        filters = parse_qs(urlparse(self.path).query)
        query = "SELECT * FROM products WHERE shop = ?"
        params = [shop]
        for field in ("category", "mattress_type", "size_label"):
            if filters.get(field):
                query += f" AND {field} = ?"
                params.append(filters[field][0])
        query += " ORDER BY featured DESC, id DESC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return json_response(self, {"products": [to_public_product(row) for row in rows]})

    def admin_products(self, shop="matelas"):
        conn = db_connection()
        rows = conn.execute("SELECT * FROM products WHERE shop = ? ORDER BY id DESC", (shop,)).fetchall()
        conn.close()
        return json_response(self, {"products": [to_public_product(row) for row in rows]})

    def admin_orders(self, shop="matelas"):
        conn = db_connection()
        rows = conn.execute("SELECT * FROM orders WHERE shop = ? ORDER BY id DESC", (shop,)).fetchall()
        conn.close()
        return json_response(self, {"orders": [to_admin_order(row) for row in rows]})

    def admin_contacts(self, shop="matelas"):
        conn = db_connection()
        rows = conn.execute("SELECT * FROM contacts WHERE shop = ? ORDER BY id DESC", (shop,)).fetchall()
        conn.close()
        return json_response(self, {"contacts": [to_admin_contact(row) for row in rows]})

    def admin_dashboard(self, shop="matelas"):
        conn = db_connection()
        metrics = {
            "products": conn.execute("SELECT COUNT(*) AS c FROM products WHERE shop = ?", (shop,)).fetchone()["c"],
            "orders": conn.execute("SELECT COUNT(*) AS c FROM orders WHERE shop = ?", (shop,)).fetchone()["c"],
            "pending_orders": conn.execute("SELECT COUNT(*) AS c FROM orders WHERE shop = ? AND status = 'attente'", (shop,)).fetchone()["c"],
            "delivered_orders": conn.execute("SELECT COUNT(*) AS c FROM orders WHERE shop = ? AND status = 'livree'", (shop,)).fetchone()["c"],
            "contacts": conn.execute("SELECT COUNT(*) AS c FROM contacts WHERE shop = ?", (shop,)).fetchone()["c"],
        }
        conn.close()
        return json_response(self, {"metrics": metrics, "admin_username": ADMIN_USERNAME})

    def admin_login(self):
        client_ip = self.address_string()
        if not _check_login_rate_limit(client_ip):
            logger.warning("Login bloqué (rate limit) pour IP %s", client_ip)
            return json_response(
                self,
                {"error": "Trop de tentatives. Réessayez dans 5 minutes."},
                HTTPStatus.TOO_MANY_REQUESTS,
            )

        try:
            body = self.parse_json_body()
        except ValueError as exc:
            return json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        username = body.get("username", "").strip()
        password = body.get("password", "")
        conn = db_connection()
        user = conn.execute(
            "SELECT * FROM admin_users WHERE username = ?", (username,)
        ).fetchone()
        conn.close()

        if not user or not verify_password(password, user["password_hash"]):
            logger.warning("Échec de connexion admin pour utilisateur '%s' depuis %s", username, client_ip)
            return json_response(self, {"error": "Identifiants invalides."}, HTTPStatus.UNAUTHORIZED)

        _reset_login_attempts(client_ip)
        logger.info("Connexion admin réussie pour '%s' depuis %s", username, client_ip)
        token = create_session_token(username)
        body_bytes = json.dumps({"success": True, "username": username}, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self._cors_headers()
        set_session_cookie(self, token)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def create_order(self, shop="matelas"):
        try:
            body = self.parse_json_body()
        except ValueError as exc:
            return json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        required = ["product_id", "customer_name", "phone", "address", "city", "payment_method"]
        missing = [field for field in required if not str(body.get(field, "")).strip()]
        if missing:
            return json_response(
                self, {"error": "Champs obligatoires manquants.", "fields": missing}, HTTPStatus.BAD_REQUEST
            )

        conn = db_connection()
        product = conn.execute(
            "SELECT id, name FROM products WHERE id = ? AND shop = ?", (body["product_id"], shop)
        ).fetchone()
        if not product:
            conn.close()
            return json_response(self, {"error": "Produit introuvable."}, HTTPStatus.NOT_FOUND)

        cur = conn.execute(
            """
            INSERT INTO orders (
                shop, product_id, product_name, customer_name, phone, address, city,
                payment_method, status, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'attente', ?, ?, ?)
            """,
            (
                shop,
                product["id"],
                product["name"],
                body["customer_name"].strip(),
                body["phone"].strip(),
                body["address"].strip(),
                body["city"].strip(),
                body["payment_method"].strip(),
                body.get("notes", "").strip(),
                now_iso(),
                now_iso(),
            ),
        )
        conn.commit()
        order_id = cur.lastrowid
        conn.close()
        logger.info("Nouvelle commande #%s (%s) pour le produit '%s'", order_id, shop, product["name"])
        return json_response(
            self,
            {
                "success": True,
                "order_id": order_id,
                "message": "Commande bien enregistrée. Nous vous répondrons dans un bref délai.",
            },
            HTTPStatus.CREATED,
        )

    def create_contact(self, shop="matelas"):
        try:
            body = self.parse_json_body()
        except ValueError as exc:
            return json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        required = ["full_name", "phone", "message"]
        missing = [field for field in required if not str(body.get(field, "")).strip()]
        if missing:
            return json_response(
                self, {"error": "Champs obligatoires manquants.", "fields": missing}, HTTPStatus.BAD_REQUEST
            )

        conn = db_connection()
        cur = conn.execute(
            "INSERT INTO contacts (shop, full_name, phone, message, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'nouveau', ?, ?)",
            (shop, body["full_name"].strip(), body["phone"].strip(), body["message"].strip(), now_iso(), now_iso()),
        )
        conn.commit()
        conn.close()
        return json_response(
            self,
            {
                "success": True,
                "contact_id": cur.lastrowid,
                "message": "Message bien enregistré. Nous vous répondrons dans un bref délai.",
            },
            HTTPStatus.CREATED,
        )

    def validate_product_payload(self, body: dict) -> int:
        required = ["name", "category", "mattress_type", "size_label", "dimensions", "price_gnf", "description", "stock_status"]
        missing = [field for field in required if not str(body.get(field, "")).strip()]
        if missing:
            raise ValueError("Certains champs produit sont manquants.")
        try:
            price = int(str(body["price_gnf"]).replace(" ", ""))
        except ValueError as exc:
            raise ValueError("Le prix doit être numérique.") from exc
        if price <= 0:
            raise ValueError("Le prix doit être supérieur à zéro.")
        return price

    def create_product(self, shop="matelas"):
        try:
            body = self.parse_json_body()
            price = self.validate_product_payload(body)
            image_url = save_base64_image(body.get("image_data", ""), "product", shop) if body.get("image_data") else ""
        except ValueError as exc:
            return json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        conn = db_connection()
        cur = conn.execute(
            """
            INSERT INTO products (
                shop, name, category, mattress_type, size_label, dimensions, price_gnf,
                description, image_url, rating, review_count, stock_status, featured,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                shop,
                body["name"].strip(),
                body["category"].strip(),
                body["mattress_type"].strip(),
                body["size_label"].strip(),
                body["dimensions"].strip(),
                price,
                body["description"].strip(),
                image_url,
                float(body.get("rating") or 0),
                int(body.get("review_count") or 0),
                body["stock_status"].strip(),
                1 if body.get("featured") else 0,
                now_iso(),
                now_iso(),
            ),
        )
        conn.commit()
        product_id = cur.lastrowid
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        conn.close()
        return json_response(self, {"success": True, "product": to_public_product(row)}, HTTPStatus.CREATED)

    def update_product(self, product_id, shop="matelas"):
        try:
            body = self.parse_json_body()
            price = self.validate_product_payload(body)
        except ValueError as exc:
            return json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        conn = db_connection()
        existing = conn.execute("SELECT * FROM products WHERE id = ? AND shop = ?", (product_id, shop)).fetchone()
        if not existing:
            conn.close()
            return json_response(self, {"error": "Produit introuvable."}, HTTPStatus.NOT_FOUND)

        image_url = existing["image_url"]
        if body.get("image_data"):
            try:
                new_image_url = save_base64_image(body["image_data"], "product", shop)
            except ValueError as exc:
                conn.close()
                return json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            # Supprimer l'ancienne image pour éviter l'accumulation de fichiers orphelins
            _delete_upload(existing["image_url"], shop)
            image_url = new_image_url

        conn.execute(
            """
            UPDATE products
            SET name = ?, category = ?, mattress_type = ?, size_label = ?, dimensions = ?, price_gnf = ?,
                description = ?, image_url = ?, rating = ?, review_count = ?, stock_status = ?, featured = ?, updated_at = ?
            WHERE id = ? AND shop = ?
            """,
            (
                body["name"].strip(),
                body["category"].strip(),
                body["mattress_type"].strip(),
                body["size_label"].strip(),
                body["dimensions"].strip(),
                price,
                body["description"].strip(),
                image_url,
                float(body.get("rating") or 0),
                int(body.get("review_count") or 0),
                body["stock_status"].strip(),
                1 if body.get("featured") else 0,
                now_iso(),
                product_id,
                shop,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        conn.close()
        return json_response(self, {"success": True, "product": to_public_product(row)})

    def update_order(self, order_id, shop="matelas"):
        try:
            body = self.parse_json_body()
        except ValueError as exc:
            return json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        status = body.get("status", "").strip()
        allowed = {"attente", "confirmee", "livraison", "livree", "annulee"}
        if status not in allowed:
            return json_response(self, {"error": "Statut invalide."}, HTTPStatus.BAD_REQUEST)

        conn = db_connection()
        existing = conn.execute("SELECT id FROM orders WHERE id = ? AND shop = ?", (order_id, shop)).fetchone()
        if not existing:
            conn.close()
            return json_response(self, {"error": "Commande introuvable."}, HTTPStatus.NOT_FOUND)
        conn.execute("UPDATE orders SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), order_id))
        conn.commit()
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        conn.close()
        return json_response(self, {"success": True, "order": to_admin_order(row)})

    def update_contact(self, contact_id, shop="matelas"):
        try:
            body = self.parse_json_body()
        except ValueError as exc:
            return json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        status = body.get("status", "").strip()
        allowed = {"nouveau", "traite"}
        if status not in allowed:
            return json_response(self, {"error": "Statut invalide."}, HTTPStatus.BAD_REQUEST)

        conn = db_connection()
        existing = conn.execute("SELECT id FROM contacts WHERE id = ? AND shop = ?", (contact_id, shop)).fetchone()
        if not existing:
            conn.close()
            return json_response(self, {"error": "Message introuvable."}, HTTPStatus.NOT_FOUND)
        conn.execute("UPDATE contacts SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), contact_id))
        conn.commit()
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        conn.close()
        return json_response(self, {"success": True, "contact": to_admin_contact(row)})

    def update_site_settings(self, shop="matelas"):
        try:
            body = self.parse_json_body()
        except ValueError as exc:
            return json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        clear_background = bool(body.get("clear_background"))
        image_data = body.get("image_data", "")
        if not clear_background and not image_data:
            return json_response(self, {"error": "Aucune image fournie."}, HTTPStatus.BAD_REQUEST)

        column = "home_background_url" if shop == "matelas" else "home_background_url_meubles"

        conn = db_connection()
        ensure_site_settings(conn)
        background_url = ""
        if not clear_background:
            try:
                background_url = save_base64_image(image_data, "home-background", shop)
            except ValueError as exc:
                conn.close()
                return json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            # Supprimer l'ancienne image de fond
            old = conn.execute(f"SELECT {column} AS bg FROM site_settings WHERE id = 1").fetchone()
            if old:
                _delete_upload(old["bg"], shop)

        conn.execute(
            f"UPDATE site_settings SET {column} = ?, updated_at = ? WHERE id = 1",
            (background_url, now_iso()),
        )
        conn.commit()
        conn.close()
        return json_response(
            self,
            {
                "success": True,
                "home_background_url": background_url,
                "message": "Fond de la page d'accueil mis à jour.",
            },
        )

    def delete_product(self, product_id, shop="matelas"):
        conn = db_connection()
        existing = conn.execute("SELECT * FROM products WHERE id = ? AND shop = ?", (product_id, shop)).fetchone()
        if not existing:
            conn.close()
            return json_response(self, {"error": "Produit introuvable."}, HTTPStatus.NOT_FOUND)
        conn.execute("DELETE FROM products WHERE id = ? AND shop = ?", (product_id, shop))
        conn.commit()
        conn.close()
        # Supprimer l'image associée
        _delete_upload(existing["image_url"], shop)
        return json_response(self, {"success": True})

    def delete_order(self, order_id, shop="matelas"):
        conn = db_connection()
        existing = conn.execute("SELECT id FROM orders WHERE id = ? AND shop = ?", (order_id, shop)).fetchone()
        if not existing:
            conn.close()
            return json_response(self, {"error": "Commande introuvable."}, HTTPStatus.NOT_FOUND)
        conn.execute("DELETE FROM orders WHERE id = ? AND shop = ?", (order_id, shop))
        conn.commit()
        conn.close()
        return json_response(self, {"success": True})

    def delete_contact(self, contact_id, shop="matelas"):
        conn = db_connection()
        existing = conn.execute("SELECT id FROM contacts WHERE id = ? AND shop = ?", (contact_id, shop)).fetchone()
        if not existing:
            conn.close()
            return json_response(self, {"error": "Message introuvable."}, HTTPStatus.NOT_FOUND)
        conn.execute("DELETE FROM contacts WHERE id = ? AND shop = ?", (contact_id, shop))
        conn.commit()
        conn.close()
        return json_response(self, {"success": True})


def run():
    init_database()
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, port), DjibShopHandler)
    logger.info("DjibShop démarré sur http://%s:%d", host, port)
    logger.info("Mode production : %s", IS_PRODUCTION)
    server.serve_forever()


if __name__ == "__main__":
    run()
