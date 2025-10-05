"""FastAPI application exposing monitoring and control endpoints."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .config import Config, WebSettings, load_config
from .db import get_connection
from .security import AuthManager
from .services.dashboard import gather_dashboard_snapshot
from .services.reporting import ReportingService
from .services.repayment import RepaymentService
from .services.reserve import ReserveManager
from .services.pricing import PriceService


class TokenRequest(BaseModel):
    username: str
    totp: str


class TokenResponse(BaseModel):
    token: str
    role: str


class RepayRequest(BaseModel):
    amount: float
    dry_run: bool = False
    profile: Optional[str] = None


class TransferRequest(BaseModel):
    asset: str
    amount: float
    direction: str
    profile: Optional[str] = None


def _enforce_direction(value: str) -> bool:
    return value in {"to_collateral", "to_reserve"}


def create_app(
    config: Config | None = None,
    *,
    db_path: Path | None = None,
    price_service: PriceService | None = None,
) -> FastAPI:
    """Build the FastAPI application used by the web dashboard."""

    cfg = config or load_config()
    price_service = price_service or PriceService()
    settings: WebSettings = cfg.web
    auth_manager = AuthManager(cfg.security)
    bearer = HTTPBearer(auto_error=False)

    app = FastAPI(title="Loan Monitor API", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    def get_conn():
        conn = get_connection(db_path, check_same_thread=False)
        try:
            yield conn
        finally:
            conn.close()

    def require_token(required_role: str = "viewer"):
        async def dependency(
            credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        ) -> dict:
            if credentials is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing bearer token",
                )
            token = credentials.credentials
            try:
                payload = auth_manager.verify_token(token, required_role=required_role)
            except ValueError as exc:  # pragma: no cover - defensive guard
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token",
                ) from exc
            except PermissionError as exc:  # pragma: no cover - defensive guard
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Insufficient role",
                ) from exc
            return payload

        return dependency

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/token", response_model=TokenResponse)
    def auth_token(payload: TokenRequest) -> TokenResponse:
        try:
            token = auth_manager.authenticate(payload.username, payload.totp)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
            ) from exc
        user = cfg.security.get_user(payload.username)
        return TokenResponse(token=token, role=user.role)

    @app.get("/api/profiles")
    def list_profiles(
        _: dict = Depends(require_token("viewer")),
    ) -> Dict[str, Any]:
        profiles = [
            {
                "id": profile.id,
                "name": profile.name,
                "thresholds": asdict(profile.thresholds),
                "poll_interval": profile.poll_interval,
            }
            for profile in cfg.iter_profiles()
        ]
        return {"profiles": profiles, "default": cfg.default_profile}

    @app.get("/api/dashboard")
    async def get_dashboard(
        profile: Optional[str] = Query(None),
        _: dict = Depends(require_token("viewer")),
        conn=Depends(get_conn),
    ) -> Dict[str, Any]:
        snapshot = await gather_dashboard_snapshot(
            cfg,
            profile_id=profile,
            price_service=price_service,
            conn=conn,
        )
        return snapshot.as_dict()

    @app.get("/api/history")
    def get_history(
        profile: Optional[str] = Query(None),
        limit: int = Query(100, ge=1, le=2000),
        hours: Optional[float] = Query(None, gt=0.0),
        _: dict = Depends(require_token("viewer")),
        conn=Depends(get_conn),
    ) -> Dict[str, Any]:
        service = ReportingService(cfg, conn=conn)
        entries = service.get_ltv_history(profile, limit=limit, hours=hours)
        return {"entries": [entry.to_dict() for entry in entries]}

    @app.get("/api/history/summary")
    def get_history_summary(
        profile: Optional[str] = Query(None),
        limit: int = Query(1000, ge=10, le=5000),
        hours: Optional[float] = Query(None, gt=0.0),
        _: dict = Depends(require_token("viewer")),
        conn=Depends(get_conn),
    ) -> Dict[str, Any]:
        service = ReportingService(cfg, conn=conn)
        summary = service.summarize_ltv(profile, limit=limit, hours=hours)
        return summary

    @app.post("/api/actions/reserves/transfer")
    def transfer_reserve(
        request: TransferRequest,
        token: dict = Depends(require_token("trader")),
        conn=Depends(get_conn),
    ) -> Dict[str, Dict[str, Dict[str, float]]]:
        if not _enforce_direction(request.direction):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="direction must be 'to_collateral' or 'to_reserve'",
            )
        manager = ReserveManager(cfg, profile_id=request.profile, conn=conn)
        to_collateral = request.direction == "to_collateral"
        try:
            manager.transfer(
                request.asset,
                request.amount,
                to_collateral,
                user=token.get("sub"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"balances": manager.get_balances()}

    @app.post("/api/actions/repay")
    def repay(
        request: RepayRequest,
        token: dict = Depends(require_token("trader")),
        conn=Depends(get_conn),
    ) -> Dict[str, Any]:
        service = RepaymentService(
            cfg,
            conn=conn,
            profile_id=request.profile,
            price_service=price_service,
        )
        try:
            result = service.repay(
                request.amount,
                dry_run=request.dry_run,
                user=token.get("sub"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return result

    return app


app = create_app()

__all__ = ["create_app", "app"]
