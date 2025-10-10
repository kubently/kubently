"""
Queue Module - Black Box Interface

Purpose: Handle command queuing and result delivery
Interface: push_command(), pop_command(), store_result(), get_result()
Hidden: Queue implementation, blocking logic, result storage

Can be replaced with RabbitMQ, Kafka, or any message queue.
"""

from .queue import QueueModule

__all__ = ["QueueModule"]
