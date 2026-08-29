import requests

from packaging import version
from winotify import Notification, audio

from .i18n.i18n import t
from .logger import get_logger
from .config import CLI_VERSION, ConfigManager


# 官方接口
OFFICIAL_URL = ""

# 消息的app_id
APP_ID = "XDclassmate-CLI"

logger = get_logger("update")

cfg = ConfigManager()

# 全局开关
USE_UPDATE_MESSAGE = cfg.load_config("update_message")
AUTO_UPDATA = cfg.load_config("auto_update")

def send_update_cli_message():
    data = get_last_cli_info()
    new_version = data["version"]
    if new_version is None:
        return
    logger.info(f"当前版本: {CLI_VERSION}, 新版本：{new_version}")
    if version.parse(new_version) < version.parse(CLI_VERSION):
        logger.error("wtf, 什么情况???")
        return
    if version.parse(new_version) == version.parse(CLI_VERSION):
        logger.info("当前CLI版本已为最新版.")

    # 新版的数据
    download_url = data["download_url"]
    changelog_url = data["changelog_url"]
    
    logger.info("发现新版本.")
    toast = Notification(
        app_id=APP_ID,
        title=t("update.new.version.title"),
        msg=t("update.new.cli_version.message", old_version=CLI_VERSION, new_version=new_version)
    )
    toast.add_actions(label=t("update.new.version.download"), launch=download_url)
    toast.add_actions(label=t("update.new.version.changelog"), launch=changelog_url)
    toast.set_audio(audio.Mail, loop=False)
    toast.show()
    logger.debug("已弹出弹窗.")

def get_last_cli_info() -> str | None:
    try:
        resp = requests.get(OFFICIAL_URL, timeout=10)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        info = resp.json()
        return info
    except (requests.RequestException, Exception) as e:
        logger.error(f"获取最新 CLI 信息失败: {e}")
        return None

send_update_cli_message()