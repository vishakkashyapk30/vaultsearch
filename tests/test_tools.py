from app.acl import IdentityStore, can_access
from app.retrieval_core import RetrievalResult, RetrievedChunk
from app.tools import Toolbox, validate_tool_call


class FakeRetriever:
    """Minimal permission-respecting retriever over in-memory chunk dicts."""

    def __init__(self, chunks: list[dict], identity: IdentityStore):
        self.chunks = chunks
        self.identity = identity

    def search(self, user_id: str, query: str, top_n: int = 6, **_) -> RetrievalResult:
        principals = self.identity.expand_principals(user_id)
        allowed = [
            chunk
            for chunk in self.chunks
            if can_access(principals, chunk["allowed_principals"])
        ]
        matched = [
            RetrievedChunk(
                chunk_id=chunk["chunk_id"],
                doc_id=chunk["doc_id"],
                source=chunk["source"],
                title=chunk["title"],
                text=chunk["text"],
                allowed_principals=chunk["allowed_principals"],
                score=1.0,
            )
            for chunk in allowed
            if any(term in chunk["text"].lower() for term in query.lower().split())
        ]
        return RetrievalResult(
            chunks=matched[:top_n],
            candidates_allowed=len(allowed),
            candidates_total=len(self.chunks),
        )


def make_identity() -> IdentityStore:
    return IdentityStore(
        {
            "user:eng": ["group:all-staff", "group:engineering"],
            "user:fin": ["group:all-staff", "group:finance"],
        },
        names={"user:eng": "Eve Engineer", "user:fin": "Fred Finance"},
    )


def make_chunks() -> list[dict]:
    return [
        {
            "chunk_id": "drive-1#c0",
            "doc_id": "drive-1",
            "source": "drive",
            "title": "Eng doc",
            "text": "atlas migration rollback plan",
            "allowed_principals": ["group:engineering"],
        },
        {
            "chunk_id": "drive-2#c0",
            "doc_id": "drive-2",
            "source": "drive",
            "title": "Budget",
            "text": "budget is 1.2 million",
            "allowed_principals": ["group:finance"],
        },
        {
            "chunk_id": "slack-1#c0",
            "doc_id": "slack-1",
            "source": "slack",
            "title": "PTO",
            "text": "25 pto days for everyone",
            "allowed_principals": ["group:all-staff"],
        },
    ]


def make_toolbox(user_id: str) -> Toolbox:
    identity = make_identity()
    return Toolbox(user_id, FakeRetriever(make_chunks(), identity), identity)


def test_validate_rejects_unknown_tool_and_malformed_args() -> None:
    assert validate_tool_call({"tool": "delete_index", "args": {}}) is None
    assert validate_tool_call({"tool": "search", "args": {"query": "  "}}) is None
    assert validate_tool_call({"tool": "search", "args": {"query": 42}}) is None
    assert validate_tool_call("search: budget") is None
    assert validate_tool_call({"tool": "lookup_person", "args": {}}) is None


def test_validate_accepts_and_normalizes_search() -> None:
    validated = validate_tool_call({"tool": "search", "args": {"query": " budget "}})
    assert validated == ("search", {"query": "budget"})


def test_tools_have_no_user_id_parameter() -> None:
    """The identity is bound at construction; no tool argument can change it."""
    validated = validate_tool_call(
        {"tool": "search", "args": {"query": "budget", "user_id": "user:fin"}}
    )
    assert validated == ("search", {"query": "budget"})  # user_id silently dropped


def test_search_tool_enforces_caller_acl() -> None:
    toolbox = make_toolbox("user:eng")
    execution = toolbox.execute("search", {"query": "budget million"})
    assert execution.error is None
    assert all("1.2 million" not in chunk.text for chunk in execution.chunks)


def test_search_tool_returns_permitted_matches() -> None:
    toolbox = make_toolbox("user:fin")
    execution = toolbox.execute("search", {"query": "budget million"})
    assert any("1.2 million" in chunk.text for chunk in execution.chunks)


def test_lookup_person_returns_org_public_metadata_only() -> None:
    toolbox = make_toolbox("user:eng")
    execution = toolbox.execute("lookup_person", {"name": "fred"})
    matches = execution.payload["matches"]
    assert len(matches) == 1
    assert matches[0]["user_id"] == "user:fin"
    assert set(matches[0]) == {"user_id", "name", "groups"}


def test_list_my_sources_counts_only_visible_chunks() -> None:
    toolbox = make_toolbox("user:eng")
    execution = toolbox.execute("list_my_sources", {})
    assert execution.payload["visible_chunks"] == 2  # eng doc + all-staff
    assert execution.payload["sources"] == {"drive": 1, "slack": 1}


def test_toolbox_records_every_call() -> None:
    toolbox = make_toolbox("user:eng")
    toolbox.execute("search", {"query": "atlas"})
    toolbox.execute("list_my_sources", {})
    assert [execution.tool for execution in toolbox.calls] == [
        "search",
        "list_my_sources",
    ]
