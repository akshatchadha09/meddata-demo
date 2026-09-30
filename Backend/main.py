import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from io import StringIO
import math
import hashlib
import secrets
from urllib.parse import parse_qs
import statistics
import csv

from fastapi import FastAPI, Depends, HTTPException, Query, Header, Cookie, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "meddata.db"
FRONTEND = BASE_DIR / "frontend"

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

app = FastAPI(title="MedData", version="0.8.1")
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)


class PHC(Base):
    __tablename__ = "phcs"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    district = Column(String, nullable=False)
    state = Column(String, nullable=False)
    beds = Column(Integer, default=0)
    occupied_beds = Column(Integer, default=0)
    staff_total = Column(Integer, default=0)
    staff_present = Column(Integer, default=0)
    lat = Column(Float, default=28.6)
    lon = Column(Float, default=77.2)
    medicines = relationship("MedicineStock", back_populates="phc", cascade="all, delete-orphan")


class PatientRecord(Base):
    __tablename__ = "patient_records"
    id = Column(Integer, primary_key=True)
    phc_id = Column(Integer, ForeignKey("phcs.id"), nullable=False)
    condition = Column(String, nullable=False)
    age = Column(Integer, nullable=False)
    sex = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    phc = relationship("PHC")


class MedicineStock(Base):
    __tablename__ = "medicine_stock"
    id = Column(Integer, primary_key=True)
    phc_id = Column(Integer, ForeignKey("phcs.id"), nullable=False)
    medicine_name = Column(String, nullable=False)
    batch_no = Column(String, nullable=False)
    quantity = Column(Integer, default=0)
    reorder_level = Column(Integer, default=0)
    expiry_date = Column(String, nullable=False)
    last_updated = Column(DateTime, default=datetime.utcnow)
    phc = relationship("PHC", back_populates="medicines")


class SupplyEvent(Base):
    __tablename__ = "supply_events"
    id = Column(Integer, primary_key=True)
    medicine_name = Column(String, nullable=False)
    batch_no = Column(String, nullable=False)
    source = Column(String, nullable=False)
    destination = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String, default="Delivered")
    timestamp = Column(DateTime, default=datetime.utcnow)
    note = Column(Text, nullable=True)


class MedicineUsage(Base):
    __tablename__ = "medicine_usage"
    id = Column(Integer, primary_key=True)
    phc_id = Column(Integer, ForeignKey("phcs.id"), nullable=False)
    medicine_name = Column(String, nullable=False)
    usage_date = Column(String, nullable=False)
    quantity_used = Column(Integer, nullable=False)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    phc_id = Column(Integer, ForeignKey("phcs.id"), nullable=True)
    active = Column(Integer, default=1)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id = Column(Integer, primary_key=True)
    token_hash = Column(String, unique=True, nullable=False)
    email = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    active = Column(Integer, default=1)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    actor_email = Column(String, nullable=False)
    actor_role = Column(String, nullable=False)
    action = Column(String, nullable=False)
    target = Column(String, nullable=True)
    detail = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)


class VerificationEvent(Base):
    __tablename__ = "verification_events"
    id = Column(Integer, primary_key=True)
    batch_no = Column(String, nullable=False)
    verifier = Column(String, nullable=False)
    result = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    note = Column(Text, nullable=True)


class FederatedRound(Base):
    __tablename__ = "federated_rounds"
    id = Column(Integer, primary_key=True)
    round_no = Column(Integer, nullable=False)
    global_bias = Column(Float, nullable=False)
    global_slope = Column(Float, nullable=False)
    participating_nodes = Column(Integer, nullable=False)
    raw_data_shared = Column(Integer, default=0)
    timestamp = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


class PatientIn(BaseModel):
    phc_id: int
    condition: str = Field(min_length=2, max_length=100)
    age: int = Field(ge=0, le=120)
    sex: str = Field(default="O", min_length=1, max_length=1)


class MedicineIn(BaseModel):
    phc_id: int
    medicine_name: str
    batch_no: str
    quantity: int = Field(ge=0)
    reorder_level: int = Field(ge=0)
    expiry_date: str


class FacilityIn(BaseModel):
    beds: Optional[int] = Field(default=None, ge=0)
    occupied_beds: Optional[int] = Field(default=None, ge=0)
    staff_present: Optional[int] = Field(default=None, ge=0)


class TransferIn(BaseModel):
    medicine_name: str
    batch_no: str
    from_phc_id: int
    to_phc_id: int
    quantity: int = Field(gt=0)
    note: Optional[str] = Field(default="AI-recommended transfer", max_length=240)


class LoginIn(BaseModel):
    email: str
    password: str


class DispatchIn(BaseModel):
    medicine_name: str = Field(min_length=2, max_length=100)
    batch_no: str = Field(min_length=2, max_length=60)
    destination_phc_id: int
    quantity: int = Field(gt=0)
    expiry_date: str = Field(min_length=10, max_length=10)
    reorder_level: int = Field(default=0, ge=0)
    source: str = Field(default="Supplier Dispatch Hub", max_length=120)
    note: str = Field(default="Supplier dispatch — demo", max_length=240)


class VerifyIn(BaseModel):
    result: str = Field(default="VERIFIED", pattern="^(VERIFIED|REVIEW)$")
    note: Optional[str] = Field(default="Batch verification scan", max_length=240)


CONDITION_DRIVERS = {
    "Dengue": {"ORS": 2.1, "Paracetamol": 1.4},
    "Gastroenteritis": {"ORS": 2.5, "Paracetamol": 0.8},
    "Respiratory Infection": {"Paracetamol": 1.7},
    "Malaria": {"Paracetamol": 1.2, "ORS": 0.6},
    "Hypertension": {"Paracetamol": 0.2},
    "Diabetes": {"Paracetamol": 0.1},
}

# Approximate demo coordinates for transport-cost estimation. Not for operational routing.
DISTRICT_COORDS = {
    "Gurugram": (28.4595, 77.0266),
    "Faridabad": (28.4089, 77.3178),
    "Mathura": (27.4924, 77.6737),
    "Jaipur": (26.9124, 75.7873),
    "Delhi South": (28.5355, 77.2100),
    "Noida": (28.5355, 77.3910),
}


DEMO_USERS = {
    "gov@MedData.demo": {"name": "National Command Centre", "role": "GOVERNMENT", "password": "gov123", "phc_id": None},
    "district@MedData.demo": {"name": "District Health Officer", "role": "DISTRICT", "password": "district123", "phc_id": None},
    "phc@MedData.demo": {"name": "Shakti PHC Operator", "role": "PHC", "password": "phc123", "phc_id": 1},
    "supplier@MedData.demo": {"name": "Apex Pharma Supplier", "role": "SUPPLIER", "password": "supplier123", "phc_id": None},
}
SESSION_TOKENS = {}  # Backward-compatible in-memory token cache for the local demo
SESSION_COOKIE = "MedData_session"
SESSION_HOURS = 12


def new_session(db: Session, email: str) -> str:
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    expires = datetime.utcnow() + timedelta(hours=SESSION_HOURS)
    db.add(AuthSession(token_hash=token_hash, email=email, expires_at=expires, active=1))
    db.commit()
    SESSION_TOKENS[raw] = email
    return raw


def resolve_session_email(db: Session, token: str) -> Optional[str]:
    if not token:
        return None
    cached = SESSION_TOKENS.get(token)
    if cached:
        return cached
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session = db.query(AuthSession).filter(
        AuthSession.token_hash == token_hash,
        AuthSession.active == 1,
        AuthSession.expires_at > datetime.utcnow(),
    ).first()
    if session:
        SESSION_TOKENS[token] = session.email
        return session.email
    return None


def revoke_session(db: Session, token: Optional[str]):
    if not token:
        return
    SESSION_TOKENS.pop(token, None)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session = db.query(AuthSession).filter(AuthSession.token_hash == token_hash).first()
    if session:
        session.active = 0
        db.commit()


def hash_password(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def audit(db: Session, user: dict, action: str, target: str = "", detail: str = ""):
    db.add(AuditLog(actor_email=user["email"], actor_role=user["role"], action=action, target=target, detail=detail))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    MedData_session: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    # Prefer the HttpOnly browser cookie when present. This makes the direct
    # demo-login buttons resilient to stale Bearer tokens left by older builds.
    token = MedData_session
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
    email = resolve_session_email(db, token or "")
    if not email:
        raise HTTPException(401, "Authentication required")
    user = db.query(User).filter(User.email == email, User.active == 1).first()
    if not user:
        raise HTTPException(401, "User is not active")
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role, "phc_id": user.phc_id}


def require_roles(*roles):
    def dependency(user=Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, f"Role {user['role']} is not allowed for this action")
        return user
    return dependency


def seed_users(db: Session):
    for email, info in DEMO_USERS.items():
        existing = db.query(User).filter(User.email == email).first()
        if not existing:
            db.add(User(email=email, name=info["name"], role=info["role"], password_hash=hash_password(info["password"]), phc_id=info["phc_id"], active=1))
    db.commit()


def seed_federated_round(db: Session):
    if db.query(FederatedRound).count() == 0:
        db.add(FederatedRound(round_no=8, global_bias=0.082, global_slope=1.143, participating_nodes=5, raw_data_shared=0))
        db.commit()


def seed_data(db: Session):
    if db.query(PHC).count() > 0:
        return
    phcs = [
        PHC(name="Shakti PHC", district="Gurugram", state="Haryana", beds=30, occupied_beds=21, staff_total=18, staff_present=17, lat=28.4595, lon=77.0266),
        PHC(name="Jan Seva PHC", district="Faridabad", state="Haryana", beds=40, occupied_beds=35, staff_total=22, staff_present=18, lat=28.4089, lon=77.3178),
        PHC(name="Surya Primary Health Centre", district="Mathura", state="Uttar Pradesh", beds=35, occupied_beds=18, staff_total=20, staff_present=19, lat=27.4924, lon=77.6737),
        PHC(name="Arogya PHC", district="Jaipur", state="Rajasthan", beds=50, occupied_beds=47, staff_total=28, staff_present=24, lat=26.9124, lon=75.7873),
        PHC(name="Swasthya Kendra", district="Delhi South", state="Delhi", beds=45, occupied_beds=31, staff_total=26, staff_present=23, lat=28.5355, lon=77.2100),
        PHC(name="Sehat PHC", district="Noida", state="Uttar Pradesh", beds=25, occupied_beds=8, staff_total=14, staff_present=13, lat=28.5355, lon=77.3910),
    ]
    db.add_all(phcs)
    db.flush()

    conditions = ["Dengue", "Respiratory Infection", "Hypertension", "Diabetes", "Malaria", "Gastroenteritis"]
    base_counts = {"Dengue": 46, "Respiratory Infection": 58, "Hypertension": 71, "Diabetes": 64, "Malaria": 31, "Gastroenteritis": 25}
    for phc in phcs:
        for condition in conditions:
            c = max(3, base_counts[condition] // len(phcs))
            for i in range(c):
                db.add(PatientRecord(phc_id=phc.id, condition=condition,
                    age=18 + ((i * 7 + phc.id) % 55), sex="F" if i % 2 else "M",
                    created_at=datetime.utcnow() - timedelta(days=(i + phc.id) % 30)))

    meds = [
        ("Paracetamol", "PCM-A101", 120, 80, "2027-06-30"),
        ("ORS", "ORS-B204", 310, 150, "2027-09-30"),
        ("Doxycycline", "DOX-C221", 42, 60, "2027-02-28"),
        ("IV Fluids", "IVF-D311", 95, 50, "2027-01-15"),
    ]
    for phc in phcs:
        for name, batch, qty, reorder, expiry in meds:
            local_qty = qty
            if phc.id in (2, 4) and name == "Doxycycline":
                local_qty = 0
            if phc.id == 6 and name == "Doxycycline":
                local_qty = 180
            if phc.id == 4 and name == "Paracetamol":
                local_qty = 38
            db.add(MedicineStock(phc_id=phc.id, medicine_name=name, batch_no=batch + str(phc.id), quantity=local_qty, reorder_level=reorder, expiry_date=expiry))

    events = [
        SupplyEvent(medicine_name="Doxycycline", batch_no="DOX-C2212", source="State Warehouse Haryana", destination="Jan Seva PHC", quantity=120, note="Emergency allocation — demo"),
        SupplyEvent(medicine_name="Paracetamol", batch_no="PCM-A1014", source="Central Distribution Hub", destination="Arogya PHC", quantity=300, note="Routine replenishment — demo"),
        SupplyEvent(medicine_name="ORS", batch_no="ORS-B2041", source="Apex Pharma Plant", destination="State Warehouse Haryana", quantity=1200, note="Manufacturer dispatch — demo"),
        SupplyEvent(medicine_name="ORS", batch_no="ORS-B2041", source="State Warehouse Haryana", destination="Shakti PHC", quantity=300, note="State allocation — demo"),
        SupplyEvent(medicine_name="Doxycycline", batch_no="DOX-C2216", source="Apex Pharma Plant", destination="State Warehouse Uttar Pradesh", quantity=500, note="Manufacturer dispatch — demo"),
        SupplyEvent(medicine_name="Doxycycline", batch_no="DOX-C2216", source="State Warehouse Uttar Pradesh", destination="Sehat PHC", quantity=180, note="District allocation — demo"),
    ]
    db.add_all(events)
    db.commit()
    seed_usage_history(db)


def seed_usage_history(db: Session):
    if db.query(MedicineUsage).count() > 0:
        return
    phcs = db.query(PHC).all()
    names = [m[0] for m in db.query(MedicineStock.medicine_name).distinct().all()]
    today = datetime.utcnow().date()
    rows = []
    for phc in phcs:
        for med in names:
            for offset in range(30):
                day = today - timedelta(days=offset)
                base = {"Paracetamol": 4, "ORS": 5, "Doxycycline": 1, "IV Fluids": 2}.get(med, 2)
                wave = ((phc.id * 3 + offset) % 5) - 2
                recent_boost = 1 if offset < 7 and med in ("ORS", "Paracetamol") and phc.id in (2, 4) else 0
                amount = max(0, base + wave + recent_boost)
                rows.append(MedicineUsage(phc_id=phc.id, medicine_name=med, usage_date=str(day), quantity_used=amount))
    db.add_all(rows)
    db.commit()


with SessionLocal() as _db:
    seed_data(_db)
    seed_users(_db)
    seed_federated_round(_db)


def usage_series(db: Session, phc_id: int, medicine_name: str, days: int = 30):
    cutoff = str(datetime.utcnow().date() - timedelta(days=days - 1))
    rows = db.query(MedicineUsage.usage_date, MedicineUsage.quantity_used).filter(
        MedicineUsage.phc_id == phc_id,
        MedicineUsage.medicine_name == medicine_name,
        MedicineUsage.usage_date >= cutoff,
    ).order_by(MedicineUsage.usage_date).all()
    return [(d, q) for d, q in rows]


def model_forecast(values: list[int], horizon: int = 30):
    """Transparent baseline model: least-squares linear trend + recent average blend."""
    if not values:
        return 0.1, 0.0, 0.0
    n = len(values)
    recent = statistics.mean(values[-7:]) if n >= 7 else statistics.mean(values)
    overall = statistics.mean(values)
    xbar = (n + 1) / 2
    ybar = overall
    denom = sum((x - xbar) ** 2 for x in range(1, n + 1)) or 1
    slope = sum((x - xbar) * (y - ybar) for x, y in zip(range(1, n + 1), values)) / denom
    next_daily = max(0.1, 0.6 * recent + 0.4 * (ybar + slope * (n + 1 - xbar)))
    residuals = [values[i] - (ybar + slope * (i + 1 - xbar)) for i in range(n)]
    sigma = statistics.pstdev(residuals) if n > 1 else 0.0
    return round(next_daily, 2), round(slope, 3), round(sigma, 2)


def projected_daily_use(db: Session, phc_id: int, medicine_name: str, shock: float = 1.0):
    values = [q for _, q in usage_series(db, phc_id, medicine_name)]
    daily, slope, sigma = model_forecast(values)
    avg7 = statistics.mean(values[-7:]) if len(values) >= 7 else (statistics.mean(values) if values else 0)
    avg30 = statistics.mean(values) if values else avg7
    trend = ((avg7 - avg30) / avg30) if avg30 else 0
    daily = daily * shock
    return round(daily, 2), round(avg7, 2), round(trend * 100, 1), sigma, slope


def medicine_forecast_rows(db: Session, shock: float = 1.0):
    rows = db.query(MedicineStock, PHC).join(PHC, MedicineStock.phc_id == PHC.id).all()
    out = []
    for stock, phc in rows:
        daily, avg7, trend, sigma, slope = projected_daily_use(db, phc.id, stock.medicine_name, shock)
        days = stock.quantity / daily if daily > 0 else 999
        demand30 = daily * 30
        safety_stock = math.ceil(max(daily * 7, sigma * 2 * math.sqrt(30)))
        projected_gap = max(0, math.ceil(demand30 + safety_stock - stock.quantity))
        risk = "LOW"
        if days <= 7 or stock.quantity <= stock.reorder_level * 0.5:
            risk = "CRITICAL"
        elif days <= 14 or stock.quantity <= stock.reorder_level:
            risk = "HIGH"
        elif days <= 30:
            risk = "MEDIUM"
        out.append({
            "phc_id": phc.id, "phc": phc.name, "district": phc.district, "state": phc.state,
            "medicine": stock.medicine_name, "batch": stock.batch_no, "stock": stock.quantity,
            "daily_use": daily, "avg7": avg7, "trend_pct": trend, "trend_slope": slope,
            "volatility": sigma, "days_remaining": round(days, 1),
            "demand_30d": math.ceil(demand30), "safety_stock": safety_stock, "risk": risk,
            "procurement_gap": projected_gap,
        })
    return out


def haversine_km(a: PHC, b: PHC):
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = math.radians(b.lat - a.lat)
    dl = math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(h)), 1)


def build_recommendations(db: Session, shock: float = 1.0):
    rows = medicine_forecast_rows(db, shock=shock)
    groups = {}
    for row in rows:
        groups.setdefault(row["medicine"], []).append(row)
    recs = []
    phc_map = {p.id: p for p in db.query(PHC).all()}
    for medicine, items in groups.items():
        donor_items = []
        receiver_items = []
        for r in items:
            surplus = max(0, r["stock"] - r["demand_30d"] - r["safety_stock"])
            if surplus > 0:
                donor_items.append({**r, "surplus": surplus})
            if r["procurement_gap"] > 0:
                receiver_items.append(r)
        # Nearest viable donor first; transfer quantity preserves donor safety stock.
        for receiver in sorted(receiver_items, key=lambda x: (-({"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}[x["risk"]]), x["days_remaining"])):
            candidates = []
            receiver_phc = phc_map[receiver["phc_id"]]
            for donor in donor_items:
                if donor["surplus"] <= 0 or donor["phc_id"] == receiver["phc_id"]:
                    continue
                dist = haversine_km(phc_map[donor["phc_id"]], receiver_phc)
                candidates.append((dist, -donor["surplus"], donor))
            candidates.sort(key=lambda x: (x[0], x[1]))
            if candidates:
                dist, _, donor = candidates[0]
                qty = min(donor["surplus"], receiver["procurement_gap"])
                if qty > 0:
                    recs.append({
                        "medicine": medicine,
                        "batch": donor["batch"],
                        "from_phc_id": donor["phc_id"],
                        "to_phc_id": receiver["phc_id"],
                        "from_phc": donor["phc"],
                        "to_phc": receiver["phc"],
                        "quantity": int(qty),
                        "distance_km": dist,
                        "from_days": donor["days_remaining"],
                        "to_days": receiver["days_remaining"],
                        "reason": f"Transfer from projected surplus to cover {receiver['procurement_gap']} units of 30-day + safety-stock need.",
                    })
                    donor["surplus"] -= qty
    return recs[:20]


@app.post("/api/login")
def login(payload: LoginIn, response: Response, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    user = db.query(User).filter(User.email == email, User.active == 1).first()
    if not user or user.password_hash != hash_password(payload.password):
        raise HTTPException(401, "Invalid demo credentials")
    token = new_session(db, user.email)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=SESSION_HOURS * 3600,
        path="/",
    )
    u = {"email": user.email, "name": user.name, "role": user.role, "phc_id": user.phc_id}
    audit(db, {**u}, "LOGIN", user.email, "Demo session created")
    db.commit()
    return {"token": token, "user": u, "expires": f"{SESSION_HOURS} hours"}


@app.post("/web-login")
async def web_login(request: Request, db: Session = Depends(get_db)):
    body = (await request.body()).decode("utf-8")
    values = parse_qs(body)
    email = values.get("email", [""])[0].strip().lower()
    password = values.get("password", [""])[0]
    user = db.query(User).filter(User.email == email, User.active == 1).first()
    if not user or user.password_hash != hash_password(password):
        return RedirectResponse(url="/?login_error=Invalid%20demo%20credentials", status_code=303)
    token = new_session(db, user.email)
    audit(db, {"email": user.email, "name": user.name, "role": user.role, "phc_id": user.phc_id}, "LOGIN", user.email, "Web session created")
    db.commit()
    response = RedirectResponse(url="/app", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=SESSION_HOURS * 3600,
        path="/",
    )
    return response


@app.get("/demo-login/{role}")
def demo_login(role: str, db: Session = Depends(get_db)):
    role = role.strip().upper()
    info = next((v for v in DEMO_USERS.values() if v["role"] == role), None)
    if not info:
        return RedirectResponse(url="/?login_error=Unknown%20demo%20role", status_code=303)
    email = next(k for k, v in DEMO_USERS.items() if v["role"] == role)
    token = new_session(db, email)
    audit(db, {"email": email, "name": info["name"], "role": role, "phc_id": info["phc_id"]}, "DEMO_LOGIN", email, "One-click demo role session")
    db.commit()
    response = RedirectResponse(url="/app", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=SESSION_HOURS * 3600,
        path="/",
    )
    return response


@app.get("/api/me")
def me(user=Depends(get_current_user)):
    return user


@app.post("/api/logout")
def logout(response: Response, authorization: Optional[str] = Header(default=None), MedData_session: Optional[str] = Cookie(default=None), db: Session = Depends(get_db)):
    # Revoke both possible session sources so stale tokens from earlier builds
    # cannot immediately log the user back in after logout.
    bearer = authorization.split(" ", 1)[1].strip() if authorization and authorization.startswith("Bearer ") else None
    if bearer:
        revoke_session(db, bearer)
    if MedData_session and MedData_session != bearer:
        revoke_session(db, MedData_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"message": "Logged out"}


@app.get("/api/audit")
def audit_logs(limit: int = Query(25, ge=1, le=100), db: Session = Depends(get_db), user=Depends(require_roles("GOVERNMENT", "DISTRICT"))):
    rows = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit).all()
    return [{"actor": r.actor_email, "role": r.actor_role, "action": r.action, "target": r.target, "detail": r.detail, "timestamp": r.timestamp.isoformat()} for r in rows]


@app.get("/api/public/verify/{batch_no}")
def public_verify(batch_no: str, db: Session = Depends(get_db)):
    stock_rows = db.query(MedicineStock, PHC).join(PHC, MedicineStock.phc_id == PHC.id).filter(MedicineStock.batch_no == batch_no).all()
    events = db.query(SupplyEvent).filter(SupplyEvent.batch_no == batch_no).order_by(SupplyEvent.timestamp).all()
    if not stock_rows and not events:
        raise HTTPException(404, "Batch not found")
    movement = sum(e.quantity for e in events if e.status.lower() in {"delivered", "dispatched", "in transit"})
    stock = sum(s.quantity for s, _ in stock_rows)
    status = "VERIFIED" if events and stock <= movement else "REVIEW"
    db.add(VerificationEvent(batch_no=batch_no, verifier="PUBLIC_QR_SCAN", result=status, note="Public batch-verifier access"))
    db.commit()
    return {"batch": batch_no, "status": status, "medicine": stock_rows[0][0].medicine_name if stock_rows else (events[0].medicine_name if events else "Unknown"),
            "locations": [{"phc": p.name, "district": p.district, "quantity": s.quantity} for s, p in stock_rows],
            "event_count": len(events), "trace_url": f"/api/public/verify/{batch_no}"}


@app.get("/api/qr/{batch_no}")
def batch_qr(batch_no: str, db: Session = Depends(get_db)):
    from io import BytesIO
    try:
        import qrcode
    except ImportError:
        raise HTTPException(500, "QR dependency not installed")
    exists = db.query(MedicineStock).filter(MedicineStock.batch_no == batch_no).first() or db.query(SupplyEvent).filter(SupplyEvent.batch_no == batch_no).first()
    if not exists:
        raise HTTPException(404, "Batch not found")
    img = qrcode.make(f"http://127.0.0.1:8000/verify.html?batch={batch_no}")
    buf = BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    from fastapi.responses import StreamingResponse
    return StreamingResponse(buf, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/trace/{batch_no}/verify")
def verify_batch(batch_no: str, payload: VerifyIn, db: Session = Depends(get_db), user=Depends(require_roles("GOVERNMENT", "DISTRICT", "PHC", "SUPPLIER"))):
    result = public_verify(batch_no, db)
    db.add(VerificationEvent(batch_no=batch_no, verifier=user["email"], result=payload.result, note=payload.note))
    audit(db, user, "BATCH_VERIFY", batch_no, payload.result)
    db.commit()
    return {"message": "Verification event recorded", "batch": batch_no, "result": payload.result, "trace": result}


@app.post("/api/supply/dispatch")
def supplier_dispatch(payload: DispatchIn, db: Session = Depends(get_db), user=Depends(require_roles("SUPPLIER", "GOVERNMENT"))):
    phc = db.get(PHC, payload.destination_phc_id)
    if not phc:
        raise HTTPException(404, "Destination PHC not found")
    stock = db.query(MedicineStock).filter_by(phc_id=phc.id, medicine_name=payload.medicine_name, batch_no=payload.batch_no).first()
    if stock:
        stock.quantity += payload.quantity
        stock.expiry_date = payload.expiry_date
        stock.reorder_level = payload.reorder_level
        stock.last_updated = datetime.utcnow()
    else:
        stock = MedicineStock(phc_id=phc.id, medicine_name=payload.medicine_name, batch_no=payload.batch_no, quantity=payload.quantity, reorder_level=payload.reorder_level, expiry_date=payload.expiry_date)
        db.add(stock)
    db.add(SupplyEvent(medicine_name=payload.medicine_name, batch_no=payload.batch_no, source=payload.source, destination=phc.name, quantity=payload.quantity, status="Dispatched", note=payload.note))
    audit(db, user, "SUPPLIER_DISPATCH", payload.batch_no, f"{payload.quantity} units → {phc.name}")
    db.commit()
    return {"message": "Supplier dispatch recorded", "destination": phc.name, "quantity": payload.quantity}


@app.get("/api/verification-events")
def verification_events(batch_no: Optional[str] = None, limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), user=Depends(get_current_user)):
    q = db.query(VerificationEvent).order_by(VerificationEvent.timestamp.desc())
    if batch_no:
        q = q.filter(VerificationEvent.batch_no == batch_no)
    rows = q.limit(limit).all()
    return [{"batch": r.batch_no, "verifier": r.verifier, "result": r.result, "timestamp": r.timestamp.isoformat(), "note": r.note} for r in rows]


@app.get("/api/federated/round")
def federated_round(db: Session = Depends(get_db), user=Depends(get_current_user)):
    r = db.query(FederatedRound).order_by(FederatedRound.round_no.desc()).first()
    return {"round": r.round_no, "global_bias": r.global_bias, "global_slope": r.global_slope, "participating_nodes": r.participating_nodes, "raw_data_shared": bool(r.raw_data_shared), "timestamp": r.timestamp.isoformat()}


@app.post("/api/federated/run")
def run_federated_round(db: Session = Depends(get_db), user=Depends(require_roles("GOVERNMENT", "DISTRICT"))):
    previous = db.query(FederatedRound).order_by(FederatedRound.round_no.desc()).first()
    round_no = (previous.round_no + 1) if previous else 1
    nodes = [
        ("India", 125000, 0.072, 1.19), ("Brazil", 54000, 0.061, 1.12), ("Russia", 47000, 0.069, 1.08),
        ("China", 180000, 0.079, 1.21), ("South Africa", 32000, 0.055, 1.05)
    ]
    total = sum(x[1] for x in nodes)
    bias = sum(samples*bias for _, samples, bias, _ in nodes)/total
    slope = sum(samples*slope for _, samples, _, slope in nodes)/total
    global_bias = round(bias, 4); global_slope = round(slope, 4)
    db.add(FederatedRound(round_no=round_no, global_bias=global_bias, global_slope=global_slope, participating_nodes=len(nodes), raw_data_shared=0))
    audit(db, user, "FEDERATED_ROUND", str(round_no), "Weighted parameter aggregation demo; raw records remained local")
    db.commit()
    return {"round": round_no, "global_bias": global_bias, "global_slope": global_slope, "participating_nodes": len(nodes), "raw_data_shared": False, "method": "weighted parameter averaging demo"}


@app.get("/api/governance")
def governance(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return {
        "privacy": {"national_views": "aggregated", "raw_patient_data_shared_with_suppliers": False, "federated_raw_data_exchange": False},
        "audit_events": db.query(AuditLog).count(),
        "verification_scans": db.query(VerificationEvent).count(),
        "active_demo_roles": ["GOVERNMENT", "DISTRICT", "PHC", "SUPPLIER"],
        "disclaimer": "Hackathon prototype using synthetic data; production deployment requires clinical, privacy, cybersecurity and regulatory validation."
    }


@app.get("/verify.html")
def verify_page():
    return FileResponse(FRONTEND / "verify.html")


@app.get("/")
def root(MedData_session: Optional[str] = Cookie(default=None), db: Session = Depends(get_db)):
    # If a valid session exists, take the user straight into the authenticated app.
    email = resolve_session_email(db, MedData_session or "")
    if email:
        return RedirectResponse(url="/app", status_code=303)
    return FileResponse(FRONTEND / "login.html")


@app.get("/app")
def app_page(user=Depends(get_current_user)):
    return FileResponse(FRONTEND / "app.html")


@app.get("/api/search")
def search(q: str = Query(..., min_length=2, max_length=80), db: Session = Depends(get_db), user=Depends(get_current_user)):
    term = q.strip().lower()
    results = []
    for p in db.query(PHC).all():
        hay = f"{p.name} {p.district} {p.state}".lower()
        if term in hay:
            results.append({"type":"PHC","label":p.name,"meta":f"{p.district}, {p.state}","key":str(p.id)})
    for m in db.query(MedicineStock).all():
        hay = f"{m.medicine_name} {m.batch_no}".lower()
        if term in hay:
            phc = db.get(PHC, m.phc_id)
            results.append({"type":"MEDICINE" if term in m.medicine_name.lower() else "BATCH","label":m.medicine_name if term in m.medicine_name.lower() else m.batch_no,
                            "meta":f"{m.batch_no} · {phc.name if phc else 'Unknown PHC'} · {m.quantity} units", "key":m.batch_no})
    # De-duplicate and keep search intentionally compact for the command bar.
    unique=[]; seen=set()
    for r in results:
        k=(r["type"],r["key"])
        if k not in seen:
            seen.add(k); unique.append(r)
    return unique[:12]


def _csv_response(rows, headers, filename):
    from fastapi.responses import StreamingResponse
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/export/medicines.csv")
def export_medicines(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = db.query(MedicineStock, PHC).join(PHC, MedicineStock.phc_id == PHC.id).all()
    data=[[p.name,p.district,s.medicine_name,s.batch_no,s.quantity,s.reorder_level,s.expiry_date,s.last_updated.isoformat()] for s,p in rows]
    return _csv_response(data,["PHC","District","Medicine","Batch","Quantity","Reorder Level","Expiry","Last Updated"],"MedData_medicines.csv")


@app.get("/api/export/conditions.csv")
def export_conditions(db: Session = Depends(get_db), user=Depends(require_roles("GOVERNMENT","DISTRICT"))):
    rows=db.query(PatientRecord.condition, func.count(PatientRecord.id)).group_by(PatientRecord.condition).order_by(func.count(PatientRecord.id).desc()).all()
    return _csv_response([[c,n] for c,n in rows],["Condition","Aggregated Records"],"MedData_conditions.csv")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "MedData", "version": "0.5.0"}


@app.get("/api/phcs")
def list_phcs(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = db.query(PHC).all()
    return [{"id": p.id, "name": p.name, "district": p.district, "state": p.state,
             "beds": p.beds, "occupied_beds": p.occupied_beds, "staff_total": p.staff_total,
             "staff_present": p.staff_present,
             "bed_utilisation": round((p.occupied_beds / p.beds * 100), 1) if p.beds else 0,
             "staff_attendance": round((p.staff_present / p.staff_total * 100), 1) if p.staff_total else 0}
            for p in rows]


@app.post("/api/phcs/{phc_id}/facility")
def update_facility(phc_id: int, payload: FacilityIn, db: Session = Depends(get_db), user=Depends(require_roles("GOVERNMENT", "DISTRICT", "PHC"))):
    p = db.get(PHC, phc_id)
    if not p:
        raise HTTPException(404, "PHC not found")
    if user["role"] == "PHC" and user.get("phc_id") != phc_id:
        raise HTTPException(403, "PHC operators can update only their assigned centre")
    for field in ("beds", "occupied_beds", "staff_present"):
        value = getattr(payload, field)
        if value is not None:
            setattr(p, field, value)
    if p.occupied_beds > p.beds:
        raise HTTPException(400, "Occupied beds cannot exceed total beds")
    if p.staff_present > p.staff_total:
        raise HTTPException(400, "Present staff cannot exceed total staff")
    audit(db, user, "FACILITY_UPDATE", str(phc_id), f"beds={p.beds}, occupied={p.occupied_beds}, staff_present={p.staff_present}")
    db.commit()
    return {"message": "Facility data updated"}


@app.post("/api/patients")
def add_patient(payload: PatientIn, db: Session = Depends(get_db), user=Depends(require_roles("GOVERNMENT", "DISTRICT", "PHC"))):
    if not db.get(PHC, payload.phc_id):
        raise HTTPException(404, "PHC not found")
    if user["role"] == "PHC" and user.get("phc_id") != payload.phc_id:
        raise HTTPException(403, "PHC operators can create records only for their assigned centre")
    record = PatientRecord(**payload.model_dump())
    db.add(record)
    audit(db, user, "PATIENT_RECORD", str(payload.phc_id), f"condition={payload.condition}")
    db.commit()
    db.refresh(record)
    return {"message": "Patient record added", "id": record.id}


@app.get("/api/conditions")
def condition_summary(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = db.query(PatientRecord.condition, func.count(PatientRecord.id)).group_by(PatientRecord.condition).order_by(func.count(PatientRecord.id).desc()).all()
    return [{"condition": c, "cases": n} for c, n in rows]


@app.get("/api/districts")
def district_summary(db: Session = Depends(get_db), user=Depends(get_current_user)):
    phcs = db.query(PHC).all()
    out = []
    for district in sorted({p.district for p in phcs}):
        ps = [p for p in phcs if p.district == district]
        total_beds = sum(p.beds for p in ps)
        occupied = sum(p.occupied_beds for p in ps)
        patients = db.query(func.count(PatientRecord.id)).join(PHC).filter(PHC.district == district).scalar() or 0
        critical = sum(1 for r in medicine_forecast_rows(db) if r["district"] == district and r["risk"] == "CRITICAL")
        out.append({"district": district, "state": ps[0].state, "phcs": len(ps), "beds": total_beds, "occupied": occupied,
                    "bed_utilisation": round(occupied / total_beds * 100, 1) if total_beds else 0,
                    "patient_records": patients, "critical_items": critical})
    return sorted(out, key=lambda x: x["critical_items"], reverse=True)


@app.get("/api/medicines")
def medicines(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = db.query(MedicineStock, PHC).join(PHC, MedicineStock.phc_id == PHC.id).all()
    result = []
    today = datetime.utcnow().date()
    for s, p in rows:
        status = "OK"
        if s.quantity <= s.reorder_level:
            status = "REORDER"
        if s.quantity == 0:
            status = "STOCK-OUT"
        days_to_expiry = (datetime.strptime(s.expiry_date, "%Y-%m-%d").date() - today).days
        if days_to_expiry <= 90 and days_to_expiry >= 0:
            status = "EXPIRING" if status == "OK" else status
        result.append({"id": s.id, "phc_id": p.id, "phc": p.name, "district": p.district, "medicine": s.medicine_name,
                       "batch": s.batch_no, "quantity": s.quantity, "reorder_level": s.reorder_level,
                       "expiry": s.expiry_date, "days_to_expiry": days_to_expiry, "status": status, "updated": s.last_updated.isoformat()})
    return result


@app.post("/api/medicines")
def upsert_medicine(payload: MedicineIn, db: Session = Depends(get_db), user=Depends(require_roles("GOVERNMENT", "DISTRICT", "PHC"))):
    if not db.get(PHC, payload.phc_id):
        raise HTTPException(404, "PHC not found")
    existing = db.query(MedicineStock).filter_by(phc_id=payload.phc_id, medicine_name=payload.medicine_name, batch_no=payload.batch_no).first()
    if existing:
        for k, v in payload.model_dump().items():
            setattr(existing, k, v)
        existing.last_updated = datetime.utcnow()
    else:
        existing = MedicineStock(**payload.model_dump())
        db.add(existing)
    audit(db, user, "INVENTORY_UPDATE", payload.batch_no, f"{payload.medicine_name} qty={payload.quantity}")
    db.commit()
    return {"message": "Medicine inventory updated"}


@app.get("/api/supply-events")
def supply_events(db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = db.query(SupplyEvent).order_by(SupplyEvent.timestamp.desc()).limit(40).all()
    return [{"id": r.id, "medicine": r.medicine_name, "batch": r.batch_no, "source": r.source, "destination": r.destination,
             "quantity": r.quantity, "status": r.status, "timestamp": r.timestamp.isoformat(), "note": r.note} for r in rows]


@app.get("/api/trace/{batch_no}")
def trace_batch(batch_no: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    stock_rows = db.query(MedicineStock, PHC).join(PHC, MedicineStock.phc_id == PHC.id).filter(MedicineStock.batch_no == batch_no).all()
    events = db.query(SupplyEvent).filter(SupplyEvent.batch_no == batch_no).order_by(SupplyEvent.timestamp).all()
    if not stock_rows and not events:
        raise HTTPException(404, "Batch not found in the demo network")
    movement_units = sum(e.quantity for e in events if e.status.lower() in {"delivered", "in transit", "dispatched"})
    stock_units = sum(s.quantity for s, _ in stock_rows)
    issues = []
    if events and movement_units and stock_units > movement_units:
        issues.append("Recorded stock exceeds traced movement for this batch; investigate reconciliation.")
    if not events:
        issues.append("No chain-of-custody events are recorded for this batch.")
    return {"batch": batch_no, "verified": len(issues) == 0, "issues": issues,
            "stock_locations": [{"phc": p.name, "district": p.district, "quantity": s.quantity, "expiry": s.expiry_date} for s, p in stock_rows],
            "events": [{"source": e.source, "destination": e.destination, "quantity": e.quantity, "status": e.status, "timestamp": e.timestamp.isoformat(), "note": e.note} for e in events]}


@app.post("/api/transfers")
def execute_transfer(payload: TransferIn, db: Session = Depends(get_db), user=Depends(require_roles("GOVERNMENT", "DISTRICT"))):
    from_phc = db.get(PHC, payload.from_phc_id)
    to_phc = db.get(PHC, payload.to_phc_id)
    if not from_phc or not to_phc:
        raise HTTPException(404, "Source or destination PHC not found")
    stock = db.query(MedicineStock).filter_by(phc_id=payload.from_phc_id, medicine_name=payload.medicine_name, batch_no=payload.batch_no).first()
    if not stock:
        raise HTTPException(404, "Source batch not found")
    if stock.quantity < payload.quantity:
        raise HTTPException(400, "Transfer quantity exceeds source stock")
    destination_stock = db.query(MedicineStock).filter_by(phc_id=payload.to_phc_id, medicine_name=payload.medicine_name, batch_no=payload.batch_no).first()
    if destination_stock:
        destination_stock.quantity += payload.quantity
        destination_stock.last_updated = datetime.utcnow()
    else:
        destination_stock = MedicineStock(phc_id=payload.to_phc_id, medicine_name=payload.medicine_name, batch_no=payload.batch_no,
                                          quantity=payload.quantity, reorder_level=stock.reorder_level, expiry_date=stock.expiry_date)
        db.add(destination_stock)
    stock.quantity -= payload.quantity
    stock.last_updated = datetime.utcnow()
    db.add(SupplyEvent(medicine_name=payload.medicine_name, batch_no=payload.batch_no, source=from_phc.name, destination=to_phc.name,
                       quantity=payload.quantity, status="Delivered", note=payload.note))
    audit(db, user, "TRANSFER", payload.batch_no, f"{payload.quantity} units {from_phc.name} → {to_phc.name}")
    db.commit()
    return {"message": "Transfer recorded and inventory reconciled", "from": from_phc.name, "to": to_phc.name, "quantity": payload.quantity}


@app.get("/api/alerts")
def alerts(db: Session = Depends(get_db), shock: float = Query(1.0, ge=1.0, le=4.0), user=Depends(get_current_user)):
    result = []
    forecasts = medicine_forecast_rows(db, shock=shock)
    for row in forecasts:
        if row["risk"] == "CRITICAL":
            result.append({"severity": "CRITICAL", "phc": row["phc"], "district": row["district"], "medicine": row["medicine"],
                           "message": f"{row['medicine']} projected to run out in ~{row['days_remaining']} days at {row['phc']}.", "days_remaining": row["days_remaining"]})
        elif row["risk"] == "HIGH":
            result.append({"severity": "HIGH", "phc": row["phc"], "district": row["district"], "medicine": row["medicine"],
                           "message": f"{row['medicine']} may run out within 14 days at {row['phc']}.", "days_remaining": row["days_remaining"]})
    for p in db.query(PHC).all():
        util = p.occupied_beds / p.beds if p.beds else 0
        if util >= 0.9:
            result.append({"severity": "HIGH", "phc": p.name, "district": p.district, "medicine": None,
                           "message": f"Bed utilisation is {util*100:.0f}%; surge capacity should be reviewed.", "days_remaining": None})
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
    result.sort(key=lambda x: order[x["severity"]])
    return result[:40]


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), user=Depends(get_current_user)):
    phcs = db.query(PHC).all()
    total_beds = sum(p.beds for p in phcs)
    occupied = sum(p.occupied_beds for p in phcs)
    total_stock = db.query(func.sum(MedicineStock.quantity)).scalar() or 0
    forecasts = medicine_forecast_rows(db)
    at_risk = sum(1 for r in forecasts if r["risk"] in {"CRITICAL", "HIGH"})
    procurement_gap = sum(r["procurement_gap"] for r in forecasts)
    total_patients = db.query(func.count(PatientRecord.id)).scalar() or 0
    high_bed = sum(1 for p in phcs if p.beds and p.occupied_beds / p.beds >= 0.9)
    return {"phcs": len(phcs), "total_beds": total_beds, "occupied_beds": occupied,
            "bed_utilisation": round(occupied / total_beds * 100, 1) if total_beds else 0,
            "medicine_units": total_stock, "medicine_items_at_risk": at_risk,
            "patient_records": total_patients, "procurement_gap": procurement_gap, "high_bed_centres": high_bed}


@app.get("/api/forecasts")
def forecasts(medicine: Optional[str] = None, risk: Optional[str] = None, db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = medicine_forecast_rows(db)
    if medicine:
        rows = [r for r in rows if r["medicine"].lower() == medicine.lower()]
    if risk:
        rows = [r for r in rows if r["risk"].lower() == risk.lower()]
    return sorted(rows, key=lambda r: (r["days_remaining"], r["district"], r["medicine"]))


@app.get("/api/procurement")
def procurement_plan(shock: float = Query(1.0, ge=1.0, le=4.0), db: Session = Depends(get_db), user=Depends(get_current_user)):
    rows = medicine_forecast_rows(db, shock=shock)
    grouped = {}
    for r in rows:
        g = grouped.setdefault(r["medicine"], {"medicine": r["medicine"], "network_stock": 0, "demand_30d": 0, "safety_stock": 0, "gap": 0, "centres_at_risk": 0})
        g["network_stock"] += r["stock"]
        g["demand_30d"] += r["demand_30d"]
        g["safety_stock"] += r["safety_stock"]
        g["gap"] += r["procurement_gap"]
        if r["risk"] in {"CRITICAL", "HIGH"}:
            g["centres_at_risk"] += 1
    return sorted(grouped.values(), key=lambda x: (-x["gap"], x["medicine"]))


@app.get("/api/recommendations")
def recommendations(db: Session = Depends(get_db), shock: float = Query(1.0, ge=1.0, le=4.0), user=Depends(get_current_user)):
    return build_recommendations(db, shock=shock)


@app.get("/api/emergency/simulate")
def emergency_simulate(condition: str = "Dengue", multiplier: float = Query(1.8, ge=1.1, le=4.0), db: Session = Depends(get_db), user=Depends(get_current_user)):
    phcs = db.query(PHC).all()
    counts = db.query(PatientRecord.phc_id, func.count(PatientRecord.id)).filter(PatientRecord.condition == condition).group_by(PatientRecord.phc_id).all()
    current_by_phc = {phc_id: count for phc_id, count in counts}
    impact = []
    for p in phcs:
        current = current_by_phc.get(p.id, 0)
        projected = math.ceil(current * multiplier)
        incremental = max(0, projected - current)
        bed_need = math.ceil(incremental * 0.10)
        free_beds = max(0, p.beds - p.occupied_beds)
        impact.append({"phc_id": p.id, "phc": p.name, "district": p.district, "current_cases": current, "projected_cases": projected,
                       "incremental_cases": incremental, "estimated_bed_need": bed_need, "free_beds": free_beds,
                       "capacity_risk": "HIGH" if bed_need > free_beds else ("MEDIUM" if bed_need > max(1, free_beds * 0.5) else "LOW")})
    drivers = CONDITION_DRIVERS.get(condition, {"Paracetamol": 1.2, "ORS": 0.8})
    incremental_cases = sum(x["incremental_cases"] for x in impact)
    med_shock = {}
    for med, driver in drivers.items():
        total_stock = sum(r["stock"] for r in medicine_forecast_rows(db) if r["medicine"] == med)
        extra = math.ceil(incremental_cases * driver)
        med_shock[med] = {"current_network_stock": total_stock, "estimated_extra_demand": extra,
                          "stress_ratio": round(extra / total_stock, 2) if total_stock else 99}
    surge_recs = build_recommendations(db, shock=multiplier)
    return {"condition": condition, "multiplier": multiplier,
            "warning": "Simulation only — use epidemiological/clinical validation before operational deployment.",
            "phc_impact": sorted(impact, key=lambda x: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}[x["capacity_risk"]], -x["incremental_cases"])),
            "medicine_impact": med_shock, "surge_redistribution": surge_recs[:8]}


@app.get("/api/federated/nodes")
def federated_nodes(user=Depends(get_current_user)):
    nations = [("India", 125000, 0.91), ("Brazil", 54000, 0.88), ("Russia", 47000, 0.86), ("China", 180000, 0.93), ("South Africa", 32000, 0.82)]
    nodes = []
    for name, samples, quality in nations:
        digest = hashlib.sha256(f"{name}:{samples}:round8".encode()).hexdigest()[:12]
        nodes.append({"node": name, "local_samples": samples, "local_model_quality": quality, "update_id": digest,
                      "raw_patient_data_shared": False, "status": "READY"})
    return {"federation": "BRICS Health Intelligence Demo", "round": 8,
            "aggregation": "Secure parameter/update exchange — simulated", "nodes": nodes}


# ---------------- MedData Assistant ----------------
class ChatRequest(BaseModel):
    message: str

def _chat_context_text():
    """Return a small, non-sensitive snapshot from the existing demo state."""
    parts = []
    # Reuse existing dashboard/data helpers if they are available.
    try:
        if 'get_dashboard' in globals():
            d = get_dashboard()
            if isinstance(d, dict):
                parts.append("Dashboard snapshot: " + json.dumps(d, default=str)[:3500])
    except Exception:
        pass
    return "\n".join(parts)

def _MedData_chat_reply(message: str, role: str) -> str:
    q = (message or "").strip().lower()
    if not q:
        return "Please enter a question."

    role_name = {
        "GOVERNMENT":"Government",
        "DISTRICT":"District",
        "MEDIA":"Media",
        "SUPPLIER":"Supplier",
        "CENTRE":"Healthcare Centre"
    }.get(str(role).upper(), str(role))

    if any(x in q for x in ["diagnose", "diagnosis", "prescribe", "medicine for me", "treatment"]):
        return ("MedData is an operational decision-support assistant, not a clinical "
                "diagnosis or treatment system. Please consult a qualified healthcare professional "
                "for individual medical advice.")

    if "what can i do" in q or "what can you do" in q or "help" == q:
        return (f"You are signed in as {role_name}. I can explain the dashboard, help interpret "
                "inventory and alerts, explain forecasts and redistribution recommendations, "
                "and guide you through the features available to your role. I will not expose "
                "data outside your authorised access.")

    if "dashboard" in q or "overview" in q:
        return (f"The MedData dashboard provides a role-specific view of healthcare operations. "
                f"As a {role_name} user, you can only access information permitted for that role. "
                "Use the sidebar to move between operations, AI planning, supply chain, emergency "
                "readiness and governance sections.")

    if any(x in q for x in ["shortage", "stock out", "stockout", "inventory", "medicine"]):
        return ("Medicine shortage risk is assessed using current inventory, utilisation and "
                "projected demand. Look for warning/critical alerts and the AI Planning section "
                "for projected demand and recommended redistribution. Recommendations are decision "
                "support and should be reviewed by an authorised human before execution.")

    if any(x in q for x in ["forecast", "demand", "prediction", "predict"]):
        return ("The demand-forecasting module estimates future medicine requirements from "
                "historical utilisation and operational patterns in the demonstration dataset. "
                "A forecast is a projection, not a guarantee; review the forecast horizon and "
                "uncertainty before making procurement decisions.")

    if any(x in q for x in ["redistribute", "transfer", "surplus", "shortage centre"]):
        return ("MedData can compare projected demand and available stock across centres and "
                "recommend transfers from surplus locations to locations approaching a shortage. "
                "A recommendation should be human-reviewed before the inventory is changed.")

    if any(x in q for x in ["supply chain", "batch", "traceability", "counterfeit", "adulteration"]):
        return ("The supply-chain module records medicine batch events such as dispatch, receipt, "
                "transfer and dispensing. This creates an auditable chain of custody and can help "
                "surface unexplained movements or inventory discrepancies; it does not by itself "
                "prove that a medicine is counterfeit or adulterated.")

    if any(x in q for x in ["emergency", "pandemic", "outbreak", "readiness"]):
        return ("Emergency Readiness lets authorised users model demand shocks and inspect their "
                "potential effect on medicine demand, stock-out risk and resource pressure. "
                "These are scenario estimates for planning, not predictions of a real outbreak.")

    if "federated" in q or "brics" in q:
        return ("The BRICS federated-learning demonstration represents a privacy-preserving model "
                "in which participating countries can contribute model updates without sending "
                "raw patient records to a central system.")

    if any(x in q for x in ["privacy", "patient data", "personal data", "security"]):
        return ("MedData is designed around role-based access. Patient-identifying information "
                "should not be exposed to media or suppliers, and the healthcare-centre source "
                "records are editable only by authorised centre operators. The assistant follows "
                "the permissions of the authenticated user.")

    return (f"I understand the question, but I do not have enough grounded information to answer "
            f"that reliably for your {role_name} role. Try asking about inventory, shortages, "
            "forecasting, redistribution, supply-chain traceability, emergency readiness, "
            "the dashboard, privacy, or federated AI.")

@app.post("/api/ai/chat")
async def ai_chat(req: ChatRequest, user=Depends(get_current_user)):
    role = user["role"]
    return {"reply": _MedData_chat_reply(req.message, role), "role": role}
