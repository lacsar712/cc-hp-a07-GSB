import json
import os
import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from rules import judge

SECRET = os.environ.get("JWT_SECRET", "herb-process-dev-secret")
DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54393/herb")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "processor": {"role": "writer", "password_hash": pwd.hash("herb123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


class LoginIn(BaseModel):
    username: str
    password: str


class StepIn(BaseModel):
    name: str
    temp_c: float
    minutes: float


class BatchIn(BaseModel):
    herb: str = Field(min_length=1, max_length=80)
    barcode: str = Field(min_length=1, max_length=80)
    steps: list[StepIn]


class BarcodeIn(BaseModel):
    herb: str = Field(min_length=1, max_length=80)
    window_start: datetime
    window_end: datetime


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=401, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅炮制员可写入记录")
    return user


app = FastAPI(title="饮片炮制记录台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS batches (
                id serial PRIMARY KEY,
                herb text NOT NULL,
                doc jsonb NOT NULL,
                verdict text NOT NULL,
                reason text NOT NULL,
                created_by text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sample_barcodes (
                id serial PRIMARY KEY,
                code text NOT NULL UNIQUE,
                herb text NOT NULL,
                window_start timestamptz NOT NULL,
                window_end timestamptz NOT NULL,
                status text NOT NULL DEFAULT 'active',
                created_by text NOT NULL,
                created_at timestamptz NOT NULL,
                used_at timestamptz,
                voided_at timestamptz
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS barcode_ledger (
                id serial PRIMARY KEY,
                barcode_id integer NOT NULL REFERENCES sample_barcodes(id),
                code text NOT NULL,
                event text NOT NULL,
                actor text NOT NULL,
                note text NOT NULL DEFAULT '',
                created_at timestamptz NOT NULL
            )"""
        )
        count = conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"]
        if count == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("甘草", {"steps": [{"name": "清炒", "temp_c": 120, "minutes": 12}]}),
                ("黄芩", {"steps": [{"name": "清炒", "temp_c": 40, "minutes": 12}]}),
            ]
            for herb, doc in samples:
                verdict, reason = judge(doc)
                conn.execute(
                    """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at)
                       VALUES (%s, %s::jsonb, %s, %s, %s, %s)""",
                    (herb, json.dumps(doc, ensure_ascii=False), verdict, reason, "processor", now),
                )
        conn.commit()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "herb-process-record"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = USERS.get(body.username.strip())
    if not user or not pwd.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({"sub": body.username.strip(), "role": user["role"], "exp": exp}, SECRET, algorithm="HS256")
    return {"access_token": token, "username": body.username.strip(), "role": user["role"]}


@app.get("/api/batches")
def list_batches(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute("SELECT id, herb, doc, verdict, reason, created_by FROM batches ORDER BY id DESC").fetchall()
    return rows


@app.post("/api/batches", status_code=201)
def create_batch(body: BatchIn, user: dict = Depends(require_writer)):
    doc = {"steps": [s.model_dump() for s in body.steps]}
    verdict, reason = judge(doc)
    now = datetime.now(timezone.utc)
    code = body.barcode.strip()
    with connect() as conn:
        barcode = conn.execute(
            "SELECT * FROM sample_barcodes WHERE code = %s FOR UPDATE", (code,)
        ).fetchone()
        if barcode is None:
            raise HTTPException(status_code=404, detail="留样条码不存在")
        if barcode["status"] == "voided":
            raise HTTPException(status_code=409, detail="留样条码已作废，拒写")
        if barcode["status"] == "used":
            raise HTTPException(status_code=409, detail="留样条码已使用，不可复用")
        if not barcode["window_start"] <= now <= barcode["window_end"]:
            raise HTTPException(status_code=409, detail="当前时刻不在预约取样窗口内，拒写")
        row = conn.execute(
            """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at)
               VALUES (%s, %s::jsonb, %s, %s, %s, %s)
               RETURNING id, herb, doc, verdict, reason, created_by""",
            (body.herb.strip(), json.dumps(doc, ensure_ascii=False), verdict, reason, user["username"], now),
        ).fetchone()
        conn.execute(
            "UPDATE sample_barcodes SET status = 'used', used_at = %s WHERE id = %s",
            (now, barcode["id"]),
        )
        conn.execute(
            """INSERT INTO barcode_ledger (barcode_id, code, event, actor, note, created_at)
               VALUES (%s, %s, 'used', %s, %s, %s)""",
            (barcode["id"], code, user["username"], f"写入批次 #{row['id']}", now),
        )
        conn.commit()
    return row


@app.post("/api/barcodes", status_code=201)
def create_barcode(body: BarcodeIn, user: dict = Depends(require_writer)):
    start = body.window_start
    end = body.window_end
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if end <= start:
        raise HTTPException(status_code=400, detail="窗口结束时刻必须晚于起始时刻")
    now = datetime.now(timezone.utc)
    code = f"LY{now:%Y%m%d}-{secrets.token_hex(4).upper()}"
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO sample_barcodes (code, herb, window_start, window_end, status, created_by, created_at)
               VALUES (%s, %s, %s, %s, 'active', %s, %s)
               RETURNING id, code, herb, window_start, window_end, status, created_by, created_at""",
            (code, body.herb.strip(), start, end, user["username"], now),
        ).fetchone()
        conn.execute(
            """INSERT INTO barcode_ledger (barcode_id, code, event, actor, note, created_at)
               VALUES (%s, %s, 'reserved', %s, %s, %s)""",
            (row["id"], code, user["username"], "预约生成", now),
        )
        conn.commit()
    return row


@app.get("/api/barcodes")
def list_barcodes(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, code, herb, window_start, window_end, status, created_by, created_at, used_at, voided_at
               FROM sample_barcodes ORDER BY id DESC"""
        ).fetchall()
    return rows


@app.get("/api/barcodes/ledger")
def list_barcode_ledger(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT l.id, l.code, b.herb, l.event, l.actor, l.note, l.created_at
               FROM barcode_ledger l
               JOIN sample_barcodes b ON b.id = l.barcode_id
               ORDER BY l.id DESC"""
        ).fetchall()
    return rows


@app.post("/api/barcodes/{barcode_id}/void")
def void_barcode(barcode_id: int, user: dict = Depends(require_writer)):
    now = datetime.now(timezone.utc)
    with connect() as conn:
        barcode = conn.execute(
            "SELECT * FROM sample_barcodes WHERE id = %s FOR UPDATE", (barcode_id,)
        ).fetchone()
        if barcode is None:
            raise HTTPException(status_code=404, detail="留样条码不存在")
        if barcode["status"] == "used":
            raise HTTPException(status_code=409, detail="留样条码已使用，不能作废")
        if barcode["status"] == "voided":
            raise HTTPException(status_code=409, detail="留样条码已作废")
        conn.execute(
            "UPDATE sample_barcodes SET status = 'voided', voided_at = %s WHERE id = %s",
            (now, barcode_id),
        )
        conn.execute(
            """INSERT INTO barcode_ledger (barcode_id, code, event, actor, note, created_at)
               VALUES (%s, %s, 'voided', %s, %s, %s)""",
            (barcode_id, barcode["code"], user["username"], "炮制员作废", now),
        )
        conn.commit()
    return {"id": barcode_id, "code": barcode["code"], "status": "voided"}
