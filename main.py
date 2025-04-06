import os
import json
import requests
import uuid
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client, Client
from dateutil import parser  # 🔄 JST変換に必要
from google.oauth2 import service_account
from googleapiclient.discovery import build

# 🌍 .env 読み込み
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
NOTIFICATION_WINDOW_SECONDS = int(os.getenv("NOTIFICATION_WINDOW_SECONDS", 30))  # デフォルト30秒

# 📁 calendar_config.json 読み込み
base_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(base_dir, "calendar_config.json")
with open(config_path, "r") as f:
    calendar_configs = json.load(f)

# ⏰ JST（日本時間）
JST = timezone(timedelta(hours=9))

# Supabase クライアント
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://morning-check-app.vercel.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    failed_logins = []

    # ✅ その日の planlog だけ取得
    records = supabase.table("planlog").select("*").eq("date", today).execute().data

    print(f"📅 本日: {today}")
    print(f"🕒 現在時刻（JST）: {now.strftime('%Y-%m-%d %H:%M:%S')}")

    for item in records:
        user_id = item["user_id"]
        expected_time = item.get("expected_login_time")
        login_time = item.get("login_time")
        triggered_at = item.get("alert_triggered_at")
        expire_at = item.get("alert_expire_at")

        if not expected_time:
            continue  # 予定がなければスキップ

        try:
            expected_dt = datetime.strptime(f"{today} {expected_time}", "%Y-%m-%d %H:%M").replace(tzinfo=JST)
        except ValueError:
            # fallback: 秒まで対応
            expected_dt = datetime.strptime(f"{today} {expected_time}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)

        if login_time:
            continue  # 出勤済ならスキップ

        # ✅ 予定時刻を過ぎていて未出勤
        if now >= expected_dt:
            if not triggered_at:
                # 初回成立
                triggered_at = now
                expire_at = triggered_at + timedelta(seconds=NOTIFICATION_WINDOW_SECONDS)

                supabase.table("planlog").update({
                    "alert_triggered_at": triggered_at.isoformat(),
                    "alert_expire_at": expire_at.isoformat()
                }).eq("user_id", user_id).eq("date", today).execute()

                failed_logins.append({
                    "user_id": user_id,
                    "date": today,
                    "reason": f"未ログイン（予定時刻: {expected_time}）"
                })

            elif expire_at:
                expire_dt = datetime.fromisoformat(expire_at).replace(tzinfo=JST)
                if now <= expire_dt:
                    # 通知継続期間中
                    failed_logins.append({
                        "user_id": user_id,
                        "date": today,
                        "reason": f"未ログイン（予定時刻: {expected_time}）"
                    })
                else:
                    print(f"⏱ 通知終了: user_id={user_id}")
        else:
            print(f"🕒 予定時刻未到達: user_id={user_id}, expected={expected_time}")

    # ✅ 通知実行（整形済み関数を使う）
    if failed_logins:
        notify_slack_formatted(failed_logins)
    else:
        print("✅ ログイン漏れはありませんでした")

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

@app.post("/update-login")
async def update_login_time(request: Request):
    data = await request.json()

    try:
        user_id = int(data["user_id"])
        date_str = data["date"]  # 形式: "2025-04-06"
        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()

        now_jst = datetime.now(JST)
        login_time_str = now_jst.strftime("%H:%M:%S")  # Supabaseはtime型なので文字列で渡す
        print(f"🔍 出勤登録: user_id={user_id}, date={date_obj}, login_time={login_time_str}")

        # ✅ planlog の login_time を更新
        response = supabase.table("planlog").update({
            "login_time": login_time_str
        }).eq("user_id", user_id).eq("date", str(date_obj)).execute()

        if response.data:
            print("✅ planlog 更新成功:", response.data)
            return {"message": "✅ 出勤時刻を記録しました"}
        else:
            print("⚠ planlog に該当データなし")
            return {"message": "⚠ 出勤予定ログが見つかりません。計画登録してください"}

    except Exception as e:
        print("❌ エラー:", str(e))
        return {"message": f"❌ エラーが発生しました: {str(e)}"}

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

def notify_slack_formatted(failed_logins: List[dict]):
    if not SLACK_WEBHOOK_URL:
        print("⚠ Slack Webhook URLが未設定です（.env確認）")
        return

    if not failed_logins:
        return

    today = datetime.now(JST).strftime("%Y-%m-%d")
    header = f"📢 *未出勤ユーザー通知 ({today})*\n"
    message_lines = []

    for entry in failed_logins:
        # ⏰ 現在時刻を通知に追加（ユニーク化）
        now_str = datetime.now(JST).strftime("%H:%M:%S")
        uniq = str(uuid.uuid4())[:6]
        line = f"• `{entry['user_id']}` : {entry['reason']}（{now_str} / ID:{uniq}）"
        message_lines.append(line)

    message = header + "\n".join(message_lines)

    response = requests.post(SLACK_WEBHOOK_URL, json={"text": message})
    if response.status_code != 200:
        print("Slack通知失敗:", response.text)
    else:
        print("✅ Slack通知成功！")


SERVICE_ACCOUNT_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)
calendar_service = build('calendar', 'v3', credentials=creds)

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

            start_dt = parser.isoparse(start).astimezone(JST)
            end_dt = parser.isoparse(end).astimezone(JST)

            supabase.table("calendar_events").upsert({
                "id": event_id,
                "calendar_id": calendar_id,
                "group_name": group_name,
                "title": title,
                "description": description,
                "start_time": start_dt.isoformat(),
                "end_time": end_dt.isoformat(),
                "updated_at": updated,
                "synced_at": datetime.utcnow().isoformat()
            }, on_conflict=["id"]).execute()

            total_synced += 1

    return {"message": f"{total_synced} 件のイベントを同期しました"}
