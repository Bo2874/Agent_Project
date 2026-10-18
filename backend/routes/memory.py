from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from backend.dependencies import CurrentUser, DbSession
from backend.schemas.memory import MemoryPinRequest, MemoryResponse, MemoryUpdateRequest
from backend.services.memory import (
    MemoryNotFoundError,
    SensitiveMemoryRejectedError,
    delete_user_memory,
    list_user_memories,
    set_memory_pinned,
    update_user_memory,
)

router = APIRouter(prefix="/api/memories", tags=["memory"])


@router.get("", response_model=list[MemoryResponse])
async def get_memories(session: DbSession, user: CurrentUser) -> list[MemoryResponse]:
    memories = await list_user_memories(session, user.id)
    return [MemoryResponse.model_validate(memory) for memory in memories]


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def patch_memory(
    memory_id: UUID,
    data: MemoryUpdateRequest,
    session: DbSession,
    user: CurrentUser,
) -> MemoryResponse:
    try:
        memory = await update_user_memory(
            session,
            user_id=user.id,
            memory_id=memory_id,
            content=data.content,
        )
    except MemoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SensitiveMemoryRejectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return MemoryResponse.model_validate(memory)


@router.patch("/{memory_id}/pin", response_model=MemoryResponse)
async def patch_memory_pin(
    memory_id: UUID,
    data: MemoryPinRequest,
    session: DbSession,
    user: CurrentUser,
) -> MemoryResponse:
    try:
        memory = await set_memory_pinned(
            session,
            user_id=user.id,
            memory_id=memory_id,
            is_pinned=data.is_pinned,
        )
    except MemoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return MemoryResponse.model_validate(memory)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: UUID,
    session: DbSession,
    user: CurrentUser,
) -> Response:
    try:
        await delete_user_memory(session, user_id=user.id, memory_id=memory_id)
    except MemoryNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
