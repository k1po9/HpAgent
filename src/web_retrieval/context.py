"""Trusted persisted source policy, scoped to one Research fetch stage."""
from contextvars import ContextVar

from research_domain.models import SourceStrategy

source_scope: ContextVar[SourceStrategy] = ContextVar('retrieval_source_scope', default=SourceStrategy())
