"""The single error contract of the application.

Every failure -- a validation error, an unknown route, a wrong HTTP method or
an unexpected crash -- is reported with the same envelope, so a caller never
has to guess the shape of the answer:

.. code-block:: json

    {
      "error": {
        "code": "METRIC_TYPE_MISMATCH",
        "message": "Human readable sentence explaining exactly what went wrong.",
        "field": "type",
        "expected": "counter",
        "received": "gauge",
        "hint": "Concrete next step the caller should take."
      }
    }

The API answers with that envelope as JSON; the web UI renders the very same
information as an HTML page.  The contract lives at the top level of the
package (and not inside a blueprint) because it is shared by the API and the
UI, and because an error raised deep inside the domain layer must be renderable
without knowing which route it will be served on.
"""

from __future__ import annotations

from typing import Any, Mapping

from flask import Flask, Request, jsonify, make_response, render_template, request
from werkzeug.exceptions import HTTPException

#: Keys that every error object carries, in the documented order.
ERROR_KEYS = ("code", "message", "field", "expected", "received", "hint")

#: Name of the template used for the HTML representation of an error.
ERROR_TEMPLATE = "error.html"


class ApiError(Exception):
    """An explicit, caller facing failure.

    The exception carries every piece of information the envelope needs, so
    raising it deep inside the validation code is enough to produce a precise
    answer.  The stack trace is never part of the payload: the caller gets the
    code, the offending field, what was expected, what arrived and a hint.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str | None = None,
        expected: Any = None,
        received: Any = None,
        hint: str | None = None,
        http_status: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.expected = expected
        self.received = received
        self.hint = hint
        self.http_status = http_status

    def to_envelope(self) -> dict[str, Any]:
        """Return the JSON envelope describing this error."""
        return error_envelope(
            self.code,
            self.message,
            field=self.field,
            expected=self.expected,
            received=self.received,
            hint=self.hint,
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"<ApiError {self.code} status={self.http_status} "
            f"field={self.field!r} message={self.message!r}>"
        )


def error_envelope(
    code: str,
    message: str,
    *,
    field: str | None = None,
    expected: Any = None,
    received: Any = None,
    hint: str | None = None,
) -> dict[str, Any]:
    """Build the canonical error envelope.

    ``code`` is a stable machine readable identifier, ``field`` points at the
    offending part of the request (``None`` for body level errors),
    ``expected``/``received`` describe the contract that was broken and
    ``hint`` tells the caller what to do next.
    """
    return {
        "error": {
            "code": code,
            "message": message,
            "field": field,
            "expected": expected,
            "received": received,
            "hint": hint,
        }
    }


def wants_json(req: Request) -> bool:
    """Return True when the caller expects the JSON envelope.

    API paths always answer with JSON.  Any other route answers with JSON only
    when the client explicitly asks for it, because a browser navigating to a
    URL must get a readable page instead of a raw JSON blob.
    """
    if req.path.startswith("/api/"):
        return True

    accept = req.headers.get("Accept", "")
    if "application/json" in accept:
        return True

    # A request that carries a JSON body is talking to the API, even if the
    # Accept header is missing or generic.
    return req.mimetype in ("application/json", "text/json")


def _describe_http_exception(error: HTTPException) -> tuple[str, str, str | None, Any, Any]:
    """Return ``(code, message, hint, expected, received)`` for an HTTP error."""
    status = error.code or 500

    if status == 400:
        return (
            "BAD_REQUEST",
            "The request could not be understood by the server.",
            "Send a well formed request; the server rejected the raw request before it could be parsed.",
            None,
            None,
        )
    if status == 404:
        return (
            "NOT_FOUND",
            "The requested resource does not exist.",
            "Check the URL path; the routes that exist are listed in the API reference.",
            None,
            None,
        )
    if status == 405:
        allowed = sorted(error.valid_methods) if error.valid_methods else []
        expected = ", ".join(allowed) if allowed else None
        return (
            "METHOD_NOT_ALLOWED",
            "The HTTP method is not allowed for this URL.",
            (
                f"Use one of the supported methods: {expected}."
                if expected
                else "Use an HTTP method supported by this route."
            ),
            expected,
            None,
        )
    if status == 415:
        return (
            "UNSUPPORTED_MEDIA_TYPE",
            "The server does not support the media type of the request body.",
            "Send the payload as application/json.",
            "application/json",
            None,
        )
    if status == 500:
        return (
            "INTERNAL_ERROR",
            "The operation failed unexpectedly on the server.",
            "Check the server logs for the full traceback.",
            None,
            None,
        )

    return (
        "HTTP_ERROR",
        error.description or "The request failed.",
        "Check the server logs for the full traceback.",
        None,
        None,
    )


def register_error_handlers(app: Flask) -> None:
    """Install the error handlers of the application.

    Three handlers are registered: the explicit :class:`ApiError`, the
    ``HTTPException`` family (404, 405, 415, 400, 500 ...) and a catch-all
    for everything else.  The catch-all logs the traceback on the server and
    answers with a generic but still explicit envelope, so an unexpected crash
    never leaks internals to the client.
    """

    @app.errorhandler(ApiError)
    def _handle_api_error(error: ApiError) -> Any:
        return _render_error(app, error.http_status, error.to_envelope())

    @app.errorhandler(HTTPException)
    def _handle_http_exception(error: HTTPException) -> Any:
        code, message, hint, expected, received = _describe_http_exception(error)
        envelope = error_envelope(
            code,
            message,
            field=None,
            expected=expected,
            received=received,
            hint=hint,
        )
        status = error.code or 500
        response = _render_error(app, status, envelope)

        # Keep the protocol level hints Werkzeug computed (for instance the
        # ``Allow`` header of a 405), otherwise the client loses information
        # that the envelope cannot express.
        original = error.get_response()
        for header in ("Allow", "Retry-After"):
            if header in original.headers and header not in response.headers:
                response.headers[header] = original.headers[header]
        return response

    @app.errorhandler(Exception)
    def _handle_unexpected_exception(error: Exception) -> Any:
        # The traceback belongs to the server logs, never to the payload.
        app.logger.exception(
            "Unhandled exception while handling %s %s", request.method, request.path
        )
        envelope = error_envelope(
            "INTERNAL_ERROR",
            f"The operation failed unexpectedly while handling {request.method} {request.path}.",
            field=None,
            expected=None,
            received=None,
            hint="Check the server logs for the full traceback and retry the request.",
        )
        return _render_error(app, 500, envelope)


def _render_error(app: Flask, status: int, envelope: Mapping[str, Any]) -> Any:
    """Return the JSON envelope or the rendered HTML page for an error."""
    if wants_json(request):
        response = jsonify(envelope)
        response.status_code = status
        return response

    # The HTML page shows exactly the same information as the JSON envelope, so
    # a browser user is never told less than an API client.
    html = render_template(
        ERROR_TEMPLATE,
        error=envelope["error"],
        status=status,
    )
    return make_response(html, status)
