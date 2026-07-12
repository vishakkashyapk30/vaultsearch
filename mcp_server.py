"""MCP server: permission-aware VaultSearch tools for external agents.

Any MCP-capable agent (Claude Desktop, Cursor, a LangGraph app, ...) can use
VaultSearch as a retrieval tool *as a specific identity*. The identity is
bound when the server process starts (VAULTSEARCH_USER), never passed as a
tool argument, so the calling model has no way to escalate: it can phrase
queries however it likes, but every request is executed by the VaultSearch
API under the pinned user's ACLs, with the same pre-filter, re-verification,
and citation sanitization as the web app.

Run (stdio transport, the default for MCP clients):

    VAULTSEARCH_USER=user:asha python mcp_server.py

Requires the VaultSearch API to be running (default http://127.0.0.1:8000,
override with VAULTSEARCH_URL).

Example Cursor / Claude Desktop config:

    {
      "mcpServers": {
        "vaultsearch": {
          "command": "/path/to/.venv/bin/python",
          "args": ["/path/to/vaultsearch/mcp_server.py"],
          "env": {"VAULTSEARCH_USER": "user:asha"}
        }
      }
    }
"""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.getenv("VAULTSEARCH_URL", "http://127.0.0.1:8000").rstrip("/")
USER_ID = os.getenv("VAULTSEARCH_USER", "user:ines")

mcp = FastMCP(
    "vaultsearch",
    instructions=(
        "Permission-aware enterprise search. All tools run as one fixed "
        f"identity ({USER_ID}); results only ever contain content that "
        "identity is authorized to read. There is no way to query as "
        "someone else."
    ),
)


def _post(path: str, payload: dict) -> dict:
    with httpx.Client(timeout=180.0) as client:
        response = client.post(f"{BASE_URL}{path}", json=payload)
        response.raise_for_status()
        return response.json()


def _get(path: str) -> dict:
    with httpx.Client(timeout=30.0) as client:
        response = client.get(f"{BASE_URL}{path}")
        response.raise_for_status()
        return response.json()


@mcp.tool()
def ask(question: str) -> dict:
    """Ask a question over the company corpus and get a cited, permission-safe
    answer. Returns the answer text, the citations that survived sanitization,
    and the titles of the evidence documents used."""
    data = _post("/api/ask", {"user_id": USER_ID, "question": question})
    return {
        "answer": data["answer"],
        "citations": data["citations"],
        "evidence": [
            {"doc_id": item["doc_id"], "source": item["source"], "title": item["title"]}
            for item in data["evidence"]
        ],
    }


@mcp.tool()
def search(query: str, top_n: int = 6) -> dict:
    """Retrieve the most relevant permitted document chunks for a query
    (hybrid BM25 + vector + rerank). Returns raw evidence without LLM
    synthesis; useful when the caller wants to reason over sources itself."""
    data = _post(
        "/api/search",
        {"user_id": USER_ID, "query": query, "top_n": max(1, min(top_n, 20))},
    )
    results = data["modes"]["hybrid+rerank"]["results"]
    return {
        "results": results,
        "searched_chunks": data["visible_chunks"],
        "total_chunks": data["total_chunks"],
    }


@mcp.tool()
def lookup_person(name: str) -> list[dict]:
    """Look up a person in the company directory by (partial) name. Returns
    org-public metadata: user id, display name, and group membership."""
    needle = name.strip().lower()
    directory = _get("/api/users")["users"]
    return [
        {"user_id": user["user_id"], "name": user["name"], "groups": user["groups"]}
        for user in directory
        if needle in user["name"].lower() or needle in user["user_id"].lower()
    ][:5]


@mcp.tool()
def whoami() -> dict:
    """Report the identity this server is bound to and how much of the corpus
    it can access."""
    data = _get("/api/users")
    for user in data["users"]:
        if user["user_id"] == USER_ID:
            return {
                "user_id": USER_ID,
                "name": user["name"],
                "groups": user["groups"],
                "visible_chunks": user["visible_chunks"],
                "total_chunks": data["total_chunks"],
            }
    return {"user_id": USER_ID, "error": "unknown user: no principals, sees nothing"}


if __name__ == "__main__":
    mcp.run()
