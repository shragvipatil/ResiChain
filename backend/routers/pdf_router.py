"""
routers/pdf_router.py — Day 10 deliverable (Person C)

If you are reading this after another "file not found" incident:
the file itself needs `git add backend/routers/pdf_router.py` run
explicitly and committed — it is not enough to commit main.py's
import line alone. Please verify with `git log --all --full-history
-- backend/routers/pdf_router.py` before assuming it's tracked.

Standalone router for playbook PDF exports. Deliberately kept separate
from routers/api.py to avoid any merge conflict with Person A/B's work —
this file only reads from the same mock playbook/procurement contracts
that api.py already exposes; it does not modify api.py in any way.

Integration (2 lines in main.py):
    from routers.pdf_router import router as pdf_router
    app.include_router(pdf_router)

Endpoint:
    GET /api/playbook/{playbook_id}/pdf?role=ministry|procurement
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
import copy

from contracts.api_contracts import MOCK_PLAYBOOK, MOCK_PROCUREMENT_OPTIONS
from services.pdf_export import generate_ministry_pdf, generate_procurement_pdf

router = APIRouter(prefix="/api", tags=["PDF Export"])

_pdf_playbooks = {"pb_001": copy.deepcopy(MOCK_PLAYBOOK)}


@router.get("/playbook/{playbook_id}/pdf")
async def get_playbook_pdf(playbook_id: str, role: str = "ministry"):
    """
    role=ministry     -> single-page plain-language summary
    role=procurement  -> full rejection trace + approved alternatives detail
    """
    playbook = _pdf_playbooks.get(playbook_id)
    if not playbook:
        raise HTTPException(status_code=404, detail="Playbook not found")

    if role == "ministry":
        pdf_bytes = generate_ministry_pdf(playbook)
        filename = f"resichain_ministry_{playbook_id}.pdf"
    elif role == "procurement":
        pdf_bytes = generate_procurement_pdf(playbook, MOCK_PROCUREMENT_OPTIONS)
        filename = f"resichain_procurement_{playbook_id}.pdf"
    else:
        raise HTTPException(
            status_code=400, detail="role must be 'ministry' or 'procurement'"
        )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )