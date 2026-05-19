# Codex Notes

## 設計判断

- アプリ種別は UI なしの `tool-only` MCP。
- ChatGPT Custom MCP / data-only app 互換性を優先し、任意ツール名ではなく `search` / `fetch` を採用。
- eBay 商品リサーチは Browse API を使用。
- eBay 受信箱は今回の主目的ではないため未実装。将来追加する場合は Trading API `GetMyMessages` を別モジュール化する。
- eBay OAuth は client credentials grant で Application access token を取得し、メモリキャッシュする。
- access token、client secret、MCP API key はログやレスポンスに出さない。

## FastMCP 互換性

- `@mcp.tool(... output_schema=...)` は使わない。
- `structured_output=False` を指定し、戻り値は `TextContent` 1 件の JSON 文字列にしている。
- DNS rebinding protection のため `TransportSecuritySettings` を設定している。
- Render のホストは `RENDER_EXTERNAL_HOSTNAME`、必要に応じて `MCP_PUBLIC_HOST` から許可する。

## 公式ドキュメント確認

- OpenAI MCP docs では data-only / company knowledge 互換のため `search` と `fetch` の read-only tool が推奨されている。
- ChatGPT Developer mode docs では remote MCP server を ChatGPT Apps settings から作成し、Developer Mode tool として会話で使う流れが説明されている。
- eBay Browse API docs では `item_summary/search` と `item/{item_id}` が商品検索・商品詳細取得の対象。
- eBay Authorization docs では Production token endpoint、Basic 認証、`grant_type=client_credentials` が示されている。
