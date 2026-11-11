from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from .. import copilot
from ..deps import current_user_id, get_db
from ..rate_limiter import rate_limit
from ..schemas import (
    CopilotEditRequest,
    CopilotExplainRequest,
    CopilotFixRunRequest,
    CopilotGenerateRequest,
    CopilotPatchRequest,
    CopilotRequest,
    CopilotResponse,
)
from ..services.common import ServiceError
from ..services.runs import get_run

router = APIRouter(prefix="/copilot", tags=["copilot"])
_limit = [Depends(rate_limit(max_requests=20, window_seconds=60))]


@router.post("/suggest", response_model=CopilotResponse, dependencies=_limit)
async def copilot_suggest(payload: CopilotRequest):
    return CopilotResponse(content=await copilot.suggest_graph(payload.prompt, payload.graph))


@router.post("/stream", dependencies=_limit)
async def copilot_stream(payload: CopilotRequest):
    return StreamingResponse(
        copilot.stream_suggest_graph(payload.prompt, payload.graph),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/generate", dependencies=_limit)
async def copilot_generate(payload: CopilotGenerateRequest):
    """Generate a graph that is guaranteed to compile (self-repair loop)."""
    return await copilot.generate_valid_graph(payload.prompt)


@router.post("/edit", dependencies=_limit)
async def copilot_edit(payload: CopilotEditRequest):
    return await copilot.edit_graph_from_prompt(
        payload.definition, payload.instruction, selected_node_id=payload.selected_node_id
    )


@router.post("/apply-patch")
def copilot_apply_patch(payload: CopilotPatchRequest):
    try:
        definition = copilot.apply_graph_patch(payload.definition, payload.patch)
    except (copilot.CopilotError, KeyError, TypeError) as error:
        raise ServiceError(f"Patch could not be applied: {error}", 422) from error
    return {"definition": definition, "errors": copilot.validate_generated_graph(definition)}


@router.post("/explain", dependencies=_limit)
async def copilot_explain(payload: CopilotExplainRequest):
    return {"explanation": await copilot.explain_node(payload.definition, payload.node_id)}


@router.post("/fix-run", dependencies=_limit)
async def copilot_fix_run(payload: CopilotFixRunRequest, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    run = get_run(db, user_id, payload.graph_id, payload.run_id)
    if run.status != "failed":
        raise ServiceError("Only failed runs can be diagnosed.", 409)
    return await copilot.fix_failed_run(run.graph.definition, run.state, run.error)


@router.post("/validate")
def copilot_validate(definition: dict = Body(..., embed=True)):
    """Body: {"definition": {...}} - runs the compiler on a (generated) graph."""
    errors = copilot.validate_generated_graph(definition)
    return {"valid": not errors, "errors": errors}
