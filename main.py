import os
from sqlmodel import SQLModel, Session, create_engine, select, Field
from models import Schedule,PlanLog  # ← モデルを読み込み
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv
from sqlmodel import delete

# --- DB設定 ---
app = FastAPI()
load_dotenv()

sqlite_file_name = os.path.join(os.path.dirname(__file__), "schedule.db")
engine = create_engine(f"sqlite:///{sqlite_file_name}", echo=True)

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://morning-check-app.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- テーブル作成 ---
@app.on_event("startup")
def on_startup():
    print("✅ テーブル作成処理開始")
    SQLModel.metadata.create_all(engine)
    print("✅ テーブル作成完了")

# --- Schedule API ---
class ScheduleItem(BaseModel):
    user_id: int
    username: str
    date: str
    expected_login_time: Optional[str]
    login_time: Optional[str]
    is_holiday: bool
    work_code: Optional[str] = None

@app.post("/upload-schedule")
async def upload_schedule(items: List[ScheduleItem]):
    with Session(engine) as session:
        if not items:
            return {"message": "スケジュールが空です"}

        target_dates = set(item.date for item in items)
        for date in target_dates:
            session.exec(delete(Schedule).where(Schedule.date == date))

        for item in items:
            schedule = Schedule(
                user_id=item.user_id,
                username=item.username,
                date=item.date,
                expected_login_time=item.expected_login_time,
                login_time=item.login_time,
                is_holiday=item.is_holiday,
                work_code=item.work_code
            )
            session.add(schedule)

        session.commit()
    return {"message": f"{len(items)} 件のスケジュールを保存しました"}

@app.get("/schedules")
def get_schedules(date: Optional[str] = Query(None)):
    with Session(engine) as session:
        statement = select(Schedule)
        if date:
            statement = statement.where(Schedule.date == date)
        results = session.exec(statement).all()
        return results

# --- ログインチェック ---
@app.get("/login-check")
def login_check():
    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)
    today_str = now.strftime("%Y-%m-%d")

    with Session(engine) as session:
        statement = select(Schedule).where(Schedule.date == today_str, Schedule.is_holiday == False)
        records = session.exec(statement).all()

        failed_logins = []

        for item in records:
            if not item.expected_login_time:
                continue

            expected_dt = datetime.strptime(f"{item.date} {item.expected_login_time}", "%Y-%m-%d %H:%M").replace(tzinfo=JST)

            if item.work_code == "★07A":
                limit_dt = datetime.strptime(f"{item.date} 07:00", "%Y-%m-%d %H:%M").replace(tzinfo=JST)
                if expected_dt >= limit_dt:
                    failed_logins.append({
                        "username": item.username,
                        "date": item.date,
                        "reason": f"予定時刻が勤務指定（★07A）の基準より遅い: {item.expected_login_time}"
                    })
                    continue
            elif item.work_code == "★11A":
                limit_dt = datetime.strptime(f"{item.date} 11:00", "%Y-%m-%d %H:%M").replace(tzinfo=JST)
                if expected_dt >= limit_dt:
                    failed_logins.append({
                        "username": item.username,
                        "date": item.date,
                        "reason": f"予定時刻が勤務指定（★11A）の基準より遅い: {item.expected_login_time}"
                    })
                    continue

            if now >= expected_dt and not item.login_time:
                failed_logins.append({
                    "username": item.username,
                    "date": item.date,
                    "reason": f"未ログイン（予定時刻: {item.expected_login_time}）"
                })

        if failed_logins:
            notify_slack("\n".join(
                [f"{entry['username']}（{entry['date']}）: {entry['reason']}" for entry in failed_logins]
            ))

        return {"missed_logins": failed_logins}

# --- Slack通知 ---
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

def notify_slack(message: str):
    response = requests.post(SLACK_WEBHOOK_URL, json={"text": message})
    if response.status_code != 200:
        print("Slack通知失敗:", response.text)
    else:
        print("✅ Slack通知成功！")

# --- PlanLogモデル追加 ---
class PlanLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    date: str
    expected_login_time: str
    registered_at: Optional[str] = None

# --- PlanLog API（履歴保存）---
@app.post("/log-plan")
def log_plan_entry(log: PlanLog):
    with Session(engine) as session:
        log.registered_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        session.add(log)
        session.commit()
        session.refresh(log)
        return {"message": "出勤予定ログを保存しました", "log": log}

@app.get("/log-plan")
def get_plan_logs(user_id: Optional[int] = None, date: Optional[str] = None):
    with Session(engine) as session:
        query = select(PlanLog)
        if user_id:
            query = query.where(PlanLog.user_id == user_id)
        if date:
            query = query.where(PlanLog.date == date)
        results = session.exec(query).all()
        return results
