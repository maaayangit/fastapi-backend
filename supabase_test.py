from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

supabase = create_client(url, key)

# schedule テーブルの中身を取得してみる
response = supabase.table("schedule").select("*").limit(5).execute()
print("✅ Supabase 接続成功！")
print(response.data)
