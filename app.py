import os
import hashlib
import time
import textwrap
import requests
import feedparser
from requests_oauthlib import OAuth1

# ========= 設定エリア =========
TEMPLATE_VARIANT = os.getenv("TEMPLATE_VARIANT", "yasashii")  # ← やさしい版をデフォルトに
POST_SLOTS_PER_RUN = int(os.getenv("POST_SLOTS_PER_RUN", "1"))
RSS_LIST = [
    "https://news.google.com/rss/search?q=%E6%97%A5%E9%8A%80+OR+%E9%87%91%E5%88%A9&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=%E7%B5%8C%E5%9B%A3%E9%80%A3+OR+%E6%98%A5%E9%97%98&hl=ja&gl=JP&ceid=JP:ja",
]
SEEN_FILE = "seen.txt"
FIXED_TAGS = [
    "令和幕府かわら版","こどもニュース","やさしいニュース","日本経済","政治ニュース",
    "社会のしくみ","ニュース解説","庶民目線ニュース","教育ニュース","今日のニュース",
]
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 🔽 X（旧Twitter）の認証情報
X_API_KEY = os.getenv("X_API_KEY")
X_API_SECRET = os.getenv("X_API_SECRET")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET")
# ============================


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(x.strip() for x in f if x.strip())


def save_seen(hs):
    with open(SEEN_FILE, "a", encoding="utf-8") as f:
        for h in hs:
            f.write(h + "\n")


def final_url(url: str, timeout=12) -> str:
    try:
        r = requests.get(url, allow_redirects=True, timeout=timeout, headers={"User-Agent": "curl/8"})
        return r.url
    except Exception:
        return url


def sha(u: str) -> str:
    return hashlib.sha256(u.encode("utf-8")).hexdigest()


def clip_len(txt: str, maxlen: int) -> str:
    return txt if len(txt) <= maxlen else (txt[: maxlen - 1] + "…")


# 🟢 やさしい瓦版プロンプト
def build_prompt(title: str, summary: str, link: str, variant: str) -> str:
    base_rules = textwrap.dedent(f"""
    あなたは「令和幕府かわら版」の編集者です。
    小学生でも理解できるように、やさしく・わかりやすく・前向きにニュースを伝えます。
    文字数はハッシュタグ込みで280字前後。
    表記ゆれを避け、難しい言葉は必ず言い換えること。

    構成：
    🏯【令和幕府かわら版：○○の巻】
    💡なぜ話題になったの？
    🏮よいところ
    ⚖️ちょっと残念なところ
    📜かわら版屋のひとこと（まとめ）

    固定タグ：
    #{' #'.join(FIXED_TAGS)}

    出力例：
    🏯【令和幕府かわら版：お金の話】
    💡なぜ話題になったの？ → ○○だから。
    🏮よいところ → ○○がよくなる。
    ⚖️ちょっと残念 → ○○が心配。
    📜かわら版屋のひとこと → ○○するといいのう。
    #令和幕府かわら版 #こどもニュース
    """)

    user = textwrap.dedent(f"""
    タイトル: {title}
    要旨: {summary}
    出典URL: {link}
    """).strip()

    return base_rules + "\n\n" + user


def call_openai(prompt: str, fallback_text: str) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": "You are a kind Japanese editor for children’s news posts."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.5,
    }
    for attempt in range(3):
        r = requests.post(url, headers=headers, json=body, timeout=30)
        if r.status_code == 429:
            wait_sec = 5 * (attempt + 1)
            print(f"[OpenAI] 429 → {wait_sec}秒待ち")
            time.sleep(wait_sec)
            continue
        if 200 <= r.status_code < 300:
            return r.json()["choices"][0]["message"]["content"].strip()
        r.raise_for_status()

    print("[OpenAI] 連続429のためfallbackで投稿します")
    return fallback_text


def post_to_x(text: str):
    auth = OAuth1(X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET)
    url = "https://api.x.com/2/tweets"
    r = requests.post(url, auth=auth, json={"text": text}, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"X API Error: {r.status_code} {r.text}")


def main():
    assert OPENAI_API_KEY, "OPENAI_API_KEY not set"
    seen = load_seen()
    new_hashes = []
    picked = []

    # RSS取得
    items = []
    for feed in RSS_LIST:
        d = feedparser.parse(feed)
        for e in d.entries[:5]:
            items.append({
                "title": e.title,
                "summary": getattr(e, "summary", ""),
                "link": e.link,
            })

    # 未投稿だけ選ぶ
    for it in items:
        fin = final_url(it["link"])
        h = sha(fin)
        if h in seen:
            continue
        it["final"] = fin
        it["hash"] = h
        picked.append(it)
        if len(picked) >= POST_SLOTS_PER_RUN:
            break

    if not picked:
        print("No new items. Done.")
        return

    for it in picked:
        prompt = build_prompt(it["title"], it["summary"], it["final"], TEMPLATE_VARIANT)
        fallback = (
            "🏯【令和幕府かわら版：速報の巻】\n"
            f"本日の話題：{it['title']}\n"
            "――むずかしい話をやさしく伝える準備中じゃ。\n"
            "📜 かわら版屋のひとこと：また後ほど詳しくお届けするぞ！\n"
            "#令和幕府かわら版 #こどもニュース #やさしいニュース"
        )
        draft = call_openai(prompt, fallback)
        tweet = draft if len(draft) <= 280 else clip_len(draft, 280)
        post_to_x(tweet)
        print("Posted:", it["final"])
        new_hashes.append(it["hash"])
        time.sleep(2)

    if new_hashes:
        save_seen(new_hashes)


if __name__ == "__main__":
    main()
