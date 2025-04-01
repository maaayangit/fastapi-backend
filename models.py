from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import date, time, datetime

class Schedule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    username: str
    date: date  # ← str → date に変更
    expected_login_time: Optional[time] = None  # ← str → time に変更
    login_time: Optional[time] = None  # ← str → time に変更
    is_holiday: bool
    work_code: Optional[str] = None

# ✅ 追加：計画登録の操作ログ
class PlanLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    date: date  # ← str → date に変更
    expected_login_time: time  # ← str → time に変更
    registered_at: datetime = Field(default_factory=datetime.utcnow)
