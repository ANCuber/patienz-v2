"""Who may touch what. Pure policy: no Streamlit, no database.

Roles
-----
* ``root``        – exactly one. Belongs to no group, may do everything.
* ``group_admin`` – at most one per group. Manages the ordinary users and all
  data of its own group, nothing outside it.
* ``user``        – manages only its own data.

An *actor* / *target* is a user dict with at least ``id``, ``role`` and
``group_id`` (as returned by ``db_store.get_user_by_id``). Every function
returns False for a missing actor, so callers can pass ``None`` when nobody is
logged in. ``group_id`` ``None`` denotes the root admin's private data store.
"""
ROLE_ROOT = "root"
ROLE_GROUP_ADMIN = "group_admin"
ROLE_USER = "user"

ROLE_LABELS = {
    ROLE_ROOT: "Root 管理員",
    ROLE_GROUP_ADMIN: "群組管理員",
    ROLE_USER: "一般使用者",
}


def is_root(actor):
    return bool(actor) and actor.get("role") == ROLE_ROOT


def is_group_admin(actor):
    return bool(actor) and actor.get("role") == ROLE_GROUP_ADMIN and actor.get("group_id") is not None


def can_administer(actor):
    """May open the admin page at all."""
    return is_root(actor) or is_group_admin(actor)


def can_manage_groups(actor):
    """Create, rename and delete groups; appoint group admins; move users."""
    return is_root(actor)


def can_access_group(actor, group_id):
    """Read and write *every* user's data in a group's database."""
    if is_root(actor):
        return True
    return is_group_admin(actor) and group_id is not None and actor["group_id"] == group_id


def can_manage_user(actor, target):
    """Edit or delete another account."""
    if not actor or not target:
        return False
    if is_root(actor):
        return True
    return (
        is_group_admin(actor)
        and target.get("role") == ROLE_USER
        and target.get("group_id") == actor["group_id"]
    )


def can_access_user_data(actor, owner_id, owner_group_id):
    """Read and write the data owned by one user living in ``owner_group_id``."""
    if not actor:
        return False
    if actor.get("id") == owner_id:
        return True
    return can_access_group(actor, owner_group_id)


def assignable_roles(actor):
    """Roles the actor may give to an account it creates or edits."""
    if is_root(actor):
        return [ROLE_USER, ROLE_GROUP_ADMIN]
    if is_group_admin(actor):
        return [ROLE_USER]
    return []
