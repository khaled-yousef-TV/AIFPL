"""
Background tasks management endpoints.
"""

import logging
from fastapi import APIRouter, HTTPException

from services.dependencies import get_dependencies

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_tasks(include_old: bool = False):
    """
    Get all tasks.
    
    Args:
        include_old: If True, include old completed tasks.
                     If False, only return tasks from last 5 minutes or running/pending tasks.
    """
    try:
        deps = get_dependencies()
        tasks = deps.db_manager.get_all_tasks(include_old=include_old)
        return {"tasks": tasks}
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{task_id}")
async def get_task(task_id: str):
    """Get a specific task by ID."""
    try:
        deps = get_dependencies()
        task = deps.db_manager.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
        return task
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def create_task(request: dict):
    """
    Create a new task.
    
    Request body:
        - task_id/id: Unique task identifier (required)
        - task_type/type: Type of task (required)
        - title: Task title (required)
        - description: Task description (optional)
        - status: Task status (default: "pending")
        - progress: Progress percentage 0-100 (default: 0)
    """
    try:
        deps = get_dependencies()
        
        task_id = request.get("task_id") or request.get("id")
        if not task_id:
            raise HTTPException(status_code=400, detail="task_id is required")
        
        task_type = request.get("task_type") or request.get("type")
        if not task_type:
            raise HTTPException(status_code=400, detail="task_type is required")
        
        title = request.get("title")
        if not title:
            raise HTTPException(status_code=400, detail="title is required")
        
        task = deps.db_manager.create_task(
            task_id=task_id,
            task_type=task_type,
            title=title,
            description=request.get("description"),
            status=request.get("status", "pending"),
            progress=request.get("progress", 0)
        )
        return task
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{task_id}")
async def update_task(task_id: str, request: dict):
    """
    Update an existing task.
    
    Request body (all optional):
        - status: New status
        - progress: New progress (0-100)
        - error: Error message if failed
    """
    try:
        deps = get_dependencies()
        task = deps.db_manager.update_task(
            task_id=task_id,
            status=request.get("status"),
            progress=request.get("progress"),
            error=request.get("error")
        )
        if not task:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
        return task
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    """Delete a task."""
    try:
        deps = get_dependencies()
        deleted = deps.db_manager.delete_task(task_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
        return {"success": True, "message": f"Task '{task_id}' deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

