import json
import os
import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
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
    barcode: str = Field(min_length=1, max_length=64)
    steps: list[StepIn]


class BarcodeIn(BaseModel):
    herb: str = Field(min_length=1, max_length=80)
    window_start: datetime
    window_end: datetime


class VoidIn(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


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
        raise HTTPException(status_code=403, detail="仅炮制员可执行该操作")
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
                created_at timestamptz NOT NULL,
                barcode text
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS barcodes (
                id serial PRIMARY KEY,
                code text UNIQUE NOT NULL,
                herb text NOT NULL,
                window_start timestamptz NOT NULL,
                window_end timestamptz NOT NULL,
                status text NOT NULL DEFAULT 'active',
                created_by text NOT NULL,
                created_at timestamptz NOT NULL,
                used_at timestamptz,
                used_by_batch integer
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS barcode_voids (
                id serial PRIMARY KEY,
                barcode_id integer NOT NULL,
                code text NOT NULL,
                herb text NOT NULL,
                reason text NOT NULL,
                voided_by text NOT NULL,
                voided_at timestamptz NOT NULL
            )"""
        )
        conn.execute("ALTER TABLE batches ADD COLUMN IF NOT EXISTS barcode text")
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
        rows = conn.execute(
            "SELECT id, herb, doc, verdict, reason, created_by, barcode FROM batches ORDER BY id DESC"
        ).fetchall()
    return rows


@app.post("/api/batches", status_code=201)
def create_batch(body: BatchIn, user: dict = Depends(require_writer)):
    doc = {"steps": [s.model_dump() for s in body.steps]}
    verdict, reason = judge(doc)
    now = datetime.now(timezone.utc)
    with connect() as conn:
        try:
            barcode = conn.execute(
                "SELECT * FROM barcodes WHERE code = %s FOR UPDATE", (body.barcode.strip(),)
            ).fetchone()
        except psycopg.Error as exc:
            raise HTTPException(status_code=400, detail="条码查询失败") from exc
        if barcode is None:
            raise HTTPException(status_code=400, detail="留样条码不存在，拒写")
        if barcode["status"] == "voided":
            raise HTTPException(status_code=403, detail="留样条码已作废，拒写")
        if barcode["status"] == "used":
            raise HTTPException(status_code=409, detail="留样条码已使用，不可复用")
        if barcode["herb"] != body.herb.strip():
            raise HTTPException(status_code=400, detail="条码绑定饮片与记录饮片不一致，拒写")
        if not (barcode["window_start"] <= now <= barcode["window_end"]):
            raise HTTPException(status_code=400, detail="写入时刻不在取样窗口内，拒写")
        row = conn.execute(
            """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at, barcode)
               VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s)
               RETURNING id, herb, doc, verdict, reason, created_by, barcode""",
            (body.herb.strip(), json.dumps(doc, ensure_ascii=False), verdict, reason,
             user["username"], now, barcode["code"]),
        ).fetchone()
        conn.execute(
            "UPDATE barcodes SET status = 'used', used_at = %s, used_by_batch = %s WHERE id = %s",
            (now, row["id"], barcode["id"]),
        )
        conn.commit()
    return row


@app.post("/api/barcodes", status_code=201)
def reserve_barcode(body: BarcodeIn, user: dict = Depends(require_writer)):
    if body.window_start.tzinfo is None or body.window_end.tzinfo is None:
        raise HTTPException(status_code=400, detail="窗口起止时刻须带时区")
    if body.window_end <= body.window_start:
        raise HTTPException(status_code=400, detail="窗口结束时刻须晚于开始时刻")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        for _ in range(3):
            code = f"LY{now:%Y%m%d}-{secrets.token_hex(4).upper()}"
            try:
                row = conn.execute(
                    """INSERT INTO barcodes (code, herb, window_start, window_end, created_by, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       RETURNING id, code, herb, window_start, window_end, status, created_by, created_at""",
                    (code, body.herb.strip(), body.window_start, body.window_end, user["username"], now),
                ).fetchone()
                conn.commit()
                return row
            except psycopg.errors.UniqueViolation:
                conn.rollback()
        raise HTTPException(status_code=500, detail="条码生成冲突，请重试")


@app.get("/api/barcodes")
def list_barcodes(
    scope: str = Query("active", pattern="^(active|used|voided|all)$"),
    _user: dict = Depends(current_user),
):
    sql = """SELECT id, code, herb, window_start, window_end, status,
                    created_by, created_at, used_at, used_by_batch
             FROM barcodes {where} ORDER BY id DESC"""
    where = "" if scope == "all" else "WHERE status = %s"
    with connect() as conn:
        rows = conn.execute(sql.format(where=where), (scope,) if where else None).fetchall()
    return rows


@app.post("/api/barcodes/{code}/void", status_code=201)
def void_barcode(code: str, body: VoidIn, user: dict = Depends(require_writer)):
    now = datetime.now(timezone.utc)
    with connect() as conn:
        barcode = conn.execute(
            "SELECT * FROM barcodes WHERE code = %s FOR UPDATE", (code.strip(),)
        ).fetchone()
        if barcode is None:
            raise HTTPException(status_code=404, detail="留样条码不存在")
        if barcode["status"] != "active":
            raise HTTPException(
                status_code=409,
                detail="条码已使用或已作废，不可重复作废" if barcode["status"] == "voided"
                else "条码已使用，不可作废",
            )
        conn.execute("UPDATE barcodes SET status = 'voided' WHERE id = %s", (barcode["id"],))
        row = conn.execute(
            """INSERT INTO barcode_voids (barcode_id, code, herb, reason, voided_by, voided_at)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id, barcode_id, code, herb, reason, voided_by, voided_at""",
            (barcode["id"], barcode["code"], barcode["herb"], body.reason.strip(),
             user["username"], now),
        ).fetchone()
        conn.commit()
    return row


@app.get("/api/barcode-voids")
def list_voids(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, barcode_id, code, herb, reason, voided_by, voided_at
               FROM barcode_voids ORDER BY id DESC"""
        ).fetchall()
    return rows
