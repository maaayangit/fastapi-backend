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
from fastapi import Query

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
from sqlmodel import delete  # ← これを追加するのを忘れずに！

@app.post("/upload-schedule")
async def upload_schedule(items: List[ScheduleItem]):
    print("✅ 保存処理開始")
    with Session(engine) as session:
        if not items:
            return {"message": "スケジュールが空です"}

        # 📌 対象となるすべての日付を取得して、その日付のデータを削除
        target_dates = set(item.date for item in items)
        for date in target_dates:
            session.exec(delete(Schedule).where(Schedule.date == date))
            print(f"🗑️ {date} のスケジュールを削除")

        # 📌 新しいスケジュールを追加
        for item in items:
            print("📌 追加中:", item)
            schedule = Schedule(
                user_id=item.user_id,
                username=item.username,
                date=item.date,
                expected_login_time=item.expected_login_time,
                login_time=item.login_time,
                is_holiday=item.is_holiday,
                work_code=item.work_code  # ← work_code も忘れずに
            )
            session.add(schedule)

        session.commit()
    print("✅ 保存完了！")

    return {"message": f"{len(items)} 件のスケジュールを保存しました"}

@app.get("/schedules")
def get_schedules(date: Optional[str] = Query(None)):
    with Session(engine) as session:
        if date:
            # 日付が指定されたらその日のデータだけ取得
            statement = select(Schedule).where(Schedule.date == date)
        else:
            # 指定がない場合は全件取得（今まで通り）
            statement = select(Schedule)
        results = session.exec(statement).all()
        return results



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

            # ★ 勤務指定チェック
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

            # 未ログインチェック
            if now >= expected_dt and not item.login_time:
                failed_logins.append({
                    "username": item.username,
                    "date": item.date,
                    "reason": f"未ログイン（予定時刻: {item.expected_login_time}）"
                })

        if failed_logins:
            message_lines = ["🚨 ログイン遅れユーザー（予定時刻超過 or 勤務指定違反）"]
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
