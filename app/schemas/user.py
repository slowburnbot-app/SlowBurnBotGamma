import uuid
from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    display_name: str | None = None
    plan_tier: str = "free"


class UserCreate(schemas.BaseUserCreate):
    display_name: str | None = None
    invite_code: str | None = None

    # invite_code exists only to carry the code from the request body to
    # UserManager.create()'s own validation — it isn't a User column, so it
    # must never reach fastapi-users' create_update_dict(_superuser)(), which
    # otherwise dumps it straight into User(**create_dict) and blows up with
    # "'invite_code' is an invalid keyword argument for User".
    def create_update_dict(self) -> dict:
        return {k: v for k, v in super().create_update_dict().items() if k != "invite_code"}

    def create_update_dict_superuser(self) -> dict:
        return {
            k: v for k, v in super().create_update_dict_superuser().items() if k != "invite_code"
        }


class UserUpdate(schemas.BaseUserUpdate):
    display_name: str | None = None
