from sqlmodel import SQLModel, Field
from typing import Optional

class Schedule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    username: str
    date: str
    expected_login_time: Optional[str]  # ← ★ログイン予定時刻を追加！
    login_time: Optional[str]
    is_holiday: bool
