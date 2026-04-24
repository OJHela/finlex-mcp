"""
server.py – Finlex MCP -palvelin (FastMCP, SSE-transport).

Käyttö:
    python3 server.py                  → käynnistää palvelimen portissa 8000
    uvicorn server:app --port 8000     → tuotanto-ajotapa

Ympäristömuuttujat (.env):
    MCP_SERVER_JWT_SECRET    – HMAC-allekirjoitusavain (pakollinen tuotannossa)
    MCP_SERVER_JWT_ISSUER    – JWT-myöntäjä (valinnainen)
    MCP_SERVER_JWT_AUDIENCE  – JWT-kohdeyleisö (valinnainen)
"""

import os

from dotenv import load_dotenv
from fastmcp import FastMCP
from mcp.server.fastmcp import Icon
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

load_dotenv()

from tools_finlex import (
    get_decision,
    get_outline,
    get_proposal,
    get_statute,
    search_decisions,
    search_statutes,
)

####### CUSTOM MIDDLEWARE #######

class IPAllowlistMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_ips: list[str]):
        super().__init__(app)
        self.allowed_ips = set(allowed_ips)
        self.allow_all = "*" in self.allowed_ips

    async def dispatch(self, request, call_next):
        if self.allow_all:
            return await call_next(request)
        client_ip = request.client.host if request.client else None
        if client_ip not in self.allowed_ips:
            return JSONResponse(
                status_code=403,
                content={"error": "Forbidden", "your_ip": client_ip},
            )
        return await call_next(request)


ALLOWED_IPS = os.getenv("MCP_ALLOWED_IPS", "*").split(",")
middleware = [Middleware(IPAllowlistMiddleware, allowed_ips=ALLOWED_IPS)]

####### SERVER METADATA #######

icon = Icon(
    src="https://raw.githubusercontent.com/SimonBerg255/finlex-mcp/main/icon.png",
)

INSTRUCTION_STRING = """Finlex – Finnish legal database (opendata.finlex.fi, CC BY 4.0).

TOOLS:
  search_statutes(start_year, end_year) → list statute citations by year
  get_outline("number/year")            → chapter/section index of a statute (no body text)
  get_statute("number/year")            → statute text, paginated if long
  search_decisions(court)               → list decisions (court: "okv" or "dpo")
  get_decision(year, number, court)     → single decision text
  get_proposal(year, number)            → government proposal (HE) text

NAVIGATION PATTERN FOR LONG STATUTES:
  1. get_outline("citation")            → see all section numbers and headings
  2. get_statute("citation", section="N") → fetch only that section

RULES:
  • Statute citation: "number/year" e.g. "55/2001"
  • Search terms must be Finnish or Swedish — English returns no results
  • KKO and KHO rulings are NOT available; only "okv" (Chancellor) and "dpo" (Data Protection)
  • Long documents: response shows [PART 1/N | NEXT: call...] — use the exact call shown

COMMON STATUTE CITATIONS (Finnish name → citation):
  Perustuslaki                  731/1999
  Rikoslaki                     39/1889
  Työsopimuslaki                55/2001
  Työttömyysturvalaki           1290/2002
  Lastensuojelulaki             417/2007
  Tietosuojalaki                1050/2018
  Yhdenvertaisuuslaki           1325/2014
  Laki potilaan asemasta        785/1992
  Hallintolaki                  434/2003
  Oikeudenkäymiskaari           4/1734"""

VERSION = "1.0.0"
WEBSITE_URL = "https://opendata.finlex.fi/"

####### SERVER CONFIGURATION #######

mcp = FastMCP(
    name="Finlex",
    instructions=INSTRUCTION_STRING,
    version=VERSION,
    website_url=WEBSITE_URL,
    icons=[icon],
)

####### TOOLS #######

# All tools run automatically without user confirmation
mcp.tool(meta={"requires_permission": False})(search_statutes)
mcp.tool(meta={"requires_permission": False})(get_outline)
mcp.tool(meta={"requires_permission": False})(get_statute)
mcp.tool(meta={"requires_permission": False})(search_decisions)
mcp.tool(meta={"requires_permission": False})(get_decision)
mcp.tool(meta={"requires_permission": False})(get_proposal)

####### CUSTOM ROUTES #######

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")


@mcp.custom_route("/icon.png", methods=["GET"])
async def serve_icon(request: Request):
    from pathlib import Path
    from starlette.responses import Response
    img = Path(__file__).parent / "icon.png"
    return Response(content=img.read_bytes(), media_type="image/png")

####### RUNNING THE SERVER #######
# Run with: uvicorn server:app --host 0.0.0.0 --port 8000
app = mcp.http_app(middleware=middleware)

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("MCP_PORT", "8000"))
    host = os.getenv("MCP_HOST", "0.0.0.0")
    print(f"Käynnistetään Finlex MCP -palvelin osoitteessa {host}:{port}/mcp")
    uvicorn.run(app, host=host, port=port)
