from __future__ import annotations

from typing import Final

POST_ACTION_SCHEMA: Final = "post_action"
POST_ACTION_RETURN_FIELDS: Final = ("verified", "page_changed")
POST_ACTION_RETURN_JSON: Final = '{"verified": boolean, "page_changed": boolean}'


def return_fields() -> tuple[str, ...]:
    return tuple(POST_ACTION_RETURN_FIELDS)


def return_json() -> str:
    return POST_ACTION_RETURN_JSON
