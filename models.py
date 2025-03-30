from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime

class Schedule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    username: str
    date: str
    expected_login_time: Optional[str] = None
    login_time: Optional[str] = None
    is_holiday: bool
    work_code: Optional[str] = None

# ✅ 追加：計画登録の操作ログ
class PlanLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    date: str
    expected_login_time: str
    registered_at: datetime = Field(default_factory=datetime.utcnow)
