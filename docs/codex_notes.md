# Codex Notes

## 設計判断

- Agent 編集画面での追加性を上げるため、connector 用 `search` / `fetch` と action 用 `ebay_product_research` / `fetch_ebay_item` を同じ MCP に登録する。
- `primary_use_case` は説明メタデータであり、MCPツールとしては登録しない。
- `output_schema` は使わない。
- `structured_output=False` を維持する。
- 秘密値はログ、コード、README に出さない。

## Tool Split

- `search`: id/title/url を返す検索コネクタ用。
- `fetch`: connector の fetch 用。`text` に商品リサーチ本文を含める。
- `ebay_product_research`: Agent 明示アクション用。検索結果をリスク付き本文として直接返す。
- `fetch_ebay_item`: Agent 明示アクション用。単品詳細をリスク付き本文として直接返す。
