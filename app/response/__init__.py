"""Pure, deterministic response generation: turns a Phase 07 `AgentResult` +
this turn's `TeachingPlan` into the final learner-facing reply.

No LLM calls live in this package -- see `app.response.generate` for the
fallback ladder and the code-reveal invariant this layer enforces.
"""

from app.response.format import SAFE_FALLBACK_RESPONSE
from app.response.generate import GeneratedResponse, generate_response

__all__ = ["SAFE_FALLBACK_RESPONSE", "GeneratedResponse", "generate_response"]
