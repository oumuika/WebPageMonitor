#!/usr/bin/env python3
"""Webページの変化を検知して Discord に通知するスクリプト。

使い方:
  1. targets.json に監視したいページを書く
  2. 環境変数 DISCORD_WEBHOOK_URL に Discord の Webhook URL を入れる
  3. python monitor.py を定期実行する（GitHub Actions / cron など）

初回実行時はページの内容を保存するだけで通知しません。
2回目以降、前回と内容が違えば差分つきで Discord に通知します。

targets.json の "type":
  "html"（省略時） … 普通のWebページ。selector で監視範囲を絞れる
  "asobiticket"   … ASOBI TICKET の受付ページ。"booth" にURL末尾のIDを書く
"""
import difflib
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
TARGETS_FILE = BASE_DIR / "targets.json"
STATE_DIR = BASE_DIR / "state"
DISCORD_LIMIT = 2000  # Discord のメッセージ文字数上限
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.8",
}


# ---------------------------------------------------------------- 普通のWebページ

def fetch_html(target: dict) -> str:
    url, selector = target["url"], target.get("selector")
    res = requests.get(url, headers=HEADERS, timeout=30)
    res.raise_for_status()
    # Shift_JIS などの日本語サイトで文字化けしないように推定エンコーディングを使う
    if res.encoding is None or res.encoding.lower() == "iso-8859-1":
        res.encoding = res.apparent_encoding
    soup = BeautifulSoup(res.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    if selector:
        nodes = soup.select(selector)
        if not nodes:
            raise ValueError(f"セレクタ '{selector}' に一致する要素が見つかりません")
    else:
        nodes = [soup.body or soup]

    lines = []
    for node in nodes:
        for line in node.get_text("\n").splitlines():
            line = " ".join(line.split())
            if line:
                lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------- ASOBI TICKET
# ページは JavaScript で描画されるため、ページが内部で使っている公開APIを直接読む。

ASOBI_API = "https://asobi-ticket.api.app.t-riple.com/api/v1/public"
ASOBI_HEADERS = {
    **HEADERS,
    "Accept": "application/json",
    "Origin": "https://asobiticket2.asobistore.jp",
    "Referer": "https://asobiticket2.asobistore.jp/",
}
ENTRY_TYPE = {"fcfs": "先着", "lottery": "抽選"}
PERIOD_STATUS = {
    "before_entry_period": "受付前",
    "within_entry_period": "受付中",
    "after_entry_period": "受付終了",
}
STOCK_STATUS = {"sold_out": "完売", None: "-"}
SOLD_OUT_LABEL = "在庫: 完売"


def _fmt_time(s: str | None) -> str:
    if not s:
        return "-"
    return datetime.fromisoformat(s).strftime("%Y/%m/%d %H:%M")


def _asobi_get(path: str) -> dict:
    res = requests.get(f"{ASOBI_API}/{path}", headers=ASOBI_HEADERS, timeout=30)
    res.raise_for_status()
    return res.json()


def fetch_asobiticket(target: dict) -> str:
    booth = target["booth"]
    receptions = _asobi_get(f"receptions?booth_slug={booth}")["data"]
    news = _asobi_get(f"news_items?page=1&per_page=50&booth_slug={booth}")["data"]

    lines = []
    for r in receptions:
        a = r["attributes"]
        kind = ENTRY_TYPE.get(a["entry_type"], a["entry_type"])
        label = a["name"] if kind in a["name"] else f"{a['name']}（{kind}）"
        lines.append(
            f"【受付】{label}"
            f" {PERIOD_STATUS.get(a['entry_period_status'], a['entry_period_status'])}"
            f" / 在庫: {STOCK_STATUS.get(a.get('stock_status'), a.get('stock_status'))}"
            f" / {_fmt_time(a['entry_period_starts_at'])}〜{_fmt_time(a['entry_period_ends_at'])}"
        )
    for n in news:
        a = n["attributes"]
        lines.append(f"【お知らせ】{a['name']}（{_fmt_time(a.get('published_at'))}）")
    return "\n".join(lines)


def asobi_buyable_lines(text: str) -> set[str]:
    """受付中かつ完売ではない受付の行。"""
    return {
        line for line in text.splitlines()
        if line.startswith("【受付】") and " 受付中 " in line and SOLD_OUT_LABEL not in line
    }


FETCHERS = {"html": fetch_html, "asobiticket": fetch_asobiticket}


# ---------------------------------------------------------------- 通知

def build_message(name: str, url: str, old: str, new: str, alert: str = "") -> str:
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=1)
    body = "\n".join(list(diff)[2:])  # 先頭の --- / +++ ヘッダー行は不要
    header = f"{alert}🔔 **{name}** が更新されました\n<{url}>\n"
    room = DISCORD_LIMIT - len(header) - len("```diff\n\n```") - 20
    if len(body) > room:
        body = body[:room] + "\n…（以下省略）"
    return f"{header}```diff\n{body}\n```"


def notify(webhook: str, content: str) -> None:
    res = requests.post(
        webhook,
        json={"content": content, "allowed_mentions": {"parse": ["everyone", "roles", "users"]}},
        timeout=30,
    )
    res.raise_for_status()


def main() -> int:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("環境変数 DISCORD_WEBHOOK_URL が設定されていません", file=sys.stderr)
        return 1

    targets = json.loads(TARGETS_FILE.read_text(encoding="utf-8"))
    STATE_DIR.mkdir(exist_ok=True)
    failed = False

    for t in targets:
        kind = t.get("type", "html")
        name = t.get("name") or t["url"]
        key_src = json.dumps({k: t.get(k) for k in ("type", "url", "selector", "booth")}, sort_keys=True)
        state_file = STATE_DIR / f"{hashlib.sha256(key_src.encode()).hexdigest()[:16]}.txt"

        try:
            new = FETCHERS[kind](t)
        except Exception as e:  # 1ページの失敗で他のページの監視を止めない
            print(f"[エラー] {name}: {e}", file=sys.stderr)
            failed = True
            continue

        if not state_file.exists():
            state_file.write_text(new, encoding="utf-8")
            print(f"[初回] {name}: 内容を保存しました（通知なし）")
            continue

        old = state_file.read_text(encoding="utf-8")
        if old == new:
            print(f"[変化なし] {name}")
            continue

        alert = ""
        if kind == "asobiticket" and asobi_buyable_lines(new) - asobi_buyable_lines(old):
            mention = t.get("mention", "")
            alert = f"{mention} 🎫 **購入できる受付があります！急いで確認してください**\n".lstrip()

        try:
            notify(webhook, build_message(name, t["url"], old, new, alert))
        except Exception as e:
            # 通知に失敗したら状態を更新せず、次回もう一度通知を試みる
            print(f"[通知失敗] {name}: {e}", file=sys.stderr)
            failed = True
            continue
        state_file.write_text(new, encoding="utf-8")
        print(f"[変化あり] {name}: Discord に通知しました")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
