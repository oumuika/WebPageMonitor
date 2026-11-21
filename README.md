# WebPageMonitor

Webページの変化を検知して、Discordに差分つきで通知します。
GitHub Actionsで30分ごとに自動実行されます。

## セットアップ

1. Discordで通知したいチャンネルの「チャンネルの編集 → 連携サービス → ウェブフック」からWebhookを作り、URLをコピーする
2. このリポジトリの **Settings → Secrets and variables → Actions → New repository secret** で、名前 `DISCORD_WEBHOOK_URL`、値にWebhook URLを登録する
3. `targets.json` に監視したいページを書く
4. **Actions** タブ → 「Web page monitor」 → 「Run workflow」で手動実行して動作確認する
   - 1回目はページ内容を `state/` に保存するだけで、通知はしません
   - 2回目以降、内容が変わっていればDiscordに通知します

## targets.json の書き方

```json
[
  { "name": "お知らせページ", "url": "https://example.com/news", "selector": "#news-list" },
  { "name": "商品ページ", "url": "https://example.com/item/123", "selector": ".price" }
]
```

| 項目 | 内容 |
|---|---|
| `name` | Discord通知に表示する名前 |
| `url` | 監視するページのURL |
| `selector` | 監視する部分のCSSセレクタ。`null` ならページ全体 |

### ASOBI TICKET の受付ページ

ASOBI TICKET はJavaScriptで描画されるため、`"type": "asobiticket"` を指定すると、ページが内部で使っている公開APIから受付一覧（受付状態・在庫・期間）とお知らせを読みます。

```json
{ "name": "表示名", "type": "asobiticket", "url": "https://asobiticket2.asobistore.jp/booths/xxxx", "booth": "xxxx", "mention": "@everyone" }
```

- `booth` はURL末尾のID
- 受付中かつ完売ではない受付が新しく現れたとき（完売からの復活・新しい受付の開始）は、`mention` を付けて強調通知します。メンションが不要なら `mention` を消してください

### 注意

アクセス数や日時など毎回変わる部分があると、変化がなくても通知されてしまいます。`selector` で見たい部分に絞るのがおすすめです（ブラウザで要素を右クリック →「検証」で調べられます）。

## メモ

- チェック間隔は `.github/workflows/monitor.yml` の `cron` で変更できます（UTC基準）。privateリポジトリはActionsの無料実行時間に月ごとの上限があるので、間隔を短くしすぎないように注意してください。
- JavaScriptで中身を描画するページ（一部のSPAなど）は内容を取得できません。
- 監視対象や `selector` を変えた場合、最初の1回は保存だけになります。
