"""SchemaGate: local, deterministic schema evolution admission."""

from .config import RULES_VERSION, TOOL_VERSION

__all__ = ["RULES_VERSION", "TOOL_VERSION", "__version__"]
__version__ = TOOL_VERSION
