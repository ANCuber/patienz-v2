"""Permission-checked access to the per-group databases for the admin page.

``util/save_load.py`` serves a user's *own* data during a session. This module
is the only other way into ``db_store``'s data tables: every call names the
group it wants and is refused unless ``util/access.py`` lets the logged-in
user into that group (root: any group; group admin: its own; users: none).
``group_id`` ``None`` is the root admin's private store.
"""
import util.access as access
import util.auth as auth
import util.db_store as db_store


class PermissionDenied(Exception):
    pass


def _require_group(group_id):
    if not access.can_access_group(auth.current_actor(), group_id):
        raise PermissionDenied("權限不足")


def accessible_scopes():
    """``[(group_id, label)]`` of the data stores the current user may browse."""
    actor = auth.current_actor()
    scopes = [(g["id"], g["name"]) for g in auth.list_groups()]
    if access.is_root(actor):
        scopes.append((None, "Root 管理員個人資料"))
    return scopes


def _with_usernames(rows):
    # Only names the caller may already see; rows of anyone else (e.g. a
    # deleted account) are shown by id.
    names = {u["id"]: u["username"] for u in auth.list_users()}
    for row in rows:
        row["username"] = names.get(row["user_id"], f"(已刪除 #{row['user_id']})")
    return rows


def list_progress_saves(group_id):
    _require_group(group_id)
    return _with_usernames(db_store.list_progress_saves(group_id))


def list_grading_results(group_id):
    _require_group(group_id)
    return _with_usernames(db_store.list_grading_results(group_id))


def get_progress_payload(group_id, user_id, save_name):
    _require_group(group_id)
    return db_store.get_progress_payload(group_id, user_id, save_name)


def get_grading_payload(group_id, user_id, record_name):
    _require_group(group_id)
    return db_store.get_grading_payload(group_id, user_id, record_name)


def delete_progress_save(group_id, user_id, save_name):
    _require_group(group_id)
    return db_store.delete_progress_save(group_id, user_id, save_name)


def delete_grading_result(group_id, user_id, record_name):
    _require_group(group_id)
    return db_store.delete_grading_result(group_id, user_id, record_name)


def count_log_rows(group_id):
    _require_group(group_id)
    return db_store.count_log_rows(group_id)
