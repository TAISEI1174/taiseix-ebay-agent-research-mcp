# eBay Agent Research MCP

ChatGPT Agent mode / Custom MCP App から eBay Browse API を呼び出し、eBay 上の商品リサーチを行うための read-only MCP サーバーです。最初の目的は eBay 受信箱ではなく、商品検索と商品詳細取得です。

## 目的

- `search` で eBay 商品候補を検索する
- `fetch` で `search` が返した商品 ID の詳細を取得する
- ChatGPT が比較、相場確認、再販売候補の一次調査に使いやすい `EBAY_PRODUCT_RESEARCH_START` 形式のテキストを返す
- 秘密値は Render Environment Variables だけに置き、GitHub には入れない

## ファイル構成

```text
server.py
requirements.txt
README.md
.env.example
.gitignore
render.yaml
docs/checkpoint.md
docs/codex_notes.md
```

## 必要な環境変数

Render の Environment Variables に設定します。実値を GitHub、README、ログ、コメントに貼らないでください。

```text
MCP_API_KEY
EBAY_APP_ID
EBAY_CERT_ID
EBAY_DEV_ID
EBAY_AUTH_TOKEN
EBAY_SITE_ID=0
EBAY_COMPATIBILITY_LEVEL=1231
EBAY_MARKETPLACE_ID=EBAY_US
```

今回の商品リサーチで実際に使うのは `EBAY_APP_ID`、`EBAY_CERT_ID`、`EBAY_MARKETPLACE_ID` です。`EBAY_DEV_ID`、`EBAY_AUTH_TOKEN`、`EBAY_SITE_ID`、`EBAY_COMPATIBILITY_LEVEL` は将来 Trading API の `GetMyMessages` を追加する時のために維持しています。

`Invalid Host header` が出る場合だけ、追加で `MCP_PUBLIC_HOST=<render-service-name>.onrender.com` を入れてください。通常は Render の `RENDER_EXTERNAL_HOSTNAME` から自動で許可します。

## Render デプロイ手順

1. GitHub に新規リポジトリを作り、このファイル一式を push します。
2. Render で `New +` -> `Web Service` を選び、GitHub リポジトリを接続します。
3. `render.yaml` を使う場合は Blueprint として作成します。手動作成の場合は次を設定します。
   - Runtime: `Python`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn server:app --host 0.0.0.0 --port $PORT`
   - Health Check Path: `/`
4. Environment Variables に上記の値を入れます。
5. デプロイ後、トップ URL を開いて次のような JSON が返ることを確認します。

```json
{
  "status": "ok",
  "service": "ebay-agent-research-mcp",
  "mcp_endpoint": "/mcp",
  "available_tools": ["search", "fetch"],
  "primary_use_case": "ebay_product_research",
  "marketplace": "EBAY_US"
}
```

MCP Server URL は次の形式です。

```text
https://<render-service-name>.onrender.com/mcp
```

## ChatGPT Custom MCP App 登録手順

1. ChatGPT の Settings -> Apps -> Advanced settings で Developer mode を有効にします。
2. Apps settings で `Create app` を選びます。
3. 次の値を入力します。

```text
Name:
EBAY-AGENT-RESEARCH-01

Description:
Search and fetch eBay product research data for ChatGPT Agent mode.

MCP Server URL:
https://<render-service-name>.onrender.com/mcp

Authentication:
Access token / API key

Header scheme:
Bearer

API key:
Render の MCP_API_KEY と同じ値
```

4. 作成後、ツール一覧に `search` と `fetch` が見えることを確認します。
5. ツール定義を変更した後は、古い Custom MCP App がキャッシュを持つ場合があります。その場合は新しい名前で Custom MCP App を作り直してください。

## テストプロンプト

ChatGPT の App ページまたは Agent で試します。

```text
eBayで「Japanese Pokemon card SAR」を検索し、見つかった結果をfetchしてください。fetchのtext本文を要約せず、EBAY_PRODUCT_RESEARCH_STARTからEBAY_PRODUCT_RESEARCH_ENDまでそのまま表示してください。
```

別テスト:

```text
eBayで「Mew AR Japanese Pokemon card」を検索し、価格・seller・condition・item URLを比較できる形で取得してください。
```

## 成功判定

- Render のトップ URL が `status: ok` を返す
- `available_tools` が `["search", "fetch"]`
- ChatGPT Custom MCP App で接続できる
- `search` が eBay 商品候補を返す
- `fetch` が商品詳細を返す
- `fetch` の `text` が `EBAY_PRODUCT_RESEARCH_START` 形式で始まる
- 秘密値が GitHub、README、ログに出ていない

## よくある失敗

- `401 Unauthorized`: ChatGPT 側の API key が Render の `MCP_API_KEY` と一致していません。
- `503 MCP_API_KEY is not configured`: Render に `MCP_API_KEY` が入っていません。
- `Invalid Host header`: `MCP_PUBLIC_HOST=<render-service-name>.onrender.com` を Render に追加してください。
- `eBay OAuth token request failed`: `EBAY_APP_ID` または `EBAY_CERT_ID` が Production 用ではない、または値が間違っています。
- `search` は返るが `fetch` が失敗する: `fetch` には `search` が返した `ebay_item|v1|...` の ID をそのまま渡してください。
- ChatGPT に古いツール一覧が残る: Custom MCP App を新しい名前で作り直してください。

## ローカル確認

PowerShell 例:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:MCP_API_KEY="local_test_key"
$env:EBAY_APP_ID="replace_me"
$env:EBAY_CERT_ID="replace_me"
$env:EBAY_MARKETPLACE_ID="EBAY_US"
.\.venv\Scripts\python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/
```

MCP Inspector を使う場合は `http://127.0.0.1:8000/mcp` を指定し、Authorization header に `Bearer local_test_key` を入れます。ChatGPT からローカルを直接見る場合は HTTPS が必要なので、ngrok などで公開 URL を作って `/mcp` を登録します。

## 秘密情報の注意

- `.env` は `.gitignore` 対象です。
- GitHub に置くのは `.env.example` だけです。
- `server.py` は秘密値を出力せず、health check でも `configured: true/false` だけを返します。
- チャットで共有された鍵もコードには貼らず、Render Environment Variables または Secrets に入れてください。

## 参照した公式ドキュメント

- OpenAI MCP: https://developers.openai.com/api/docs/mcp
- ChatGPT Developer mode: https://developers.openai.com/api/docs/guides/developer-mode
- eBay Browse API search: https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search
- eBay Browse API getItem: https://developer.ebay.com/api-docs/buy/browse/resources/item/methods/getItem
- eBay OAuth authorization: https://developer.ebay.com/develop/guides-v2/authorization

## 次の拡張予定

- eBay Trading API `GetMyMessages` を別ツールまたは別モジュールとして追加
- sold/completed 相場取得用の別 API 調査
- Google Sheets 保存
- 定期実行
- 商品スコアリング
- 利益率計算
- Seller 評価の自動化
