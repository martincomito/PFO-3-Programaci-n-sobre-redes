import argparse
import concurrent.futures
import hashlib
import hmac
import json
import secrets
import socket
import sqlite3
import threading
from datetime import datetime


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001
DEFAULT_WORKERS = 4
DATABASE = "tareas.db"

db_lock = threading.Lock()


def init_db():
    with sqlite3.connect(DATABASE) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT UNIQUE NOT NULL,
                salt TEXT NOT NULL,
                contrasena_hash TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tareas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL,
                descripcion TEXT NOT NULL,
                creada_en TEXT NOT NULL,
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
            );
            """
        )
        user_columns = {
            row[1] for row in db.execute("PRAGMA table_info(usuarios)").fetchall()
        }
        if "salt" not in user_columns:
            db.execute("ALTER TABLE usuarios ADD COLUMN salt TEXT NOT NULL DEFAULT ''")
        db.commit()


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000
    ).hex()
    return salt, password_hash


def verify_password(password, salt, expected_hash):
    _, password_hash = hash_password(password, salt)
    return hmac.compare_digest(password_hash, expected_hash)


def success(**data):
    response = {"ok": True}
    response.update(data)
    return response


def error(message):
    return {"ok": False, "error": message}


def get_required_text(request_data, field):
    value = request_data.get(field)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def authenticate(db, username, password):
    row = db.execute(
        "SELECT id, salt, contrasena_hash FROM usuarios WHERE usuario = ?",
        (username,),
    ).fetchone()
    if row is None:
        return None

    user_id, salt, stored_hash = row
    if not verify_password(password, salt, stored_hash):
        return None

    return user_id


def register_user(request_data):
    username = get_required_text(request_data, "usuario")
    password = get_required_text(request_data, "contrasena")

    if username is None or password is None:
        return error("Se requieren 'usuario' y 'contrasena'.")
    if len(username) < 3:
        return error("El usuario debe tener al menos 3 caracteres.")
    if len(password) < 4:
        return error("La contrasena debe tener al menos 4 caracteres.")

    salt, password_hash = hash_password(password)
    try:
        with db_lock, sqlite3.connect(DATABASE) as db:
            db.execute(
                """
                INSERT INTO usuarios (usuario, salt, contrasena_hash)
                VALUES (?, ?, ?)
                """,
                (username, salt, password_hash),
            )
            db.commit()
    except sqlite3.IntegrityError:
        return error("El usuario ya existe.")

    return success(mensaje=f"Usuario '{username}' registrado exitosamente.")


def login(request_data):
    username = get_required_text(request_data, "usuario")
    password = get_required_text(request_data, "contrasena")

    if username is None or password is None:
        return error("Se requieren 'usuario' y 'contrasena'.")

    with sqlite3.connect(DATABASE) as db:
        user_id = authenticate(db, username, password)

    if user_id is None:
        return error("Credenciales invalidas.")

    return success(mensaje=f"Bienvenido, {username}!")


def require_auth(request_data):
    username = get_required_text(request_data, "usuario")
    password = get_required_text(request_data, "contrasena")

    if username is None or password is None:
        return None, error("Necesitas iniciar sesion primero.")

    with sqlite3.connect(DATABASE) as db:
        user_id = authenticate(db, username, password)

    if user_id is None:
        return None, error("Credenciales invalidas.")

    return user_id, None


def create_task(request_data):
    user_id, auth_error = require_auth(request_data)
    if auth_error:
        return auth_error

    description = get_required_text(request_data, "descripcion")
    if description is None:
        return error("Se requiere 'descripcion'.")

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_lock, sqlite3.connect(DATABASE) as db:
        cursor = db.execute(
            """
            INSERT INTO tareas (usuario_id, descripcion, creada_en)
            VALUES (?, ?, ?)
            """,
            (user_id, description, created_at),
        )
        db.commit()

    return success(
        mensaje="Tarea creada.",
        tarea={"id": cursor.lastrowid, "descripcion": description, "creada_en": created_at},
    )


def list_tasks(request_data):
    user_id, auth_error = require_auth(request_data)
    if auth_error:
        return auth_error

    with sqlite3.connect(DATABASE) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            SELECT id, descripcion, creada_en
            FROM tareas
            WHERE usuario_id = ?
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()

    return success(tareas=[dict(row) for row in rows])


def delete_task(request_data):
    user_id, auth_error = require_auth(request_data)
    if auth_error:
        return auth_error

    task_id = request_data.get("id")
    if not isinstance(task_id, int):
        return error("Se requiere un 'id' numerico.")

    with db_lock, sqlite3.connect(DATABASE) as db:
        cursor = db.execute(
            "DELETE FROM tareas WHERE id = ? AND usuario_id = ?",
            (task_id, user_id),
        )
        db.commit()

    if cursor.rowcount == 0:
        return error("Tarea no encontrada o no te pertenece.")

    return success(mensaje=f"Tarea #{task_id} eliminada.")


def process_request(request_data):
    action = request_data.get("accion")
    handlers = {
        "registrar": register_user,
        "login": login,
        "crear_tarea": create_task,
        "listar_tareas": list_tasks,
        "eliminar_tarea": delete_task,
    }

    handler = handlers.get(action)
    if handler is None:
        return error("Accion no reconocida.")

    return handler(request_data)


def send_json(file, response):
    file.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
    file.flush()


def handle_client(connection, address, worker_pool):
    print(f"Cliente conectado: {address[0]}:{address[1]}")
    with connection:
        file = connection.makefile("rwb")
        for raw_line in file:
            try:
                request_data = json.loads(raw_line.decode("utf-8"))
                future = worker_pool.submit(process_request, request_data)
                response = future.result()
            except json.JSONDecodeError:
                response = error("El mensaje recibido no es JSON valido.")
            except Exception as exc:
                response = error(f"Error interno del servidor: {exc}")

            send_json(file, response)

    print(f"Cliente desconectado: {address[0]}:{address[1]}")


def run_server(host, port, max_workers):
    init_db()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as worker_pool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind((host, port))
            server_socket.listen()

            print(f"Servidor escuchando en {host}:{port}")
            print(f"Pool de workers activo con {max_workers} hilos")

            while True:
                connection, address = server_socket.accept()
                client_thread = threading.Thread(
                    target=handle_client,
                    args=(connection, address, worker_pool),
                    daemon=True,
                )
                client_thread.start()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Servidor TCP para gestion distribuida de tareas."
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_server(args.host, args.port, args.workers)
