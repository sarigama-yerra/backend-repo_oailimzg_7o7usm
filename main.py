import os
from datetime import datetime, date
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import create_document, get_documents, db
from schemas import User as UserSchema, Challenge as ChallengeSchema, ScreenTimeLog as ScreenTimeLogSchema

# Safe ObjectId handling (server should start even if bson import fails)
try:
    from bson import ObjectId as BsonObjectId
    HAS_BSON = True
except Exception:
    BsonObjectId = None
    HAS_BSON = False


def to_oid(value: str):
    """Convert string to ObjectId if bson is available; else raise helpful error."""
    if not HAS_BSON:
        raise HTTPException(status_code=500, detail="ObjectId support not available. Ensure 'pymongo' is installed.")
    try:
        return BsonObjectId(str(value))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id format")


def oid_str(oid: Any) -> str:
    try:
        return str(oid)
    except Exception:
        return ""


app = FastAPI(title="Screen Demon API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "Screen Demon API ready"}


@app.get("/health")
def health():
    return {"ok": True}


# ========================= Users =========================
class CreateUserRequest(BaseModel):
    handle: str
    name: str
    email: Optional[str] = None


@app.post("/api/users")
def create_user(payload: CreateUserRequest):
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")
    existing = db["user"].find_one({"handle": payload.handle})
    if existing:
        return {"id": oid_str(existing["_id"]), "_id": oid_str(existing["_id"]), "handle": existing.get("handle"), "name": existing.get("name"), "email": existing.get("email")}
    user_id = create_document("user", UserSchema(**payload.model_dump()))
    return {"id": user_id, "_id": user_id, **payload.model_dump()}


@app.get("/api/users")
def list_users(handle: Optional[str] = None):
    query: Dict[str, Any] = {"handle": handle} if handle else {}
    docs = get_documents("user", query)
    for d in docs:
        d["id"] = oid_str(d.get("_id"))
        d["_id"] = oid_str(d.get("_id"))
    return docs


# ======================= Challenges ======================
class CreateChallengeRequest(BaseModel):
    title: str
    creator_id: str
    start_date: date
    end_date: date
    bet_type: str
    bet_details: Optional[str] = None


@app.post("/api/challenges")
def create_challenge(payload: CreateChallengeRequest):
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")
    creator = db["user"].find_one({"_id": to_oid(payload.creator_id)})
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    challenge = ChallengeSchema(
        title=payload.title,
        creator_id=str(creator["_id"]),
        start_date=payload.start_date,
        end_date=payload.end_date,
        bet_type=payload.bet_type,
        bet_details=payload.bet_details,
        participants=[str(creator["_id"])],
        status="active",
    )
    cid = create_document("challenge", challenge)
    return {"id": cid, "_id": cid, **challenge.model_dump()}


@app.get("/api/challenges")
def list_challenges():
    if not db:
        return []
    docs = list(db["challenge"].find().sort("created_at", -1).limit(50))
    for d in docs:
        d["id"] = oid_str(d.get("_id"))
        d["_id"] = oid_str(d.get("_id"))
    return docs


class JoinChallengeRequest(BaseModel):
    user_id: str


@app.post("/api/challenges/{challenge_id}/join")
def join_challenge(challenge_id: str, payload: JoinChallengeRequest):
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")
    ch = db["challenge"].find_one({"_id": to_oid(challenge_id)})
    if not ch:
        raise HTTPException(status_code=404, detail="Challenge not found")
    user = db["user"].find_one({"_id": to_oid(payload.user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    participants: List[str] = [str(p) for p in ch.get("participants", [])]
    if str(user["_id"]) in participants:
        return {"message": "Already joined", "participants": participants}

    participants.append(str(user["_id"]))
    db["challenge"].update_one({"_id": ch["_id"]}, {"$set": {"participants": participants, "updated_at": datetime.utcnow()}})
    return {"message": "Joined", "participants": participants}


@app.get("/api/challenges/{challenge_id}")
def get_challenge(challenge_id: str):
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")
    ch = db["challenge"].find_one({"_id": to_oid(challenge_id)})
    if not ch:
        raise HTTPException(status_code=404, detail="Challenge not found")
    ch["id"] = oid_str(ch["_id"]) 
    ch["_id"] = oid_str(ch["_id"]) 
    return ch


@app.get("/api/users/{user_id}/challenges")
def user_challenges(user_id: str):
    # Note: this matches participants stored as string ids
    docs = get_documents("challenge", {"participants": str(user_id)})
    for d in docs:
        d["id"] = oid_str(d["_id"]) 
        d["_id"] = oid_str(d["_id"]) 
    return docs


# ===================== Screen Time Logs ==================
class LogTimeRequest(BaseModel):
    user_id: str
    challenge_id: str
    date: date
    minutes: int = Field(ge=0, le=24*60)


@app.post("/api/logs")
def create_log(payload: LogTimeRequest):
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")
    ch = db["challenge"].find_one({"_id": to_oid(payload.challenge_id)})
    if not ch:
        raise HTTPException(status_code=404, detail="Challenge not found")
    if str(payload.user_id) not in [str(x) for x in ch.get("participants", [])]:
        raise HTTPException(status_code=403, detail="User not in challenge")

    log = ScreenTimeLogSchema(**payload.model_dump())
    lid = create_document("screentimelog", log)
    return {"id": lid, "_id": lid, **log.model_dump()}


# ===================== Summary / Leaderboard ==================

def build_leaderboard(challenge_id: str):
    ch = db["challenge"].find_one({"_id": to_oid(challenge_id)})
    if not ch:
        raise HTTPException(status_code=404, detail="Challenge not found")

    pipeline = [
        {"$match": {
            "challenge_id": challenge_id,
            "date": {"$gte": ch["start_date"], "$lte": ch["end_date"]}
        }},
        {"$group": {"_id": "$user_id", "total_minutes": {"$sum": "$minutes"}}},
        {"$sort": {"total_minutes": 1}}
    ]
    rows = list(db["screentimelog"].aggregate(pipeline))

    participant_ids = [str(pid) for pid in ch.get("participants", [])]
    totals: Dict[str, int] = {str(r["_id"]): int(r.get("total_minutes", 0)) for r in rows}
    for pid in participant_ids:
        totals.setdefault(pid, 0)

    users = list(db["user"].find({"_id": {"$in": [to_oid(pid) for pid in totals.keys()]}})) if HAS_BSON else []
    handle_map = {str(u["_id"]): u.get("handle") for u in users}

    standings = [
        {"user_id": uid, "handle": handle_map.get(uid, "unknown"), "total_minutes": totals[uid]}
        for uid in sorted(totals.keys(), key=lambda k: totals[k])
    ]

    return ch, standings


@app.get("/api/challenges/{challenge_id}/leaderboard")
def leaderboard(challenge_id: str):
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")
    ch, standings = build_leaderboard(challenge_id)
    return {
        "challenge_id": challenge_id,
        "title": ch.get("title"),
        "bet_type": ch.get("bet_type"),
        "bet_details": ch.get("bet_details"),
        "start_date": ch.get("start_date"),
        "end_date": ch.get("end_date"),
        "standings": standings,
    }


@app.get("/api/challenges/{challenge_id}/summary")
def summary(challenge_id: str):
    if not db:
        raise HTTPException(status_code=500, detail="Database not available")
    ch, standings = build_leaderboard(challenge_id)
    return {
        "challenge": {"_id": oid_str(ch.get("_id")), **{k: v for k, v in ch.items() if k != "_id"}},
        "standings": standings,
        "participants": ch.get("participants", []),
        "bet": {"type": ch.get("bet_type"), "details": ch.get("bet_details")},
    }


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = getattr(db, 'name', '✅ Connected')
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
