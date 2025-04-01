import os
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta, timezone
import requests
from dotenv import load_dotenv
from supabase import create_client, Client

# 🌍 .env 読み込み
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

# 📁 calendar_config.json 読み込み（ここに入れる）
base_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(base_dir, "calendar_config.json")
with open(config_path, "r") as f:
    calendar_configs = json.load(f)

# ⏰ JST（日本時間）
JST = timezone(timedelta(hours=9))

# Supabase クライアント
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()

# CORS設定（Reactアプリと連携）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://morning-check-app.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic モデル
class ScheduleItem(BaseModel):
    user_id: int
    username: str
    date: str
    expected_login_time: Optional[str]
    login_time: Optional[str]
    is_holiday: bool
    work_code: Optional[str] = None

class PlanLogItem(BaseModel):
    user_id: int
    date: str
    expected_login_time: str
    registered_at: Optional[str] = None

@app.post("/upload-schedule")
async def upload_schedule(items: List[ScheduleItem]):
    if not items:
        return {"message": "スケジュールが空です"}

    for item in items:
        try:
            print("📝 アップロード対象:", item.dict())
            # 既存削除＆新規追加
            supabase.table("schedule").delete().eq("user_id", item.user_id).eq("date", item.date).execute()
            supabase.table("schedule").insert(item.dict()).execute()
        except Exception as e:
            print("❌ エラー発生:", e)
            return {"message": f"エラーが発生しました: {str(e)}", "item": item.dict()}

    return {"message": f"{len(items)} 件のスケジュールを保存しました"}


@app.get("/schedules")
def get_schedules(date: Optional[str] = Query(None)):
    query = supabase.table("schedule").select("*")
    if date:
        query = query.eq("date", date)
    return query.execute().data

@app.get("/login-check")
def login_check():
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    records = supabase.table("schedule").select("*").eq("date", today).eq("is_holiday", False).execute().data

    failed_logins = []
    for item in records:
        if not item.get("expected_login_time"):
            continue
        expected_dt = datetime.strptime(f"{item['date']} {item['expected_login_time']}", "%Y-%m-%d %H:%M").replace(tzinfo=JST)

        if item.get("work_code") == "★07A" and expected_dt >= expected_dt.replace(hour=7, minute=0):
            failed_logins.append({"user_id": item["user_id"], "username": item["username"], "date": item["date"], "reason": f"勤務指定（★07A）より遅い: {item['expected_login_time']}"})
            continue
        elif item.get("work_code") == "★11A" and expected_dt >= expected_dt.replace(hour=11, minute=0):
            failed_logins.append({"user_id": item["user_id"], "username": item["username"], "date": item["date"], "reason": f"勤務指定（★11A）より遅い: {item['expected_login_time']}"})
            continue

        if now >= expected_dt and not item.get("login_time"):
            failed_logins.append({"user_id": item["user_id"], "username": item["username"], "date": item["date"], "reason": f"未ログイン（予定時刻: {item['expected_login_time']}）"})

    if failed_logins:
        notify_slack("\n".join([f"{entry['user_id']}（{entry['date']}）: {entry['reason']}" for entry in failed_logins]))

    return {"missed_logins": failed_logins}

@app.post("/update-expected-login")
async def update_expected_login(request: Request):
    data = await request.json()
    user_id = data["user_id"]
    date = data["date"]
    expected_login_time = data["expected_login_time"]

    existing = supabase.table("schedule").select("*").eq("user_id", user_id).eq("date", date).execute().data
    if existing:
        supabase.table("schedule").update({"expected_login_time": expected_login_time}).eq("user_id", user_id).eq("date", date).execute()
    else:
        supabase.table("schedule").insert({
            "user_id": user_id,
            "username": "（未設定）",
            "date": date,
            "expected_login_time": expected_login_time,
            "is_holiday": False,
            "login_time": None,
            "work_code": None
        }).execute()

    return {"message": "出勤予定を更新しました"}

@app.post("/log-plan")
def log_plan_entry(log: PlanLogItem):
    existing = supabase.table("planlog").select("*").eq("user_id", log.user_id).eq("date", log.date).execute().data
    now_str = datetime.now(JST).isoformat()

    if existing:
        supabase.table("planlog").update({
            "expected_login_time": log.expected_login_time,
            "registered_at": now_str
        }).eq("user_id", log.user_id).eq("date", log.date).execute()
        return {"message": "既存の出勤予定ログを更新しました", "log": log}
    else:
        data = log.dict()
        data["registered_at"] = now_str
        supabase.table("planlog").insert(data).execute()
        return {"message": "出勤予定ログを保存しました", "log": log}

@app.get("/log-plan")
def get_plan_logs(user_id: Optional[int] = None, date: Optional[str] = None):
    query = supabase.table("planlog").select("*")
    if user_id:
        query = query.eq("user_id", user_id)
    if date:
        query = query.eq("date", date)
    return query.execute().data

@app.get("/work-code")
def get_work_code(user_id: int, date: str):
    result = supabase.table("schedule").select("work_code").eq("user_id", user_id).eq("date", date).execute().data
    if not result:
        return {"work_code": None}
    return {"work_code": result[0].get("work_code")}

# Slack通知関数
def notify_slack(message: str):
    if not SLACK_WEBHOOK_URL:
        print("⚠ Slack Webhook URLが未設定です（.env確認）")
        return
    response = requests.post(SLACK_WEBHOOK_URL, json={"text": message})
    if response.status_code != 200:
        print("Slack通知失敗:", response.text)
    else:
        print("✅ Slack通知成功！")

#Googleカレンダーとの同期に向けたコード
from google.oauth2 import service_account
from googleapiclient.discovery import build

# サービスアカウントの認証ファイル
SERVICE_ACCOUNT_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)
calendar_service = build('calendar', 'v3', credentials=creds)

import json

# カレンダー設定を外部ファイルから読み込む
with open("calendar_config.json", "r") as f:
    calendar_configs = json.load(f)


@app.get("/sync-calendar")
def sync_calendar_events():
    now = datetime.utcnow().isoformat() + 'Z'
    future = (datetime.utcnow() + timedelta(days=45)).isoformat() + 'Z'
    total_synced = 0

    for config in calendar_configs:
        calendar_id = config["calendar_id"]
        group_name = config["group_name"]

        events_result = calendar_service.events().list(
            calendarId=calendar_id,
            timeMin=now,
            timeMax=future,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        for event in events:
            event_id = event['id']
            title = event.get('summary', '')
            description = event.get('description', '')
            start = event['start'].get('dateTime') or event['start'].get('date')
            end = event['end'].get('dateTime') or event['end'].get('date')
            updated = event.get('updated')

            supabase.table("calendar_events").upsert({
                "id": event_id,
                "calendar_id": calendar_id,
                "group_name": group_name,
                "title": title,
                "description": description,
                "start_time": start,
                "end_time": end,
                "updated_at": updated,
                "synced_at": datetime.utcnow().isoformat()
            }, on_conflict=["id"]).execute()

            total_synced += 1

    return {"message": f"{total_synced} 件のイベントを同期しました"}
