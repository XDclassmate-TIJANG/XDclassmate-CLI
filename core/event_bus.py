"""事件总线：基于 pub/sub 的解耦通信机制。

特性：
    * 线程安全：订阅、取消与触发都在锁保护下进行；
    * 异常隔离：单个处理器的异常不会影响后续处理器，只记录日志；
    * 一次性订阅：`once=True` 的处理器在执行一次后自动取消订阅。
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable, Optional

from .logger import get_logger

LOGGER = get_logger("event_bus")


class EventBus:
    """事件总线：维护事件名到处理器列表的映射。"""

    def __init__(self):
        self._lock = threading.Lock()
        # event -> list of (handler, once_flag)
        self._handlers = defaultdict(list)

    def on(
            self,
            event: str,
            handler: Callable,
            *,
            once: bool = False
            ) -> Callable[[], None]:
        """
        订阅事件。

        :param event:   事件名称
        :param handler: 处理函数
        :param once:    True 表示执行一次后自动取消订阅
        :return:        用于取消订阅的闭包（便于匿名函数取消）
        """
        with self._lock:
            self._handlers[event].append((handler, once))
        LOGGER.debug("订阅事件 %s -> %s", event, getattr(
            handler, "__name__", handler))

        def unsubscribe() -> None:
            self.off(event, handler)

        return unsubscribe

    def off(self, event: str, handler: Callable) -> None:
        """取消订阅（只移除第一个匹配的处理器）。"""
        with self._lock:
            handlers = self._handlers.get(event)
            if not handlers:
                return
            for index, (current, _) in enumerate(handlers):
                if current is handler:
                    del handlers[index]
                    LOGGER.debug("取消订阅事件 %s", event)
                    break

    def emit(self, event: str, *args, **kwargs) -> None:
        """
        触发事件：每个处理器独立运行，异常只记录日志不影响后续处理器。

        :param event:  事件名称
        :param args:   传给处理器的位置参数
        :param kwargs: 传给处理器的关键字参数
        """
        with self._lock:
            snapshot = list(self._handlers.get(event, []))

        if not snapshot:
            LOGGER.debug("事件 %s 没有订阅者", event)
            return

        to_remove = []
        for handler, once in snapshot:
            try:
                handler(*args, **kwargs)
            except Exception as error:  # noqa: BLE001 —— 隔离单个处理器异常
                LOGGER.exception(
                    "事件 %s 的处理器 %s 执行失败: %s",
                    event, getattr(handler, "__name__", handler), error
                )
            if once:
                to_remove.append((handler, once))

        if not to_remove:
            return
        with self._lock:
            handlers = self._handlers.get(event)
            if not handlers:
                return
            for item in to_remove:
                try:
                    handlers.remove(item)
                except ValueError:
                    pass
            self._handlers[event] = handlers

    def clear(self, event: Optional[str] = None) -> None:
        """清空指定事件的处理器；event 为 None 时清空全部。"""
        with self._lock:
            if event:
                self._handlers.pop(event, None)
                LOGGER.debug("清空事件 %s 的处理器", event)
            else:
                self._handlers.clear()
                LOGGER.debug("清空全部事件处理器")


# 全局事件总线单例
bus = EventBus()
