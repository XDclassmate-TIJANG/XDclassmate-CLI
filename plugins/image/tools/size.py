"""
Tool of "Image Processing".
"""

from typing import Optional

# 依赖说明：本工具依赖第三方库 Pillow（pip install Pillow）。
# 采用函数内延迟导入，避免缺少依赖时整个插件无法加载。


def size(file: str) -> Optional[list]:
    """获取图片尺寸；缺少依赖或文件非法时返回 None。"""
    try:
        from PIL import Image
    except ImportError:
        print("缺少依赖 Pillow，请先执行: pip install Pillow")
        return None
    try:
        with Image.open(file) as img:
            width, height = img.size
            return [width, height]
    except (FileNotFoundError, OSError):
        return None


def cmd_size(file: str = ""):
    """size <图片路径>：输出图片的宽高。"""
    info = size(file)
    if not info:
        print(f"无法读取图片: {file or '(未指定路径)'}")
        return
    print(f"图片 {file} 的尺寸: {info[0]}x{info[1]}")
