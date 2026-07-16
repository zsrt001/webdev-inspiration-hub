"""Side-effect-free compatibility tombstones for permanently retired routes."""

from fastapi import APIRouter, HTTPException


router = APIRouter(include_in_schema=False)


def _raise_retired(code: str, message: str) -> None:
    raise HTTPException(status_code=410, detail={"code": code, "message": message})


@router.post("/auth/login")
async def retired_auth_login() -> None:
    _raise_retired("auth_method_retired", "This authentication method is no longer available.")


@router.post("/session/create")
async def retired_partner_session_create() -> None:
    _raise_retired("partner_session_retired", "The former anonymous partner session is no longer available.")


@router.get("/session/{session_id}/status")
async def retired_partner_session_status(session_id: str) -> None:
    _ = session_id
    _raise_retired("partner_session_retired", "The former anonymous partner session is no longer available.")


@router.post("/session/{session_id}/upload/host")
async def retired_partner_session_host_upload(session_id: str) -> None:
    _ = session_id
    _raise_retired("partner_session_retired", "The former anonymous partner session is no longer available.")


@router.post("/session/{session_id}/upload/guest")
async def retired_partner_session_guest_upload(session_id: str) -> None:
    _ = session_id
    _raise_retired("partner_session_retired", "The former anonymous partner session is no longer available.")


@router.get("/session/{session_id}/images")
async def retired_partner_session_images(session_id: str) -> None:
    _ = session_id
    _raise_retired("partner_session_retired", "The former anonymous partner session is no longer available.")


@router.get("/session/{session_id}/share_meta")
async def retired_partner_session_share_meta(session_id: str) -> None:
    _ = session_id
    _raise_retired("partner_session_retired", "The former anonymous partner session is no longer available.")


@router.post("/session/{session_id}/processing")
async def retired_partner_session_processing(session_id: str) -> None:
    _ = session_id
    _raise_retired("partner_session_retired", "The former anonymous partner session is no longer available.")


@router.post("/session/{session_id}/complete")
async def retired_partner_session_complete(session_id: str) -> None:
    _ = session_id
    _raise_retired("partner_session_retired", "The former anonymous partner session is no longer available.")


@router.post("/session/{session_id}/bind_order")
async def retired_partner_session_bind_order(session_id: str) -> None:
    _ = session_id
    _raise_retired("partner_session_retired", "The former anonymous partner session is no longer available.")


@router.post("/users/")
async def retired_user_create() -> None:
    _raise_retired("legacy_user_route_retired", "This user endpoint is no longer available.")


@router.api_route("/users/{user_id}", methods=["GET", "PATCH"])
async def retired_user_detail(user_id: str) -> None:
    _ = user_id
    _raise_retired("legacy_user_route_retired", "This user endpoint is no longer available.")


@router.post("/credits/purchase")
async def retired_credit_purchase() -> None:
    _raise_retired("legacy_credit_mutation_retired", "This credit mutation is no longer available.")


@router.post("/credits/deduct")
async def retired_credit_deduct() -> None:
    _raise_retired("legacy_credit_mutation_retired", "This credit mutation is no longer available.")


@router.post("/credits/add")
async def retired_credit_add() -> None:
    _raise_retired("legacy_credit_mutation_retired", "This credit mutation is no longer available.")


@router.post("/live_portrait/generate")
async def retired_live_portrait_generate() -> None:
    _raise_retired("live_portrait_retired", "Live Portrait is not part of this product.")


@router.get("/live_portrait/list")
async def retired_live_portrait_list() -> None:
    _raise_retired("live_portrait_retired", "Live Portrait is not part of this product.")


@router.get("/live_portrait/{job_id}")
async def retired_live_portrait_detail(job_id: str) -> None:
    _ = job_id
    _raise_retired("live_portrait_retired", "Live Portrait is not part of this product.")


@router.get("/recommendations/local_studios")
async def retired_local_recommendations() -> None:
    _raise_retired("local_recommendations_retired", "Local vendor recommendations are not part of this product.")


@router.post("/leads/submit")
async def retired_lead_submit() -> None:
    _raise_retired("leads_retired", "Lead and CRM workflows are not part of this product.")


@router.get("/leads/list")
async def retired_lead_list() -> None:
    _raise_retired("leads_retired", "Lead and CRM workflows are not part of this product.")


@router.get("/leads/export.csv")
async def retired_lead_export() -> None:
    _raise_retired("leads_retired", "Lead and CRM workflows are not part of this product.")


@router.get("/admin/crm_preview")
async def retired_admin_crm_preview() -> None:
    _raise_retired("leads_retired", "Lead and CRM workflows are not part of this product.")


@router.post("/admin/crm_push")
async def retired_admin_crm_push() -> None:
    _raise_retired("leads_retired", "Lead and CRM workflows are not part of this product.")


@router.get("/admin/crm_push_history")
async def retired_admin_crm_history() -> None:
    _raise_retired("leads_retired", "Lead and CRM workflows are not part of this product.")


@router.post("/admin/generation_probe")
async def retired_admin_generation_probe() -> None:
    _raise_retired(
        "admin_generation_execution_retired",
        "Admin generation probes must use the ordinary durable user order flow.",
    )


@router.post("/admin/orders/{order_id}/regenerate")
async def retired_admin_order_regenerate(order_id: str) -> None:
    _ = order_id
    _raise_retired(
        "admin_generation_execution_retired",
        "Admin regeneration is unavailable outside the durable job workflow.",
    )
