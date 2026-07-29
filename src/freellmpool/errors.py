"""Exception hierarchy for freellmpool."""

from __future__ import annotations


class FreeLLMPoolError(Exception):
    """Base class for all freellmpool errors."""


# Deprecated pre-rename alias; kept so old `except BuffetError` imports don't break.
# Will be removed in a future release — catch FreeLLMPoolError instead.
BuffetError = FreeLLMPoolError


class NoProvidersConfigured(FreeLLMPoolError):
    """Raised when no provider has a usable API key in the environment."""


class AllProvidersExhausted(FreeLLMPoolError):
    """Raised when every candidate provider failed or is over budget.

    The ``attempts`` attribute holds a list of ``(target, reason)`` tuples
    describing what was tried and why each one was skipped or failed.
    """

    def __init__(
        self,
        attempts: list[tuple[str, str]],
        *,
        client_status: int | None = None,
        client_message: str | None = None,
    ):
        self.attempts = attempts
        # If the failures were caused by a non-retryable *client* error (a 4xx that
        # means the request itself is wrong, not the provider), carry its status so
        # the proxy can surface that instead of a generic 502.
        self.client_status = client_status
        self.client_message = client_message
        detail = "; ".join(f"{name}: {reason}" for name, reason in attempts) or "no candidates"
        super().__init__(f"all providers exhausted ({detail})")


class ContextWindowExceeded(AllProvidersExhausted):
    """Every candidate rejected the request because the input was too long.

    A subclass of :class:`AllProvidersExhausted` (so existing handlers still catch
    it) raised when failover ran out of models whose context window could fit the
    request, and no *other* kind of failure occurred. ``est_tokens`` is freellmpool's
    rough estimate of the request's input size.
    """

    def __init__(self, attempts: list[tuple[str, str]], *, est_tokens: int):
        self.est_tokens = est_tokens
        super().__init__(attempts)

    def __str__(self) -> str:
        detail = "; ".join(f"{name}: {reason}" for name, reason in self.attempts) or "no candidates"
        return (
            f"input is ~{self.est_tokens:,} tokens and exceeded the context window of every "
            f"model tried — shorten the input or configure a larger-context provider ({detail})"
        )


class ProviderHTTPError(FreeLLMPoolError):
    """A provider returned a non-success HTTP status.

    ``status`` is the HTTP status code; ``retryable`` indicates whether the
    router should move on to another provider (True) or give up (False).
    """

    def __init__(
        self,
        status: int,
        message: str,
        *,
        retryable: bool,
        retry_after: float | None = None,
    ):
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after
        super().__init__(f"HTTP {status}: {message}")
