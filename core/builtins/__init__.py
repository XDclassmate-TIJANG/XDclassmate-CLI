"""内置能力包：随微内核一起装载的最小功能集合。"""
from .system_commands import register_system_commands

__all__ = ["register_system_commands"]
