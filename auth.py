"""
Inicio de sesión de los clientes.

Cada usuario (tabla `usuarios`) pertenece a un cliente y solo ve los datos de
ese cliente: sus balizas en PostgreSQL y sus buckets `<cliente>_*` de InfluxDB.

La sesión es un token firmado con HMAC (`usuario|cliente|caducidad|firma`), sin
tabla de sesiones. Viaja en la cookie `crops_session` (la pone POST /login; el
navegador la manda sola, también en el WebSocket) o en `Authorization: Bearer`.
"""
import base64
import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy import text

from config import SESSION_HOURS, SESSION_SECRET, SessionLocal

SESSION_COOKIE = "crops_session"


def hash_password(password: str, iterations: int = 200_000) -> str:
    """Formato pbkdf2_sha256$iteraciones$salt$hash (base64)."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


# Para comparar cuando el usuario no existe y tardar lo mismo que con uno real.
_DUMMY_HASH = hash_password(os.urandom(8).hex())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, expected = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(dk, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def authenticate(usuario: str, password: str) -> Optional[dict]:
    """Devuelve {usuario, cliente_id, nombre} si la contraseña es correcta."""
    db = SessionLocal()
    try:
        row = db.execute(
            text("""
                SELECT u.usuario, u.cliente_id, c.nombre, u.password_hash
                FROM usuarios u JOIN clientes c ON c.id = u.cliente_id
                WHERE u.usuario = :usuario
            """),
            {"usuario": usuario.strip().lower()},
        ).first()
    finally:
        db.close()

    if row is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, row[3]):
        return None
    return {"usuario": row[0], "cliente_id": row[1], "nombre": row[2]}


def _sign(payload: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def create_token(usuario: str, cliente_id: str) -> str:
    expires = int(time.time()) + SESSION_HOURS * 3600
    payload = f"{usuario}|{cliente_id}|{expires}"
    return f"{payload}|{_sign(payload)}"


def read_token(token: Optional[str]) -> Optional[dict]:
    """{usuario, cliente_id} si el token es válido y no ha caducado."""
    if not token:
        return None
    try:
        usuario, cliente_id, expires, signature = token.split("|")
        payload = f"{usuario}|{cliente_id}|{expires}"
        if not hmac.compare_digest(signature, _sign(payload)) or int(expires) < time.time():
            return None
    except ValueError:
        return None
    return {"usuario": usuario, "cliente_id": cliente_id}


def token_from(headers, cookies) -> Optional[str]:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return cookies.get(SESSION_COOKIE)


def current_session(request: Request) -> dict:
    """Dependencia de FastAPI: la sesión del usuario o 401."""
    session = read_token(token_from(request.headers, request.cookies))
    if session is None:
        raise HTTPException(status_code=401, detail="Inicia sesión")
    return session


def cliente_nombre(cliente_id: str) -> Optional[str]:
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT nombre FROM clientes WHERE id = :id"), {"id": cliente_id}).first()
        return row[0] if row else None
    finally:
        db.close()
