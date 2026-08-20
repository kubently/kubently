"""Audit module: records and surfaces the command/security audit trail."""

from kubently.modules.audit.audit import AUDIT_KEY, AUDIT_MAX_ENTRIES, AuditModule

__all__ = ["AUDIT_KEY", "AUDIT_MAX_ENTRIES", "AuditModule"]
