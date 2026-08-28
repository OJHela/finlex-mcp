"""Compatibility middleware for MCP clients that decorate tool arguments.

Intric attaches its own bookkeeping field to every tools/call payload:

    {"hakusana": "diabetes", "observability_context": {"tool_use_id": "..."}}

FastMCP validates arguments strictly against the tool signature, so those calls
failed before the tool ever ran:

    ValidationError: 1 validation error for call[<tool_name>]
    observability_context — Unexpected keyword argument

Dropping arguments the tool's input schema doesn't declare makes the server
tolerant of this (and of any similar field a client adds later).
"""

from __future__ import annotations

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

# Used only when the tool's schema can't be resolved: fields clients are known
# to inject, plus anything underscore-prefixed (MCP reserves that namespace).
KNOWN_CLIENT_EXTRAS = {"observability_context"}


class DropUnknownArgumentsMiddleware(Middleware):
    """Strip tool-call arguments that the target tool doesn't declare."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        arguments = context.message.arguments or {}
        if not arguments:
            return await call_next(context)

        declared = await self._declared_parameters(context, context.message.name)
        if declared is None:
            unknown = [
                key
                for key in arguments
                if key in KNOWN_CLIENT_EXTRAS or key.startswith("_")
            ]
        else:
            unknown = [key for key in arguments if key not in declared]

        if unknown:
            logger.info(
                "Dropping undeclared argument(s) %s from call to '%s'",
                ", ".join(sorted(unknown)),
                context.message.name,
            )
            cleaned = {k: v for k, v in arguments.items() if k not in unknown}
            context = context.copy(
                message=context.message.model_copy(update={"arguments": cleaned})
            )

        return await call_next(context)

    async def _declared_parameters(
        self, context: MiddlewareContext[mt.CallToolRequestParams], name: str
    ) -> set[str] | None:
        """Parameter names from the tool's input schema, or None if unavailable."""
        fastmcp_context = context.fastmcp_context
        if fastmcp_context is None:
            return None
        try:
            tool = await fastmcp_context.fastmcp.get_tool(name)
        except Exception:  # unknown tool, disabled tool — let the server answer
            return None
        if tool is None:
            return None
        properties = (tool.parameters or {}).get("properties")
        if not isinstance(properties, dict):
            return None
        return set(properties)
