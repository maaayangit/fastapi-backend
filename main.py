import os
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel import select
from models import Schedule  # ← モデルを読み込み
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta, timezone  # ← これでOK
import requests
from dotenv import load_dotenv  # ← これを追加

app = FastAPI()
load_dotenv()  # ← これで .env を読み込み

# ✅★ ここに追加！
sqlite_file_name = os.path.join(os.path.dirname(__file__), "schedule.db")
engine = create_engine(f"sqlite:///{sqlite_file_name}", echo=True)

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://morning-check-app.vercel.app",  # ← React をホスティングしてる Vercel の URL
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# DBの起動時テーブル作成
@app.on_event("startup")
def on_startup():
    print("✅ テーブル作成処理開始")
    SQLModel.metadata.create_all(engine)
    print("✅ テーブル作成完了")

# API用の受け取りデータ形式
class ScheduleItem(BaseModel):
    user_id: int
    username: str
    date: str
    expected_login_time: Optional[str]
    login_time: Optional[str]
    is_holiday: bool
    work_code: Optional[str] = None  # ← これを追加！


# 保存処理付き POST API（このあと書き換え）
@app.post("/upload-schedule")
async def upload_schedule(items: List[ScheduleItem]):
    print("✅ 保存処理開始")
    with Session(engine) as session:
        for item in items:
            print("📌 追加中:", item)
            schedule = Schedule(
                user_id=item.user_id,
                username=item.username,
                date=item.date,
                expected_login_time=item.expected_login_time,  # ← 追加！
                login_time=item.login_time,
                is_holiday=item.is_holiday,
            ) 

            session.add(schedule)
        session.commit()
    print("✅ 保存完了！")

    return {"message": f"{len(items)} 件のスケジュールを保存しました"}

@app.get("/schedules")
def get_schedules():
    with Session(engine) as session:
        statement = select(Schedule)
        results = session.exec(statement).all()
        return results

from datetime import datetime, timezone, timedelta

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

            if now >= expected_dt and not item.login_time:
                failed_logins.append({
                    "username": item.username,
                    "date": item.date,
                    "reason": f"未ログイン（予定時刻: {item.expected_login_time}）"
                })

        if failed_logins:
            message_lines = ["🚨 ログイン遅れユーザー（予定時刻超過）"]
            for entry in failed_logins:
                message_lines.append(f"{entry['username']}（{entry['date']}）: {entry['reason']}")
            notify_slack("\n".join(message_lines))

        return {"missed_logins": failed_logins}




SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

def notify_slack(message: str):
    print("📣 Slackに通知中...")
    payload = {
        "text": message
    }
    response = requests.post(SLACK_WEBHOOK_URL, json=payload)
    print("📨 Slack通知ステータス:", response.status_code)
    if response.status_code != 200:
        print("Slack通知失敗:", response.text)
    else:
        print("✅ Slack通知成功！")
