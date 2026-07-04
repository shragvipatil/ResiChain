"""
routers/pdf_router.py — Day 10 deliverable (Person C)

Standalone router for playbook PDF exports. Deliberately kept separate
from routers/api.py to avoid any merge conflict with Person A's work —
this file only reads from the same in-memory _playbooks store and mock
procurement options that api.py already exposes; it does not modify
api.py in any way.

Integration (one line, added by whoever owns main.py):
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

# Separate in-memory store, seeded the same way api.py seeds _playbooks.
# If api.py's _playbooks is later moved to Postgres, this can import
# from there instead — no other change needed in this file.
_pdf_playbooks = {"pb_001": copy.deepcopy(MOCK_PLAYBOOK)}


@router.get("/playbook/{playbook_id}/pdf")
async def get_playbook_pdf(playbook_id: str, role: str = "ministry"):
    """
    Generates and returns a role-specific PDF export of the playbook.
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