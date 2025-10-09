"""Todo management for systematic debugging workflows."""

import json
import logging
from typing import Dict, List, Optional, Literal
from datetime import datetime, timezone
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TodoItem(BaseModel):
    """A single todo item for tracking debugging tasks."""

    id: str = Field(description="Unique identifier for the todo item")
    content: str = Field(description="The task description in imperative form (e.g., 'Check pod status')")
    activeForm: str = Field(description="Present continuous form shown during execution (e.g., 'Checking pod status')")
    status: Literal["pending", "in_progress", "completed"] = Field(
        default="pending",
        description="Current status of the task"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    findings: Optional[str] = Field(default=None, description="Findings or results from completing this task")
    related_resources: List[str] = Field(default_factory=list, description="Kubernetes resources examined in this task")


class TodoManager:
    """Manages todo items for debugging workflows."""

    def __init__(self, thread_id: str):
        """Initialize a todo manager for a specific debugging thread.

        Args:
            thread_id: Unique identifier for the debugging session
        """
        self.thread_id = thread_id
        self.todos: Dict[str, TodoItem] = {}
        self._todo_counter = 0

    def add_todo(self, content: str, activeForm: str, status: str = "pending") -> TodoItem:
        """Add a new todo item.

        Args:
            content: The task description in imperative form
            activeForm: Present continuous form of the task
            status: Initial status (pending, in_progress, completed)

        Returns:
            The created TodoItem
        """
        self._todo_counter += 1
        todo_id = f"todo_{self.thread_id}_{self._todo_counter}"

        todo = TodoItem(
            id=todo_id,
            content=content,
            activeForm=activeForm,
            status=status
        )

        self.todos[todo_id] = todo
        logger.info(f"Added todo {todo_id}: {content}")
        return todo

    def update_todo(self, todo_id: str, **kwargs) -> Optional[TodoItem]:
        """Update an existing todo item.

        Args:
            todo_id: ID of the todo to update
            **kwargs: Fields to update (status, findings, related_resources)

        Returns:
            The updated TodoItem or None if not found
        """
        if todo_id not in self.todos:
            logger.warning(f"Todo {todo_id} not found")
            return None

        todo = self.todos[todo_id]

        # Update allowed fields
        for field in ["status", "findings", "related_resources"]:
            if field in kwargs:
                setattr(todo, field, kwargs[field])

        todo.updated_at = datetime.now(timezone.utc)
        logger.info(f"Updated todo {todo_id}: {kwargs}")
        return todo

    def get_todo(self, todo_id: str) -> Optional[TodoItem]:
        """Get a specific todo item.

        Args:
            todo_id: ID of the todo to retrieve

        Returns:
            The TodoItem or None if not found
        """
        return self.todos.get(todo_id)

    def get_all_todos(self) -> List[TodoItem]:
        """Get all todos in order of creation.

        Returns:
            List of all TodoItem objects
        """
        return sorted(self.todos.values(), key=lambda t: t.created_at)

    def get_todos_by_status(self, status: str) -> List[TodoItem]:
        """Get todos filtered by status.

        Args:
            status: Status to filter by (pending, in_progress, completed)

        Returns:
            List of TodoItem objects with matching status
        """
        return [todo for todo in self.todos.values() if todo.status == status]

    def mark_in_progress(self, todo_id: str) -> Optional[TodoItem]:
        """Mark a todo as in progress.

        Args:
            todo_id: ID of the todo to mark as in progress

        Returns:
            The updated TodoItem or None if not found
        """
        # Ensure only one item is in progress at a time
        for todo in self.todos.values():
            if todo.status == "in_progress" and todo.id != todo_id:
                todo.status = "pending"
                todo.updated_at = datetime.now(timezone.utc)

        return self.update_todo(todo_id, status="in_progress")

    def mark_completed(self, todo_id: str, findings: Optional[str] = None,
                      related_resources: Optional[List[str]] = None) -> Optional[TodoItem]:
        """Mark a todo as completed with optional findings.

        Args:
            todo_id: ID of the todo to mark as completed
            findings: Optional findings or results from the task
            related_resources: Optional list of Kubernetes resources examined

        Returns:
            The updated TodoItem or None if not found
        """
        kwargs = {"status": "completed"}
        if findings:
            kwargs["findings"] = findings
        if related_resources:
            kwargs["related_resources"] = related_resources

        return self.update_todo(todo_id, **kwargs)

    def get_current_task(self) -> Optional[TodoItem]:
        """Get the currently in-progress task.

        Returns:
            The in-progress TodoItem or None if no task is in progress
        """
        in_progress = self.get_todos_by_status("in_progress")
        return in_progress[0] if in_progress else None

    def get_next_pending_task(self) -> Optional[TodoItem]:
        """Get the next pending task.

        Returns:
            The next pending TodoItem or None if no pending tasks
        """
        pending = self.get_todos_by_status("pending")
        return pending[0] if pending else None

    def to_dict(self) -> dict:
        """Convert the todo list to a dictionary for serialization.

        Returns:
            Dictionary representation of all todos
        """
        return {
            "thread_id": self.thread_id,
            "todos": [todo.model_dump(mode="json") for todo in self.get_all_todos()]
        }

    def get_progress_summary(self) -> dict:
        """Get a summary of the current progress.

        Returns:
            Dictionary with counts by status and current task
        """
        todos = self.get_all_todos()
        current_task = self.get_current_task()

        return {
            "total": len(todos),
            "pending": len(self.get_todos_by_status("pending")),
            "in_progress": len(self.get_todos_by_status("in_progress")),
            "completed": len(self.get_todos_by_status("completed")),
            "current_task": current_task.activeForm if current_task else None,
            "completion_percentage": round(
                (len(self.get_todos_by_status("completed")) / len(todos) * 100) if todos else 0,
                1
            )
        }

    def format_for_display(self) -> str:
        """Format the todo list for human-readable display.

        Returns:
            Formatted string representation of the todo list
        """
        lines = ["📋 Debugging Tasks:"]

        for todo in self.get_all_todos():
            status_icon = {
                "pending": "⏳",
                "in_progress": "🔄",
                "completed": "✅"
            }.get(todo.status, "❓")

            line = f"{status_icon} {todo.content}"
            if todo.status == "in_progress":
                line = f"**{line}** (In Progress)"
            elif todo.status == "completed" and todo.findings:
                line += f"\n    → {todo.findings}"

            lines.append(line)

        # Add progress summary
        summary = self.get_progress_summary()
        lines.append(f"\nProgress: {summary['completed']}/{summary['total']} tasks completed ({summary['completion_percentage']}%)")

        return "\n".join(lines)