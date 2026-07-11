"""Identity model and ACL enforcement.

Security invariants:
- Deny-by-default: a document with an empty ACL is visible only to admins.
- ACL checks happen as a pre-filter before ranking, and are re-verified
  before synthesis (defense in depth).
"""

from __future__ import annotations

import json
from pathlib import Path

ADMIN_GROUP = "group:admin"


class IdentityStore:
    def __init__(self, users: dict[str, list[str]]):
        """users maps user_id -> list of group ids (with "group:" prefix)."""
        self._users = users

    @classmethod
    def load(cls, path: str | Path) -> "IdentityStore":
        data = json.loads(Path(path).read_text())
        return cls({u["user_id"]: u["groups"] for u in data["users"]})

    def known_user(self, user_id: str) -> bool:
        return user_id in self._users

    def expand_principals(self, user_id: str) -> set[str]:
        """Return the user's ID and group principals."""
        if user_id not in self._users:
            return set()
        return {user_id, *self._users[user_id]}

    def is_admin(self, user_id: str) -> bool:
        return ADMIN_GROUP in self._users.get(user_id, [])

    def all_users(self) -> list[str]:
        return sorted(self._users)


def can_access(principals: set[str], allowed_principals: list[str]) -> bool:
    """Return whether the requesting principals intersect the resource ACL."""
    if ADMIN_GROUP in principals:
        return True
    if not allowed_principals:
        return False
    return bool(principals.intersection(allowed_principals))
