# Checkpoint

## 現在の状態

- 新規 GitHub リポジトリ用の最小構成として、Python + Starlette + FastMCP の MCP サーバーを作成した。
- MCP ツールは `search` と `fetch` の 2 つだけにしている。
- `search` は eBay Browse API `item_summary/search` を呼び、`id`、`title`、`url` を返す。
- `fetch` は eBay Browse API `getItem` を呼び、商品リサーチ用の `EBAY_PRODUCT_RESEARCH_START` 形式を `text` に入れて返す。
- `output_schema` は使っていない。FastMCP の `structured_output=False` で JSON text content として返す。
- `/` は health check、`/mcp` は MCP endpoint。
- `/mcp` は `Authorization: Bearer <MCP_API_KEY>` で保護している。

## まだ実装していないもの

- Trading API `GetMyMessages`
- Google Sheets 保存
- 自動実行
- スコアリング
- 利益率計算
- sold/completed listings の取得

## Render で確認すること

1. `GET /` が `status: ok` を返す。
2. `configured.MCP_API_KEY`、`configured.EBAY_APP_ID`、`configured.EBAY_CERT_ID` が `true`。
3. ChatGPT Custom MCP App が `/mcp` に接続できる。
4. `search` と `fetch` がツール一覧に出る。
5. `search` が商品候補を返す。
6. `fetch` が `EBAY_PRODUCT_RESEARCH_START` 形式を返す。

## 注意

秘密値はこのリポジトリに置かない。Render Environment Variables または GitHub Secrets だけに保存する。
