import os
import requests
from requests_oauthlib import OAuth1
from datetime import datetime, timezone, timedelta

X_API_KEY = os.getenv("X_API_KEY")
X_API_SECRET = os.getenv("X_API_SECRET")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET")

START_DATE = datetime(2026, 4, 28, tzinfo=timezone(timedelta(hours=9)))

def get_day_number():
    now = datetime.now(timezone(timedelta(hours=9)))
    return (now - START_DATE).days + 1

def build_tweet(day: int) -> str:
    return f"""【Day{day:03d} / エンジニアへの道】

今日もPythonを学ぶ。
30歳までに月収100万を目指す28歳の記録。

進捗はGitHubに積み上げ中📈
github.com/KAIMKH0116/python-practice

#駆け出しエンジニア #Python #毎日投稿"""

def post_to_x(text: str):
    auth = OAuth1(X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET)
    url = "https://api.x.com/2/tweets"
    r = requests.post(url, auth=auth, json={"text": text}, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"X API Error: {r.status_code} {r.text}")
    print("Posted successfully")

def main():
    day = get_day_number()
    tweet = build_tweet(day)
    print(tweet)
    post_to_x(tweet)

if __name__ == "__main__":
    main()
