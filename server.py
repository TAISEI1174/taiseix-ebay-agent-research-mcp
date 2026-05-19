from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

import requests
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import TextContent, ToolAnnotations
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route


SERVICE_NAME = "ebay-agent-research-mcp"
MCP_PATH = "/mcp"
EBAY_BROWSE_BASE_URL = "https://api.ebay.com/buy/browse/v1"
EBAY_OAUTH_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
DEFAULT_MARKETPLACE_ID = "EBAY_US"
DEFAULT_SEARCH_LIMIT = 10
REQUEST_TIMEOUT_SECONDS = 20

CONFIG_KEYS = [
    "MCP_API_KEY",
    "EBAY_APP_ID",
    "EBAY_CERT_ID",
    "EBAY_DEV_ID",
    "EBAY_AUTH_TOKEN",
    "EBAY_SITE_ID",
    "EBAY_COMPATIBILITY_LEVEL",
    "EBAY_MARKETPLACE_ID",
]

logger = logging.getLogger(SERVICE_NAME)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

_token_cache: dict[str, Any] = {"access_token": None, "expires_at": 0.0}


class ConfigurationError(RuntimeError):
    pass


class EbayApiError(RuntimeError):
    pass


class BearerAuthMiddleware:
    def __init__(self, app: Any, api_key: str | None, protected_path: str = MCP_PATH) -> None:
        self.app = app
        self.api_key = api_key
        self.protected_path = protected_path.rstrip("/")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_protected = path == self.protected_path or path.startswith(self.protected_path + "/")
        if not is_protected:
            await self.app(scope, receive, send)
            return

        if not self.api_key:
            response = JSONResponse(
                {"error": "MCP_API_KEY is not configured"},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        authorization = headers.get("authorization", "")
        expected = f"Bearer {self.api_key}"
        if not secrets.compare_digest(authorization, expected):
            response = JSONResponse({"error": "Unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def marketplace_id() -> str:
    return env("EBAY_MARKETPLACE_ID", DEFAULT_MARKETPLACE_ID) or DEFAULT_MARKETPLACE_ID


def configured_status() -> dict[str, bool]:
    return {key: bool(os.getenv(key)) for key in CONFIG_KEYS}


def split_csv_env(name: str) -> list[str]:
    value = env(name, "")
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def hostname_from_value(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
    return parsed.netloc or parsed.path.split("/")[0] or None


def build_transport_security_settings() -> TransportSecuritySettings:
    allowed_hosts = {
        "127.0.0.1",
        "127.0.0.1:*",
        "localhost",
        "localhost:*",
        "[::1]",
        "[::1]:*",
    }
    allowed_origins = {
        "https://chatgpt.com",
        "https://chat.openai.com",
        "http://127.0.0.1:*",
        "http://localhost:*",
    }

    for source in (
        env("RENDER_EXTERNAL_HOSTNAME"),
        env("MCP_PUBLIC_HOST"),
        env("MCP_PUBLIC_URL"),
    ):
        host = hostname_from_value(source)
        if host:
            allowed_hosts.add(host)
            allowed_hosts.add(f"{host}:*")
            allowed_origins.add(f"https://{host}")

    allowed_hosts.update(split_csv_env("MCP_ALLOWED_HOSTS"))
    allowed_origins.update(split_csv_env("MCP_ALLOWED_ORIGINS"))

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(allowed_hosts),
        allowed_origins=sorted(allowed_origins),
    )


def require_env(name: str) -> str:
    value = env(name)
    if not value:
        raise ConfigurationError(f"{name} is not configured")
    return value


def get_ebay_access_token() -> str:
    now = time.time()
    cached_token = _token_cache.get("access_token")
    if cached_token and float(_token_cache.get("expires_at", 0)) > now + 60:
        return str(cached_token)

    app_id = require_env("EBAY_APP_ID")
    cert_id = require_env("EBAY_CERT_ID")
    credentials = base64.b64encode(f"{app_id}:{cert_id}".encode("utf-8")).decode("ascii")

    response = requests.post(
        EBAY_OAUTH_TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": EBAY_OAUTH_SCOPE,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise EbayApiError(safe_http_error("eBay OAuth token request failed", response))

    payload = response.json()
    access_token = payload.get("access_token")
    expires_in = int(payload.get("expires_in", 7200))
    if not access_token:
        raise EbayApiError("eBay OAuth token response did not include an access token")

    _token_cache["access_token"] = access_token
    _token_cache["expires_at"] = now + max(expires_in - 120, 60)
    return str(access_token)


def ebay_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_ebay_access_token()}",
        "Accept": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": marketplace_id(),
    }


def safe_http_error(prefix: str, response: requests.Response) -> str:
    body = response.text.strip().replace("\n", " ")
    if len(body) > 500:
        body = body[:500] + "..."
    return f"{prefix}: status={response.status_code}, body={body}"


def browse_search(query: str) -> dict[str, Any]:
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("query must not be empty")

    response = requests.get(
        f"{EBAY_BROWSE_BASE_URL}/item_summary/search",
        headers=ebay_headers(),
        params={
            "q": cleaned_query,
            "limit": DEFAULT_SEARCH_LIMIT,
            "offset": 0,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise EbayApiError(safe_http_error("eBay Browse search failed", response))
    return response.json()


def browse_get_item(item_id: str) -> dict[str, Any]:
    cleaned_item_id = item_id.strip()
    if not cleaned_item_id:
        raise ValueError("id must not be empty")

    encoded_item_id = quote(cleaned_item_id, safe="")
    response = requests.get(
        f"{EBAY_BROWSE_BASE_URL}/item/{encoded_item_id}",
        headers=ebay_headers(),
        params={"fieldgroups": "PRODUCT,ADDITIONAL_SELLER_DETAILS"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise EbayApiError(safe_http_error("eBay Browse getItem failed", response))
    return response.json()


def mcp_json(payload: dict[str, Any]) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]


def prefixed_item_id(item_id: str) -> str:
    return item_id if item_id.startswith("ebay_item|") else f"ebay_item|{item_id}"


def strip_item_prefix(raw_id: str) -> str:
    if raw_id.startswith("ebay_item|"):
        return raw_id[len("ebay_item|") :]
    return raw_id


def first_image_url(item: dict[str, Any]) -> str:
    image = item.get("image") or {}
    if image.get("imageUrl"):
        return str(image["imageUrl"])
    thumbnail_images = item.get("thumbnailImages") or []
    if thumbnail_images and thumbnail_images[0].get("imageUrl"):
        return str(thumbnail_images[0]["imageUrl"])
    return ""


def amount_value(amount: dict[str, Any] | None) -> tuple[str, str]:
    if not amount:
        return "", ""
    return str(amount.get("value", "")), str(amount.get("currency", ""))


def best_price(item: dict[str, Any]) -> tuple[str, str]:
    for key in ("price", "currentBidPrice", "minimumPriceToBid"):
        value, currency = amount_value(item.get(key))
        if value:
            return value, currency
    return "", ""


def seller_info(item: dict[str, Any]) -> dict[str, str]:
    seller = item.get("seller") or {}
    return {
        "username": str(seller.get("username") or seller.get("userId") or ""),
        "feedback_score": str(seller.get("feedbackScore", "")),
        "feedback_percentage": str(seller.get("feedbackPercentage", "")),
    }


def location_summary(item: dict[str, Any]) -> str:
    location = item.get("itemLocation") or {}
    parts = [
        location.get("city"),
        location.get("stateOrProvince"),
        location.get("postalCode"),
        location.get("country"),
    ]
    return ", ".join(str(part) for part in parts if part)


def shipping_summary(item: dict[str, Any]) -> str:
    options = item.get("shippingOptions") or []
    if not options:
        return ""

    summaries: list[str] = []
    for option in options[:3]:
        cost_value, cost_currency = amount_value(option.get("shippingCost"))
        cost = f"{cost_value} {cost_currency}".strip()
        parts = [
            option.get("type"),
            option.get("shippingServiceCode"),
            cost or None,
            option.get("shippingCostType"),
        ]
        summaries.append(" / ".join(str(part) for part in parts if part))
    return " | ".join(summaries)


def category_summary(item: dict[str, Any]) -> str:
    if item.get("categoryPath"):
        return str(item["categoryPath"])
    categories = item.get("categories") or []
    if categories:
        return " > ".join(str(category.get("categoryName", "")) for category in categories if category)
    return ""


def one_line(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def build_research_text(items: list[dict[str, Any]], query: str) -> str:
    lines = [
        "EBAY_PRODUCT_RESEARCH_START",
        f"QUERY: {one_line(query)}",
        f"MARKETPLACE: {marketplace_id()}",
        f"RESULT_COUNT: {len(items)}",
        f"GENERATED_AT_UTC: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]

    for index, item in enumerate(items, start=1):
        price, currency = best_price(item)
        seller = seller_info(item)
        lines.extend(
            [
                f"[{index}]",
                f"ItemID: {one_line(item.get('itemId') or item.get('legacyItemId'))}",
                f"Title: {one_line(item.get('title'))}",
                f"Price: {price}",
                f"Currency: {currency}",
                f"Condition: {one_line(item.get('condition'))}",
                f"BuyingOptions: {', '.join(item.get('buyingOptions') or [])}",
                f"ItemURL: {one_line(item.get('itemWebUrl') or item.get('itemAffiliateWebUrl'))}",
                f"ImageURL: {one_line(first_image_url(item))}",
                f"SellerUsername: {one_line(seller['username'])}",
                f"SellerFeedbackScore: {one_line(seller['feedback_score'])}",
                f"SellerFeedbackPercentage: {one_line(seller['feedback_percentage'])}",
                f"ItemLocation: {one_line(location_summary(item))}",
                f"ShippingSummary: {one_line(shipping_summary(item))}",
                f"Category: {one_line(category_summary(item))}",
                f"ListingMarketplaceId: {one_line(item.get('listingMarketplaceId'))}",
                "",
            ]
        )

    lines.append("EBAY_PRODUCT_RESEARCH_END")
    return "\n".join(lines)


async def health(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": SERVICE_NAME,
            "message": "MCP server is running",
            "mcp_endpoint": MCP_PATH,
            "available_tools": ["search", "fetch"],
            "primary_use_case": "ebay_product_research",
            "marketplace": marketplace_id(),
            "configured": configured_status(),
        }
    )


mcp = FastMCP(
    name=SERVICE_NAME,
    instructions=(
        "Read-only eBay product research MCP server. Use search first to find eBay "
        "items, then fetch one returned id to retrieve research-ready product details."
    ),
    host="0.0.0.0",
    port=int(env("PORT", "8000") or "8000"),
    streamable_http_path=MCP_PATH,
    stateless_http=True,
    transport_security=build_transport_security_settings(),
)


@mcp.tool(
    name="search",
    title="Search eBay items",
    description=(
        "Use this when the user wants to search eBay listings for product research. "
        "Input is one natural-language query string. Returns JSON text containing "
        "results with id, title, and canonical eBay url."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
    structured_output=False,
)
def search(query: str) -> list[TextContent]:
    payload = browse_search(query)
    results = []
    for item in payload.get("itemSummaries", []) or []:
        item_id = item.get("itemId")
        title = item.get("title")
        url = item.get("itemWebUrl") or item.get("itemAffiliateWebUrl")
        if item_id and title and url:
            results.append(
                {
                    "id": prefixed_item_id(str(item_id)),
                    "title": str(title),
                    "url": str(url),
                }
            )
    return mcp_json({"results": results})


@mcp.tool(
    name="fetch",
    title="Fetch eBay item research details",
    description=(
        "Use this when the user has an id returned by search and needs detailed eBay "
        "product research data. Returns JSON text with id, title, text, url, and metadata."
    ),
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
    structured_output=False,
)
def fetch(id: str) -> list[TextContent]:
    item_id = strip_item_prefix(id)
    item = browse_get_item(item_id)
    title = str(item.get("title") or item_id)
    url = str(item.get("itemWebUrl") or item.get("itemAffiliateWebUrl") or "")
    text = build_research_text([item], query=f"fetch:{item_id}")
    return mcp_json(
        {
            "id": prefixed_item_id(item_id),
            "title": title,
            "text": text,
            "url": url,
            "metadata": {
                "source": "eBay Browse API",
                "marketplace": marketplace_id(),
                "item_id": item_id,
            },
        }
    )


mcp_asgi_app = mcp.streamable_http_app()
app = Starlette(
    routes=[
        Route("/", health, methods=["GET"]),
        Mount("/", app=mcp_asgi_app),
    ],
    middleware=[
        Middleware(
            BearerAuthMiddleware,
            api_key=env("MCP_API_KEY"),
            protected_path=MCP_PATH,
        )
    ],
    lifespan=mcp_asgi_app.router.lifespan_context,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(env("PORT", "8000") or "8000"))
