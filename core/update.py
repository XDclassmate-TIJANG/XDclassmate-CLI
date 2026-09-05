"""CLI 与插件的更新提示。

设计约定（**改动前务必读完，这里踩过坑**）
------------------------------------------
1. **无模块级副作用**。旧实现在模块末尾直接调用 `send_update_cli_message()`，
   导致 `import core.update` 就会联网、读配置、弹窗——任何一处被误 import
   都会拖慢甚至打断启动。现在只定义函数，由 `core.kernel` 显式调用。
2. **绝不阻塞启动**。所有网络请求都有超时，所有异常都被吞掉并降级为日志；
   更新检查失败时 CLI 必须照常可用。
3. **可关闭**。受配置 `update_message` 控制；`official_url` 未配置时
   根本不会联网（离线环境下零开销、零外联）。
4. **桌面通知是增强项**。`winotify` 属于可选依赖，缺失时降级为一行
   终端提示；CLI 核心仍然保持零第三方依赖。

用法（由 core.kernel 在启动末尾调用）::

    from .update import notify_cli_update
    notify_cli_update()
"""
from __future__ import annotations

from typing import Optional

from .config import CLI_VERSION, CONFIG_KEY_UPDATE_MESSAGE, ConfigManager
from .i18n import t
from .logger import get_logger
from .network import get_last_cli_info, get_last_plugin_info
from .version import is_newer

LOGGER = get_logger("update")

# 桌面通知的应用标识
APP_ID = "XDclassmate-CLI"


def update_message_enabled(config_value: Optional[bool] = None) -> bool:
    """
    判断更新提示是否开启。

    :param config_value: 已读到的配置值，缺省按配置 update_message 读取
    """
    if config_value is None:
        config_value = ConfigManager().load_config(
            CONFIG_KEY_UPDATE_MESSAGE, default=True
        )
    return bool(config_value)


def check_cli_update(
        config_value: Optional[bool] = None
        ) -> Optional[dict]:
    """
    检查 CLI 是否有新版本。

    :return: 有新版本返回远端信息 dict；已是最新/未开启/查询失败返回 None
    """
    if not update_message_enabled(config_value):
        LOGGER.debug("更新提示已关闭，跳过检查")
        return None

    data = get_last_cli_info()
    if not data:
        return None
    new_version = str(data.get("version") or "").strip()
    if not new_version:
        LOGGER.debug("远端未返回 version，跳过更新提示")
        return None

    if is_newer(new_version, CLI_VERSION):
        LOGGER.info("发现 CLI 新版本: %s（当前 %s）", new_version, CLI_VERSION)
        return data
    if is_newer(CLI_VERSION, new_version):
        # 本地版本比远端还新：常见于开发分支，提示一次即可，不算错误
        LOGGER.debug(
            "本地版本 %s 新于远端 %s（开发分支？），不提示更新",
            CLI_VERSION, new_version
        )
        return None
    LOGGER.debug("CLI 已是最新版本（%s）", CLI_VERSION)
    return None


def check_plugin_update(
        name: str,
        current_version: str,
        config_value: Optional[bool] = None
        ) -> Optional[dict]:
    """
    检查指定插件是否有新版本。

    :param name:            插件名（目录名或清单名皆可）
    :param current_version: 本地已安装版本
    :return:                有新版本返回远端信息 dict；否则 None
    """
    if not update_message_enabled(config_value):
        return None
    data = get_last_plugin_info(name)
    if not data:
        return None
    new_version = str(data.get("version") or "").strip()
    if not new_version:
        return None
    if is_newer(new_version, current_version):
        LOGGER.info(
            "插件 %s 有新版本: %s（当前 %s）", name, new_version, current_version
        )
        return data
    return None


def notify_cli_update(config_value: Optional[bool] = None) -> bool:
    """
    检查并在有新版本时提示用户。

    提示方式优先级：Windows 桌面通知（需 winotify）> 终端一行提示。
    无论哪种方式失败都不抛异常。

    :return: 确实提示了新版本返回 True；否则 False
    """
    data = check_cli_update(config_value)
    if not data:
        return False

    new_version = str(data.get("version") or "")
    download_url = str(data.get("download_url") or "")
    changelog_url = str(data.get("changelog_url") or "")
    message = t(
        "update.new.cli_version.message",
        old_version=CLI_VERSION, new_version=new_version
    )

    if _send_toast(message, download_url, changelog_url):
        LOGGER.debug("已弹出桌面通知")
        return True

    # 降级：终端提示（不写 stdout，避免污染命令输出管道）
    import sys

    print(t("update.new.version.title"), file=sys.stderr)
    print(f"  {message}", file=sys.stderr)
    if download_url:
        print(f"  {t('update.new.version.download')}: {download_url}",
              file=sys.stderr)
    if changelog_url:
        print(f"  {t('update.new.version.content')}: {changelog_url}",
              file=sys.stderr)
    return True


def _send_toast(
        message: str,
        download_url: str,
        changelog_url: str
        ) -> bool:
    """
    发送 Windows 桌面通知（可选能力）。

    `winotify` 未安装时静默返回 False，由调用方降级为终端提示，
    CLI 核心不因此产生硬依赖。

    :return: 通知已发出返回 True；不可用或失败返回 False
    """
    try:
        from winotify import Notification, audio
    except ImportError:
        LOGGER.debug("未安装 winotify（可选依赖），改用终端提示")
        return False

    try:
        toast = Notification(
            app_id=APP_ID,
            title=t("update.new.version.title"),
            msg=message,
        )
        if download_url:
            toast.add_actions(
                label=t("update.new.version.download"), launch=download_url
            )
        if changelog_url:
            toast.add_actions(
                label=t("update.new.version.content"), launch=changelog_url
            )
        toast.set_audio(audio.Mail, loop=False)
        toast.show()
        return True
    except Exception as error:  # noqa: BLE001 —— 通知失败不影响 CLI
        LOGGER.debug("桌面通知发送失败: %s", error)
        return False
