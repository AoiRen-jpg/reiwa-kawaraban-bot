import os, hashlib, time, textwrap, re
import requests, feedparser

# ========= 設定エリア（必要なら編集） =========
TEMPLATE_VARIANT = os.getenv("TEMPLATE_VARIANT", "normal")  # "normal" or "yasashii"
POST_SLOTS_PER_RUN = int(os.getenv("POST_SLOTS_PER_RUN", "3"))  # 1回の実行で最大投稿本数
RSS_LIST = [
  "https://news.google.com/rss/search?q=%E6%97%A5%E9%8A%80+OR+%E9%87%91%E5%88%A9&hl=ja&gl=JP&ceid=JP:ja",
  "https://news.google.com/rss/search?q=%E7%B5%8C%E5%9B%A3%E9%80%A3+OR+%E6%98%A5%E9%97%98&hl=ja&gl=JP&ceid=JP:ja",
  "https://news.google.com/rss/search?q=%E5%A2%97%E7%A8%8E+OR+%E7%A8%8E%E5%88%B6&hl=ja&gl=JP&ceid=JP:ja",
  "https://news.google.com/rss/search?q=AI+%E8%A6%8F%E5%88%B6+OR+%E6%94%BF%E7%AD%96&hl=ja&gl=JP&ceid=JP:ja",
]
SEEN_FILE = "seen.txt"  # 重複防止用（ハッシュを追記）。ワークフローでコミット保存
FIXED_TAGS = [
  "令和幕府瓦版","時事ニュース","日本経済","政治ニュース","ニュース解説",
  "庶民目線ニュース","速報","解説","トレンド","日本の今","Xニュース","今日のニュース",
]
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")
# ============================================

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
    # Googleニュースの中継URLから元記事URLへ
    try:
        r = requests.get(url, allow_redirects=True, timeout=timeout, headers={"User-Agent":"curl/8"})
        return r.url
    except Exception:
        return url

def sha(u:str)->str:
    return hashlib.sha256(u.encode("utf-8")).hexdigest()

def clip_len(txt:str, maxlen:int)->str:
    return txt if len(txt)<=maxlen else (txt[:maxlen-1]+"…")

def build_prompt(title:str, summary:str, link:str, variant:str)->str:
    base_rules = textwrap.dedent(f"""
    あなたは「令和幕府瓦版」シリーズの編集者。事実誤認の断定を避け、煽り表現を控える。
    出力はX（旧Twitter）用の**見やすい改行**で、**280字±30**を目安に作る。

    形式：
    1) 導入（瓦版×現代の語り口）
    2) 要点（簡潔に）
    3) 瓦版屋のひとこと（1行）
    4) ハッシュタグ：固定12個 + 記事から固有名詞ベースのSEOタグ 最大10（日本語優先）
    ※ URLは末尾に短く置いてよい（省略可）

    固定タグ（12）:
    #{' #'.join(FIXED_TAGS)}
    """).strip()

    if variant == "yasashii":
        base_rules += "\n\n口調：小学生にもわかる“やさしい瓦版”。難語はかみくだく。"
    else:
        base_rules += "\n\n口調：通常版（瓦版風×現代の読みやすさ）。"

    user = textwrap.dedent(f"""
    # 入力
    タイトル: {title}
    要旨: {summary}
    出典URL: {link}

    # 出力要件
    - 本文は日本語
    - 280字±30（ハッシュタグ含む全体で収まるよう調整）
    - ハッシュタグは固定12 + SEOタグ（最大10、固有名詞中心）
    - ハッシュタグは文末にまとめる
    - 政治的断定・攻撃的表現は避ける
    """).strip()
    return base_rules + "\n\n" + user

def call_openai(prompt:str)->str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role":"system","content":"You are a careful Japanese editor for X posts."},
            {"role":"user","content": prompt}
        ],
        "temperature": 0.4,
    }
    r = requests.post(url, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def post_to_x(text:str):
    url = "https://api.x.com/2/tweets"
    headers = {
        "Authorization": f"Bearer {X_BEARER_TOKEN}",
        "Content-Type": "application/json"
    }
    r = requests.post(url, headers=headers, json={"text": text}, timeout=30)
    if r.status_code not in (200,201):
        raise RuntimeError(f"X API Error: {r.status_code} {r.text}")

def main():
    assert OPENAI_API_KEY, "OPENAI_API_KEY not set"
    assert X_BEARER_TOKEN, "X_BEARER_TOKEN not set"

    seen_hashes = load_seen()
    new_hashes = []
    picked = []

    # RSS収集（GoogleニュースはRSSのみ利用）
    items = []
    for feed in RSS_LIST:
        d = feedparser.parse(feed)
        for e in d.entries[:10]:
            link = e.link
            title = e.title
            summary = getattr(e, "summary", "")
            items.append({"title": title, "summary": summary, "link": link})

    # 重複チェック＋最終URL解決
    for it in items:
        url_final = final_url(it["link"])
        h = sha(url_final)
        if h in seen_hashes:
            continue
        it["final"] = url_final
        it["hash"] = h
        picked.append(it)
        if len(picked) >= POST_SLOTS_PER_RUN:
            break

    if not picked:
        print("No new items. Done.")
        return

    for it in picked:
        prompt = build_prompt(it["title"], it["summary"], it["final"], TEMPLATE_VARIANT)
        draft = call_openai(prompt)

        # 念のための最終280字調整（超えたら末尾からカット）
        # Xは実際にはコードポイント等で微差が出るため、やや余裕を持ってカット
        tweet = draft
        if len(tweet) > 280:
            tweet = clip_len(tweet, 280)

        post_to_x(tweet)
        print("Posted:", it["final"])
        new_hashes.append(it["hash"])
        time.sleep(2)  # 連投しないマナー

    # 見たURLのハッシュを保存（ワークフローでコミット）
    if new_hashes:
        save_seen(new_hashes)

if __name__ == "__main__":
    import os
    main()
