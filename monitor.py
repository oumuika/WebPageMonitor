#!/usr/bin/env python3
"""Webページの変化を検知して Discord に通知するスクリプト。

使い方:
  1. targets.json に監視したいページを書く
  2. 環境変数 DISCORD_WEBHOOK_URL に Discord の Webhook URL を入れる
  3. python monitor.py を定期実行する（GitHub Actions / cron など）

初回実行時はページの内容を保存するだけで通知しません。
2回目以降、前回と内容が違えば差分つきで Discord に通知します。
"""
import difflib
import hashlib
import json
import os
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
TARGETS_FILE = BASE_DIR / "targets.json"
STATE_DIR = BASE_DIR / "state"
DISCORD_LIMIT = 2000  # Discord のメッセージ文字数上限
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; page-monitor/1.0)",
    "Accept-Language": "ja,en;q=0.8",
}


def fetch_text(url: str, selector: str | None) -> str:
    """ページを取得し、比較用のテキストにして返す。"""
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


def build_message(name: str, url: str, old: str, new: str) -> str:
    diff = difflib.unified_diff(
        old.splitlines(), new.splitlines(), lineterm="", n=1
    )
    # 先頭の --- / +++ ヘッダー行は不要なので除く
    body = "\n".join(list(diff)[2:])
    header = f"🔔 **{name}** が更新されました\n<{url}>\n"
    room = DISCORD_LIMIT - len(header) - len("```diff\n\n```") - 20
    if len(body) > room:
        body = body[:room] + "\n…（以下省略）"
    return f"{header}```diff\n{body}\n```"


def notify(webhook: str, content: str) -> None:
    res = requests.post(webhook, json={"content": content}, timeout=30)
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
        name, url, selector = t.get("name") or t["url"], t["url"], t.get("selector")
        key = hashlib.sha256(f"{url}|{selector}".encode()).hexdigest()[:16]
        state_file = STATE_DIR / f"{key}.txt"

        try:
            new = fetch_text(url, selector)
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

        try:
            notify(webhook, build_message(name, url, old, new))
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
