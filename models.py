from typing import Optional
from sqlmodel import SQLModel, Field

class Schedule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int
    username: str
    date: str
    expected_login_time: Optional[str] = None
    login_time: Optional[str] = None
    is_holiday: bool
    work_code: Optional[str] = None  # ← 勤務指定（例: ★07A）を追加
