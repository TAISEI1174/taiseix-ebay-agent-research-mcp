# eBay Agent MCP

ChatGPT Custom MCP の検索コネクタ用ツールと、ChatGPT Agent の明示アクション用ツールを同じ MCP サーバーで公開します。

## 公開ツール

`tools/list` で返るツールは次の4つです。

```text
search
fetch
ebay_product_research
fetch_ebay_item
```

- `search` / `fetch`: Custom MCP 検索コネクタ用
- `ebay_product_research` / `fetch_ebay_item`: Agent の明示アクション用

`primary_use_case` は health check の説明項目だけです。MCPツールとしては登録しません。

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

商品リサーチで使うのは `EBAY_APP_ID`、`EBAY_CERT_ID`、`EBAY_MARKETPLACE_ID` です。`EBAY_DEV_ID`、`EBAY_AUTH_TOKEN`、`EBAY_SITE_ID`、`EBAY_COMPATIBILITY_LEVEL` は将来 Trading API を追加する時のために残しています。

## Health Check

`GET /` は次の形式です。

```json
{
  "status": "ok",
  "service": "ebay-agent-actions-mcp",
  "mcp_endpoint": "/mcp",
  "available_tools": [
    "search",
    "fetch",
    "ebay_product_research",
    "fetch_ebay_item"
  ],
  "primary_use_case": "agent_actions_for_ebay_product_research",
  "marketplace": "EBAY_US"
}
```

## Tool Details

### search

Custom MCP 検索コネクタ用です。

Arguments:

```text
query: str
```

戻り値は JSON text content で、`results` に `id`、`title`、`url` を含めます。

### fetch

Custom MCP 検索コネクタ用です。

Arguments:

```text
id: str
```

戻り値は JSON text content で、`id`、`title`、`text`、`url`、`metadata` を含めます。`text` は `EBAY_PRODUCT_RESEARCH_START` 形式です。

### ebay_product_research

Agent 明示アクション用です。

Arguments:

```text
query: str
limit: int = 10
```

eBay Browse API で商品検索し、seller risk / category risk / location risk / research risk を付けて、`EBAY_PRODUCT_RESEARCH_START` から `EBAY_PRODUCT_RESEARCH_END` までの文字列を返します。id/title/url だけの検索結果一覧は返しません。

### fetch_ebay_item

Agent 明示アクション用です。

Arguments:

```text
item_id: str
```

eBay Browse API で単品詳細を取得し、`EBAY_PRODUCT_RESEARCH_START` から `EBAY_PRODUCT_RESEARCH_END` までの文字列を返します。

## Render デプロイ

推奨 Render service name:

```text
taiseix-ebay-agent-actions-mcp
```

手動作成の場合:

```text
Runtime: Python
Build Command: pip install -r requirements.txt
Start Command: uvicorn server:app --host 0.0.0.0 --port $PORT
Health Check Path: /
```

MCP Server URL:

```text
https://<render-service-name>.onrender.com/mcp
```

## ChatGPT Custom MCP App

Agent 用 App 名:

```text
EBAY-AGENT-ACTIONS-01
```

設定:

```text
MCP Server URL:
https://<render-service-name>.onrender.com/mcp

Authentication:
Access token / API key

Header scheme:
Bearer

API key:
Render の MCP_API_KEY と同じ値
```

## tools/list 確認

Render Shell で確認します。

```bash
curl -i -X POST "http://127.0.0.1:10000/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $MCP_API_KEY" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

成功条件:

```json
"tools": [
  {"name": "search"},
  {"name": "fetch"},
  {"name": "ebay_product_research"},
  {"name": "fetch_ebay_item"}
]
```

## 秘密情報の注意

- `.env` は `.gitignore` 対象です。
- GitHub に置くのは `.env.example` だけです。
- API key、Cert ID、OAuth token、MCP key をコード、README、ログに出しません。
