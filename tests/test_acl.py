from app.acl import ADMIN_GROUP, IdentityStore, can_access


def store() -> IdentityStore:
    return IdentityStore(
        {
            "user:eng": ["group:all-staff", "group:engineering"],
            "user:finance": ["group:all-staff", "group:finance"],
            "user:admin": [ADMIN_GROUP],
            "user:nogroups": [],
        }
    )


def test_expands_user_and_groups() -> None:
    assert store().expand_principals("user:eng") == {
        "user:eng",
        "group:all-staff",
        "group:engineering",
    }


def test_unknown_user_has_no_principals() -> None:
    assert store().expand_principals("user:missing") == set()


def test_access_requires_any_acl_intersection() -> None:
    principals = store().expand_principals("user:eng")
    assert can_access(principals, ["group:engineering"])
    assert can_access(principals, ["user:eng"])
    assert not can_access(principals, ["group:finance"])


def test_empty_acl_denied_by_default() -> None:
    assert not can_access(store().expand_principals("user:eng"), [])
    assert not can_access(store().expand_principals("user:nogroups"), [])


def test_admin_can_access_everything_including_empty_acl() -> None:
    principals = store().expand_principals("user:admin")
    assert can_access(principals, [])
    assert can_access(principals, ["group:finance"])
