from typing import Annotated

from fastapi import Depends

from agent_core.code_execution import register_python_execution_tool
from agent_core.plugins.manager import PluginManager
from agent_core.tools import ToolRegistry
from agent_core.tools.email import register_email_tool
from agent_core.tools.web_search import register_web_search_tool
from backend.core.config import get_settings
from backend.core.mcp_config import load_stdio_configs
from backend.core.mcp_http_config import load_http_configs

settings = get_settings()
tool_registry = ToolRegistry(allowlist=settings.tool_allowlist_names)
if settings.python_sandbox_enabled:
    register_python_execution_tool(tool_registry)
if settings.workspace_artifacts_enabled:
    from agent_core.tools.artifacts import register_artifact_tools
    from backend.services.artifacts.tools import ArtifactToolService

    register_artifact_tools(tool_registry, ArtifactToolService())
if (email_config := settings.email_config) is not None:
    register_email_tool(tool_registry, email_config)
if settings.google_connector_enabled:
    from agent_core.tools.google import register_google_tools
    from backend.services.google.tools import GoogleToolService

    register_google_tools(tool_registry, GoogleToolService())
if (web_search_config := settings.web_search_config) is not None:
    register_web_search_tool(tool_registry, web_search_config)
_plugin_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _plugin_manager
    if _plugin_manager is None:
        _plugin_manager = PluginManager(
            tool_registry,
            load_stdio_configs(settings) + load_http_configs(settings),
        )
    return _plugin_manager


async def start_tool_connections() -> None:
    await get_plugin_manager().start()


async def close_tool_connections() -> None:
    if _plugin_manager is not None:
        await _plugin_manager.close()


def get_tool_registry() -> ToolRegistry:
    return tool_registry


ToolRegistryDependency = Annotated[ToolRegistry, Depends(get_tool_registry)]
