"""
Tool of "Image Processing".

演示插件如何复用 CLI 自带的 i18n：
    * 通过 `from core.i18n import t, get_language` 取得全局翻译函数与语言；
    * 文本统一走语言包键（plugin.image.*），不再硬编码字符串；
    * `get_language()` 让插件随时获知当前界面语言（如做分支处理）。
"""

from typing import Optional

from core.i18n import get_language, t

# 依赖说明：本工具依赖第三方库 Pillow（pip install Pillow）。
# 采用函数内延迟导入，避免缺少依赖时整个插件无法加载。


def size(file: str) -> Optional[list]:
    """获取图片尺寸；缺少依赖或文件非法时返回 None。"""
    try:
        from PIL import Image
    except ImportError:
        print(t("plugin.image.depend_missing"))
        return None
    try:
        with Image.open(file) as img:
            width, height = img.size
            return [width, height]
    except (FileNotFoundError, OSError):
        return None


def cmd_size(file: str = ""):
    """size <图片路径>：输出图片的宽高。"""
    # 演示：插件可读取当前语言（此处仅取值，证明全局属性可用）
    language = get_language()
    info = size(file)
    if not info:
        print(t(
            "plugin.image.size.fail",
            file=file or t("plugin.image.no_path"),
        ))
        return
    print(t(
        "plugin.image.size.ok",
        file=file, width=info[0], height=info[1], lang=language
    ))
