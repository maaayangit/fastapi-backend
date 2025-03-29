import requests
import schedule
import time

def run_check():
    print("⏰ ログインチェック実行中...")
    try:
        res = requests.get("http://localhost:8000/login-check")
        print("✅ 結果:", res.json())
    except Exception as e:
        print("❌ エラー:", e)

# 毎朝7:35に実行（自由に変更可能）
schedule.every().day.at("07:35").do(run_check)

print("🔁 自動チェック開始！Ctrl+Cで停止できます")
while True:
    schedule.run_pending()
    time.sleep(1)
