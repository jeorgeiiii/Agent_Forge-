from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ..deps import current_user_id, get_db
from ..schemas import ApprovalDecision, RunCreate, RunDetail, RunRead
from ..services import runs as svc
from ..services.graphs import get_graph_by_id

router = APIRouter(prefix="/graphs/{graph_id}/runs", tags=["runs"])


@router.post("", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
async def start_run(graph_id: int, payload: RunCreate, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    graph = get_graph_by_id(db, user_id, graph_id)
    return await svc.start_run(db, graph, payload.input, user_id=user_id, trigger_kind="manual")


@router.get("", response_model=list[RunRead])
def list_runs(graph_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.list_runs(db, user_id, get_graph_by_id(db, user_id, graph_id))


@router.get("/{run_id}", response_model=RunDetail)
def get_run(graph_id: int, run_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.get_run(db, user_id, graph_id, run_id)


@router.get("/{run_id}/status")
async def get_run_status(graph_id: int, run_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return await svc.get_run_status(db, svc.get_run(db, user_id, graph_id, run_id))


@router.get("/{run_id}/output")
def get_run_output(graph_id: int, run_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return svc.get_run_output(svc.get_run(db, user_id, graph_id, run_id))


@router.post("/{run_id}/cancel", response_model=RunRead)
async def cancel_run(graph_id: int, run_id: int, user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    return await svc.cancel_run(db, user_id, svc.get_run(db, user_id, graph_id, run_id))


@router.post("/{run_id}/approval", response_model=RunRead)
async def approve(graph_id: int, run_id: int, payload: ApprovalDecision,
                  user_id: int | None = Depends(current_user_id), db: Session = Depends(get_db)):
    run = svc.get_run(db, user_id, graph_id, run_id)
    return await svc.resume_run_with_signal(
        db, user_id, run, node_id=payload.node_id, approved=payload.approved, comment=payload.comment
    )
