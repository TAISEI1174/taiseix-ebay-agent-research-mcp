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


SERVICE_NAME = "ebay-agent-actions-mcp"
MCP_PATH = "/mcp"
EBAY_BROWSE_BASE_URL = "https://api.ebay.com/buy/browse/v1"
EBAY_OAUTH_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
DEFAULT_MARKETPLACE_ID = "EBAY_US"
DEFAULT_SEARCH_LIMIT = 10
EBAY_SEARCH_API_LIMIT = 50
REQUEST_TIMEOUT_SECONDS = 20
MIN_SELLER_FEEDBACK_SCORE = 50
MIN_SELLER_FEEDBACK_PERCENTAGE = 95.0
TRADING_CARD_CATEGORY_KEYWORDS = (
    "ccg individual cards",
    "trading card",
    "collectible card game",
    "sports trading cards",
)

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


def browse_search(query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> dict[str, Any]:
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("query must not be empty")

    api_limit = max(1, min(int(limit), EBAY_SEARCH_API_LIMIT))
    response = requests.get(
        f"{EBAY_BROWSE_BASE_URL}/item_summary/search",
        headers=ebay_headers(),
        params={
            "q": cleaned_query,
            "limit": api_limit,
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


def extract_item_ids(raw_id: str) -> list[str]:
    raw = raw_id.strip()
    if not raw:
        return []

    parsed_ids: list[Any] | None = None
    if raw.startswith("[") or raw.startswith("{"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            parsed_ids = parsed
        elif isinstance(parsed, dict):
            value = parsed.get("ids") or parsed.get("id") or parsed.get("item_ids")
            if isinstance(value, list):
                parsed_ids = value
            elif isinstance(value, str):
                parsed_ids = [value]

    if parsed_ids is None:
        parsed_ids = raw.replace("\n", ",").replace(";", ",").split(",")

    cleaned_ids: list[str] = []
    seen: set[str] = set()
    for item_id in parsed_ids:
        cleaned = strip_item_prefix(str(item_id).strip().strip('"').strip("'"))
        if cleaned and cleaned not in seen:
            cleaned_ids.append(cleaned)
            seen.add(cleaned)
    return cleaned_ids


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


def parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except ValueError:
        return None


def parse_percentage(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except ValueError:
        return None


def seller_filter_exclusion_reason(item: dict[str, Any]) -> str | None:
    seller = seller_info(item)
    score = parse_int(seller["feedback_score"])
    percentage = parse_percentage(seller["feedback_percentage"])
    reasons: list[str] = []
    if score is not None and score < MIN_SELLER_FEEDBACK_SCORE:
        reasons.append(f"seller feedback score {score} < {MIN_SELLER_FEEDBACK_SCORE}")
    if percentage is not None and percentage < MIN_SELLER_FEEDBACK_PERCENTAGE:
        reasons.append(f"seller feedback percentage {percentage:g}% < {MIN_SELLER_FEEDBACK_PERCENTAGE:g}%")
    return "; ".join(reasons) if reasons else None


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


def is_trading_card_category(item: dict[str, Any]) -> bool:
    category = category_summary(item).lower()
    return any(keyword in category for keyword in TRADING_CARD_CATEGORY_KEYWORDS)


def location_country(item: dict[str, Any]) -> str:
    location = item.get("itemLocation") or {}
    return one_line(location.get("country") or location.get("countryName"))


def is_china_location(item: dict[str, Any]) -> bool:
    location = item.get("itemLocation") or {}
    values = [
        location.get("country"),
        location.get("countryName"),
        location.get("city"),
        location.get("stateOrProvince"),
        location_summary(item),
    ]
    normalized = " ".join(str(value).lower() for value in values if value)
    country = str(location.get("country") or "").upper()
    return "china" in normalized or country == "CN"


def risk_rank(level: str) -> int:
    return {"LOW": 0, "MEDIUM": 1, "HIGH": 2}[level]


def evaluate_research_risk(item: dict[str, Any]) -> dict[str, str]:
    seller = seller_info(item)
    score = parse_int(seller["feedback_score"])
    percentage = parse_percentage(seller["feedback_percentage"])

    seller_reasons: list[str] = []
    if score is None:
        seller_reasons.append("seller feedback score missing")
    elif score < MIN_SELLER_FEEDBACK_SCORE:
        seller_reasons.append(f"seller feedback score {score} below {MIN_SELLER_FEEDBACK_SCORE}")

    if percentage is None:
        seller_reasons.append("seller feedback percentage missing")
    elif percentage < MIN_SELLER_FEEDBACK_PERCENTAGE:
        seller_reasons.append(f"seller feedback percentage {percentage:g}% below {MIN_SELLER_FEEDBACK_PERCENTAGE:g}%")

    if any("below" in reason for reason in seller_reasons):
        seller_risk = "HIGH"
    elif seller_reasons:
        seller_risk = "MEDIUM"
    else:
        seller_risk = "LOW"
        seller_reasons.append(
            f"feedback score {score} and percentage {percentage:g}% meet threshold"
        )

    category = category_summary(item)
    if not category:
        category_risk = "MEDIUM"
        category_reason = "category information missing"
    elif is_trading_card_category(item):
        category_risk = "LOW"
        category_reason = "category matches CCG / Trading Card"
    else:
        category_risk = "HIGH"
        category_reason = f"category does not look like CCG / Trading Card: {category}"

    country = location_country(item)
    if is_china_location(item):
        location_risk = "HIGH"
        location_reason = "item location is CN/China"
    elif not country:
        location_risk = "MEDIUM"
        location_reason = "item location country missing"
    elif country.upper() == "US":
        location_risk = "LOW"
        location_reason = "item location is US"
    else:
        location_risk = "MEDIUM"
        location_reason = f"item location is outside US: {country}"

    risk_levels = [seller_risk, category_risk, location_risk]
    research_risk = max(risk_levels, key=risk_rank)
    reason = "; ".join(["; ".join(seller_reasons), category_reason, location_reason])

    return {
        "seller_risk": seller_risk,
        "category_risk": category_risk,
        "location_risk": location_risk,
        "research_risk": research_risk,
        "reason": reason,
    }


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
        risk = evaluate_research_risk(item)
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
                f"SellerRisk: {risk['seller_risk']}",
                f"CategoryRisk: {risk['category_risk']}",
                f"LocationRisk: {risk['location_risk']}",
                f"ResearchRisk: {risk['research_risk']}",
                f"Reason: {one_line(risk['reason'])}",
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
            "mcp_endpoint": MCP_PATH,
            "available_tools": [
                "search",
                "fetch",
                "ebay_product_research",
                "fetch_ebay_item",
            ],
            "primary_use_case": "agent_actions_for_ebay_product_research",
            "marketplace": marketplace_id(),
        }
    )


mcp = FastMCP(
    name=SERVICE_NAME,
    instructions=(
        "Read-only eBay product research MCP server. Use search/fetch for Custom "
        "MCP connector flows and ebay_product_research/fetch_ebay_item for Agent actions."
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
    description="Search eBay listings for connector fetch.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
    structured_output=False,
)
def search(query: str) -> list[TextContent]:
    payload = browse_search(query, EBAY_SEARCH_API_LIMIT)
    results = []
    excluded = []
    for item in payload.get("itemSummaries", []) or []:
        exclusion_reason = seller_filter_exclusion_reason(item)
        if exclusion_reason:
            excluded.append(
                {
                    "id": prefixed_item_id(str(item.get("itemId", ""))),
                    "title": str(item.get("title", "")),
                    "reason": exclusion_reason,
                }
            )
            continue

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
            if len(results) >= DEFAULT_SEARCH_LIMIT:
                break

    return mcp_json(
        {
            "results": results,
            "metadata": {
                "result_count": len(results),
                "result_count_basis": "after seller feedback filtering",
                "excluded_count": len(excluded),
            },
        }
    )


@mcp.tool(
    name="fetch",
    title="Fetch eBay item research details",
    description="Fetch connector item details by search result id.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
    structured_output=False,
)
def fetch(id: str) -> list[TextContent]:
    item_ids = extract_item_ids(id)
    if not item_ids:
        raise ValueError("id must include at least one eBay item id")

    items: list[dict[str, Any]] = []
    fetched_item_ids: list[str] = []
    failed_items: list[dict[str, str]] = []
    for item_id in item_ids:
        try:
            items.append(browse_get_item(item_id))
            fetched_item_ids.append(item_id)
        except Exception as exc:
            failed_items.append({"id": prefixed_item_id(item_id), "error": str(exc)})

    if not items:
        raise EbayApiError(f"No eBay items could be fetched: {failed_items}")

    first_item = items[0]
    first_item_id = str(first_item.get("itemId") or fetched_item_ids[0])
    is_batch = len(items) > 1
    title = (
        f"eBay product research batch ({len(items)} items)"
        if is_batch
        else str(first_item.get("title") or first_item_id)
    )
    url = str(first_item.get("itemWebUrl") or first_item.get("itemAffiliateWebUrl") or "")
    text = build_research_text(items, query=f"fetch:{', '.join(fetched_item_ids)}")
    return mcp_json(
        {
            "id": f"ebay_fetch_batch|{len(items)}" if is_batch else prefixed_item_id(first_item_id),
            "title": title,
            "text": text,
            "url": url,
            "metadata": {
                "source": "eBay Browse API",
                "marketplace": marketplace_id(),
                "item_ids": [
                    prefixed_item_id(str(item.get("itemId") or fallback_item_id))
                    for item, fallback_item_id in zip(items, fetched_item_ids)
                ],
                "result_count": len(items),
                "failed_items": failed_items,
            },
        }
    )


@mcp.tool(
    name="ebay_product_research",
    title="eBay product research",
    description="Run eBay product research by query.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
    structured_output=False,
)
def ebay_product_research(query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> str:
    requested_limit = max(1, min(int(limit), EBAY_SEARCH_API_LIMIT))
    logger.info(
        "=== TOOL CALLED: ebay_product_research ===\nquery: %s\nlimit: %s",
        one_line(query),
        requested_limit,
    )
    payload = browse_search(query, requested_limit)
    items = list(payload.get("itemSummaries", []) or [])[:requested_limit]
    return build_research_text(items, query=query)


@mcp.tool(
    name="fetch_ebay_item",
    title="Fetch eBay item",
    description="Fetch one eBay item by item id.",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
    structured_output=False,
)
def fetch_ebay_item(item_id: str) -> str:
    logger.info(
        "=== TOOL CALLED: fetch_ebay_item ===\nitem_id: %s",
        one_line(item_id),
    )
    cleaned_item_id = strip_item_prefix(item_id.strip())
    if not cleaned_item_id:
        raise ValueError("item_id must not be empty")
    item = browse_get_item(cleaned_item_id)
    return build_research_text([item], query=f"fetch_ebay_item:{cleaned_item_id}")


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
