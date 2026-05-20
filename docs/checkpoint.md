# Checkpoint

## 現在の状態

- 1つの MCP サーバーに4ツールを登録している。
- `search` / `fetch` は Custom MCP 検索コネクタ用。
- `ebay_product_research` / `fetch_ebay_item` は Agent 明示アクション用。
- `primary_use_case` は health check の説明項目だけで、MCPツールではない。
- `output_schema` は使っていない。
- `ebay_product_research` と `fetch_ebay_item` は `str` を返す。
- `search` と `fetch` は connector 互換の JSON text content を返す。

## tools/list 成功条件

`tools/list` に以下4つが出ること。

```text
search
fetch
ebay_product_research
fetch_ebay_item
```

## Render で確認すること

1. `GET /` の `available_tools` が4つ。
2. `/mcp` の `tools/list` 実結果が4つ。
3. `ebay_product_research` が `EBAY_PRODUCT_RESEARCH_START` 形式を返す。
4. `fetch_ebay_item` が `EBAY_PRODUCT_RESEARCH_START` 形式を返す。

## 注意

秘密値はこのリポジトリに置かない。Render Environment Variables または GitHub Secrets だけに保存する。
