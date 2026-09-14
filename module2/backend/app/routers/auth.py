from fastapi import APIRouter, Request

from ..auth import StoreDep, authenticate, issue_token
from ..models import LoginRequest, LoginResult
from ..serializers import session_user

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=LoginResult, operation_id="login")
def login(body: LoginRequest, request: Request, store: StoreDep) -> LoginResult:
    settings = request.app.state.settings
    user = authenticate(store, body.username, body.password, scrypt_n=settings.scrypt_n)
    token = issue_token(store, user, settings.token_ttl_minutes)
    return LoginResult(token=token, user=session_user(user))
