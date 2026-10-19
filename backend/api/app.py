"""Local-only HTTP surface for the EraseLock prototype.

This intentionally does not claim production authentication. A real JWT/OIDC
provider and persistent authority are required before an internet deployment.
"""

from __future__ import annotations

import os
import hmac
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from contracts.adapter_protocol import SubjectScope
from backend.eraselock.authority import InMemoryAuthority
from backend.eraselock.domain import (
    AdmissionClosed,
    Conflict,
    EraseLockError,
    EvidenceIncomplete,
    InflightUnresolved,
    NotFound,
    StaleEpoch,
)
from backend.eraselock.workflows import ErasureWorkflow
from backend.eraselock.local_security import verify_token

SCENARIOS = ("old_database_recovery", "delayed_message_after_erasure", "s3_version_resurrection", "index_reingestion")


class EraseRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    subject_generation: int = Field(ge=1)
    request_id: str = Field(min_length=1)


class EraseResponse(BaseModel):
    decision_id: str
    request_id: str
    tenant_id: str
    subject_id: str
    subject_generation: int
    state: str
    authority_epoch: int
    evidence: dict | None = None


class LocalPrincipal(BaseModel):
    tenant_id: str
    subject_id: str


class LabRunRequest(BaseModel):
    scenario: str
    mode: str
    seed: int = Field(default=42, ge=0, le=999999)


def require_local_principal(
    authorization: Annotated[str | None, Header()] = None,
    x_rgx_tenant: Annotated[str | None, Header()] = None,
    x_rgx_subject: Annotated[str | None, Header()] = None,
) -> LocalPrincipal:
    if os.environ.get("RGX_ENV") != "local":
        raise HTTPException(status_code=503, detail="Local API is disabled outside local mode")
    if os.environ.get("RGX_AUTH_MODE", "signed") == "signed":
        try:
            if not authorization or not authorization.startswith("Bearer "):
                raise ValueError("Missing token")
            claims = verify_token(os.environ.get("RGX_LOCAL_SIGNING_KEY", "").encode(), authorization[7:])
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid or expired local session")
        return LocalPrincipal(tenant_id=claims["tenant_id"], subject_id=claims["subject_id"])
    if os.environ.get("RGX_AUTH_MODE") != "demo":
        raise HTTPException(status_code=503, detail="Unknown local authentication mode")
    token = os.environ.get("RGX_LOCAL_API_TOKEN")
    if not token or not hmac.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(status_code=401, detail="Invalid local demo token")
    if not x_rgx_tenant or not x_rgx_subject:
        raise HTTPException(status_code=401, detail="Missing local demo identity")
    return LocalPrincipal(tenant_id=x_rgx_tenant, subject_id=x_rgx_subject)


def create_app(authority: InMemoryAuthority | None = None, *, adapters=None, signer=None, tasks=None) -> FastAPI:
    app = FastAPI(title="RewindGuard X local API", version="0.1.0")
    app.state.authority = authority or InMemoryAuthority()
    workflow = ErasureWorkflow(app.state.authority, adapters) if adapters else None
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    if frontend.is_dir():
        app.mount("/ui/assets", StaticFiles(directory=frontend), name="ui-assets")

        @app.get("/ui", include_in_schema=False)
        def user_interface():
            return FileResponse(frontend / "index.html")

    @app.exception_handler(EraseLockError)
    async def erasure_error_handler(_request, error: EraseLockError):
        from fastapi.responses import JSONResponse

        status = 404 if isinstance(error, NotFound) else 409
        if isinstance(error, (EvidenceIncomplete, InflightUnresolved, StaleEpoch, AdmissionClosed, Conflict)):
            status = 409
        return JSONResponse(status_code=status, content={"code": error.code, "detail": str(error)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "local-only"}

    @app.post("/v1/erase-requests", response_model=EraseResponse, status_code=202)
    def create_erase_request(
        payload: EraseRequest,
        principal: Annotated[LocalPrincipal, Depends(require_local_principal)],
    ) -> EraseResponse:
        if (payload.tenant_id, payload.subject_id) != (principal.tenant_id, principal.subject_id):
            raise HTTPException(status_code=403, detail="Cannot erase another subject")
        scope = SubjectScope(payload.tenant_id, payload.subject_id, payload.subject_generation)
        if workflow is None:
            raise HTTPException(status_code=503, detail="Erasure adapters are not configured")
        if tasks is not None:
            result = tasks.erase({"scope": asdict(scope), "request_id": payload.request_id})
            decision = app.state.authority.decision(result["decision_id"])
            return EraseResponse(
                decision_id=decision.decision_id, request_id=decision.request_id,
                tenant_id=scope.tenant_id, subject_id=scope.subject_id,
                subject_generation=scope.subject_generation, state=result["state"],
                authority_epoch=decision.committed_epoch or decision.closed_epoch,
                evidence=result["evidence"],
            )
        outcome = workflow.run(scope, payload.request_id)
        decision = app.state.authority.decision(outcome.decision_id)
        return EraseResponse(
            decision_id=decision.decision_id,
            request_id=decision.request_id,
            tenant_id=scope.tenant_id,
            subject_id=scope.subject_id,
            subject_generation=scope.subject_generation,
            state=outcome.state.value,
            authority_epoch=decision.committed_epoch or decision.closed_epoch,
            evidence=signer.sign(outcome.certificate) if signer else asdict(outcome.certificate),
        )

    @app.get("/v1/erase-requests/{decision_id}", response_model=EraseResponse)
    def get_erase_request(
        decision_id: str,
        principal: Annotated[LocalPrincipal, Depends(require_local_principal)],
    ) -> EraseResponse:
        decision = app.state.authority.decision(decision_id)
        scope = decision.scope
        if (scope.tenant_id, scope.subject_id) != (principal.tenant_id, principal.subject_id):
            raise HTTPException(status_code=403, detail="Cannot inspect another subject")
        return EraseResponse(
            decision_id=decision.decision_id,
            request_id=decision.request_id,
            tenant_id=scope.tenant_id,
            subject_id=scope.subject_id,
            subject_generation=scope.subject_generation,
            state=decision.state.value,
            authority_epoch=decision.committed_epoch or decision.closed_epoch,
        )

    @app.get("/v1/lab/scenarios")
    def list_lab_scenarios(
        _principal: Annotated[LocalPrincipal, Depends(require_local_principal)],
    ) -> dict[str, tuple[str, ...]]:
        return {"scenarios": SCENARIOS}

    @app.post("/v1/lab/runs")
    def run_lab_scenario(
        payload: LabRunRequest,
        _principal: Annotated[LocalPrincipal, Depends(require_local_principal)],
    ) -> dict[str, object]:
        if payload.scenario not in SCENARIOS or payload.mode not in ("baseline", "protected"):
            raise HTTPException(status_code=422, detail="Unknown scenario or mode")
        dsn = os.environ.get("RGX_TEST_POSTGRES_DSN")
        if not dsn:
            raise HTTPException(status_code=503, detail="Local PostgreSQL is not configured")
        # Import the optional PostgreSQL lab only when explicitly invoked.
        from lab.runner import run_scenario
        return asdict(run_scenario(dsn, payload.scenario, payload.mode, seed=payload.seed))

    return app


app = create_app()
