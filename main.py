import os
from sqlmodel import SQLModel, Session, create_engine, select, Field, delete
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv
from models import Schedule, PlanLog

app = FastAPI()
load_dotenv()

sqlite_file_name = os.path.join(os.path.dirname(__file__), "schedule.db")
engine = create_engine(f"sqlite:///{sqlite_file_name}", echo=True)

JST = timezone(timedelta(hours=9))  # 日本時間タイムゾーン

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://morning-check-app.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# DB起動時にテーブル作成
@app.on_event("startup")
def on_startup():
    print("✅ テーブル作成処理開始")
    SQLModel.metadata.create_all(engine)
    print("✅ テーブル作成完了")

# スケジュール登録用モデル
class ScheduleItem(BaseModel):
    user_id: int
    username: str
    date: str
    expected_login_time: Optional[str]
    login_time: Optional[str]
    is_holiday: bool
    work_code: Optional[str] = None

# 勤務表のCSVアップロード
@app.post("/upload-schedule")
async def upload_schedule(items: List[ScheduleItem]):
    with Session(engine) as session:
        if not items:
            return {"message": "スケジュールが空です"}

        # ✅ user_id + date のペアで限定削除（既存の登録を温存）
        target_pairs = set((item.date, item.user_id) for item in items)
        for date, user_id in target_pairs:
            session.exec(delete(Schedule).where(Schedule.date == date, Schedule.user_id == user_id))

        # ⬇ 追加登録
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


# 勤務表一覧取得
@app.get("/schedules")
def get_schedules(date: Optional[str] = Query(None)):
    with Session(engine) as session:
        statement = select(Schedule)
        if date:
            statement = statement.where(Schedule.date == date)
        return session.exec(statement).all()

# ログインチェック
@app.get("/login-check")
def login_check():
    now = datetime.now(JST)
    today_str = now.strftime("%Y-%m-%d")
    with Session(engine) as session:
        records = session.exec(
            select(Schedule).where(Schedule.date == today_str, Schedule.is_holiday == False)
        ).all()

        failed_logins = []
        for item in records:
            if not item.expected_login_time:
                continue
            expected_dt = datetime.strptime(f"{item.date} {item.expected_login_time}", "%Y-%m-%d %H:%M").replace(tzinfo=JST)

            if item.work_code == "★07A" and expected_dt >= expected_dt.replace(hour=7, minute=0):
                failed_logins.append({
                    "user_id": item.user_id,
                    "username": item.username,
                    "date": item.date,
                    "reason": f"勤務指定（★07A）より遅い: {item.expected_login_time}"
                })
                continue
            elif item.work_code == "★11A" and expected_dt >= expected_dt.replace(hour=11, minute=0):
                failed_logins.append({
                    "user_id": item.user_id,
                    "username": item.username,
                    "date": item.date,
                    "reason": f"勤務指定（★11A）より遅い: {item.expected_login_time}"
                })
                continue

            if now >= expected_dt and not item.login_time:
                failed_logins.append({
                    "user_id": item.user_id,
                    "username": item.username,
                    "date": item.date,
                    "reason": f"未ログイン（予定時刻: {item.expected_login_time}）"
                })

        if failed_logins:
            notify_slack("\n".join(
                [f"{entry['user_id']}（{entry['date']}）: {entry['reason']}" for entry in failed_logins]
            ))

        return {"missed_logins": failed_logins}

# Slack通知
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
def notify_slack(message: str):
    response = requests.post(SLACK_WEBHOOK_URL, json={"text": message})
    if response.status_code != 200:
        print("Slack通知失敗:", response.text)
    else:
        print("✅ Slack通知成功！")

# 出勤予定時刻の更新（計画登録）
@app.post("/update-expected-login")
async def update_expected_login(request: Request):
    data = await request.json()
    user_id = data["user_id"]
    date = data["date"]
    expected_login_time = data["expected_login_time"]

    with Session(engine) as session:
        stmt = select(Schedule).where(Schedule.user_id == user_id, Schedule.date == date)
        result = session.exec(stmt).first()

        if result:
            result.expected_login_time = expected_login_time
            session.add(result)
        else:
            schedule = Schedule(
                user_id=user_id,
                username="（未設定）",
                date=date,
                expected_login_time=expected_login_time,
                is_holiday=False,
                login_time=None,
                work_code=None
            )
            session.add(schedule)
        session.commit()
    return {"message": "出勤予定を更新しました"}

# 出勤予定ログ登録・更新
@app.post("/log-plan")
def log_plan_entry(log: PlanLog):
    with Session(engine) as session:
        existing_log = session.exec(
            select(PlanLog).where(PlanLog.user_id == log.user_id, PlanLog.date == log.date)
        ).first()

        if existing_log:
            existing_log.expected_login_time = log.expected_login_time
            existing_log.registered_at = datetime.now(JST)
            session.add(existing_log)
            session.commit()
            return {"message": "既存の出勤予定ログを更新しました", "log": existing_log}
        else:
            log.registered_at = datetime.now(JST)
            session.add(log)
            session.commit()
            session.refresh(log)
            return {"message": "出勤予定ログを保存しました", "log": log}

# 出勤予定ログ取得
@app.get("/log-plan")
def get_plan_logs(user_id: Optional[int] = None, date: Optional[str] = None):
    with Session(engine) as session:
        query = select(PlanLog)
        if user_id:
            query = query.where(PlanLog.user_id == user_id)
        if date:
            query = query.where(PlanLog.date == date)
        return session.exec(query).all()

# 勤務指定の取得
@app.get("/work-code")
def get_work_code(user_id: int, date: str):
    with Session(engine) as session:
        result = session.exec(
            select(Schedule).where(Schedule.user_id == user_id, Schedule.date == date)
        ).first()
        if not result:
            return {"work_code": None}
        return {"work_code": result.work_code}
