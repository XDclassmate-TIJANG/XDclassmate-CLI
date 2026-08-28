from collections import defaultdict
from typing import Callable
import threading
import logging

logger = logging.getLogger(__name__)

# 事件总线
class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._handlers = defaultdict(list)   # event -> list of (handler, once_flag)

    def on(self, event: str, handler: Callable, *, once: bool = False) -> Callable[[], None]:
        """
        订阅事件。
        :param once: True 表示只执行一次后自动取消订阅
        :return: 一个用于取消订阅的闭包函数，方便 lambda 等匿名函数取消
        """
        with self._lock:
            self._handlers[event].append((handler, once))
        # 返回取消订阅函数，无需再持有原始 handler 引用
        def unsubscribe():
            self.off(event, handler)
        return unsubscribe

    def off(self, event: str, handler: Callable):
        """取消订阅（只移除第一个匹配的 handler）"""
        with self._lock:
            handlers = self._handlers.get(event)
            if not handlers:
                return
            # 找到并移除 (handler, once) 元组
            for i, (h, _) in enumerate(handlers):
                if h is handler:
                    del handlers[i]
                    break

    def emit(self, event: str, *args, **kwargs):
        """
        触发事件。每个处理器独立运行，异常不会影响后续处理器。
        """
        # 先获取当前快照，减少锁持有时间
        with self._lock:
            handlers_snapshot = list(self._handlers.get(event, []))

        # 执行处理器，并处理一次性标记
        to_remove = []
        for handler, once in handlers_snapshot:
            try:
                handler(*args, **kwargs)
            except Exception as e:
                logger.exception(f"Handler {handler} failed on event '{event}': {e}")
            if once:
                to_remove.append((handler, once))

        # 清理一次性处理器
        if to_remove:
            with self._lock:
                handlers = self._handlers.get(event)
                if handlers:
                    # 过滤掉需要移除的一次性处理器
                    for item in to_remove:
                        try:
                            handlers.remove(item)
                        except ValueError:
                            pass
                    self._handlers[event] = handlers

    def clear(self, event: str = None):
        """清空所有或指定事件的处理器"""
        with self._lock:
            if event:
                self._handlers.pop(event, None)
            else:
                self._handlers.clear()

bus = EventBus()

# 使用示例
if __name__ == "__main__":
    def greet(name):
        print(f"Hello, {name}!")

    # 普通订阅
    unsub = bus.on("say_hello", greet)
    # 一次性订阅
    bus.on("startup", lambda: print("第一次启动"), once=True)

    bus.emit("say_hello", "Alice")   # Hello, Alice!
    bus.emit("startup")              # 第一次启动
    bus.emit("startup")              # 不再输出（已自动取消）

    # 手动取消
    unsub()                          # 等价于 bus.off("say_hello", greet)
    bus.emit("say_hello", "Bob")     # 无输出