import os
import logging
import datetime

logging.basicConfig(level=logging.INFO)

DEFAULT_LOG_DIR = "./logs"
LASTLOG_FILE = "last.log"

LOG_FILE_NAME_FORMAT = "xd-cli_{timestamp}.log"

def setup_logger(
        name: str = __name__,
        level: int = logging.INFO,
        log_file: str = LOG_FILE_NAME_FORMAT.format(timestamp=datetime.datetime.now().strftime("%Y%m%d_%H%M%S")),
        log_dir: str = DEFAULT_LOG_DIR,
        last_log: bool = True,
        last_log_file: str = LASTLOG_FILE,
        format: str = '[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
        ) -> logging.Logger:
    """
    设置日志记录器。
    :param name: 日志记录器名称
    :param level: 日志级别
    :param log_file: 日志文件路径
    :param log_dir: 日志目录
    :param last_log: 是否记录最后的日志
    :param last_log_file: 最后日志文件路径
    :param format: 日志格式
    :return: 配置好的日志记录器

    如果开启了 last_log, 程序会将日志写入 last_log_file，
    程序推出后不会转移到新的日志文件。
    程序下一次开启时，如果 log_dir 下的 last_log_file 有内容, 
    则会将其转移到 log_dir 下的 log_file 中。
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 创建控制台处理器并设置级别
    ch = logging.StreamHandler()
    ch.setLevel(level)

    # 创建格式化器并添加到处理器
    formatter = logging.Formatter(format)
    ch.setFormatter(formatter)

    # Last log 处理器
    if last_log:
        last_log_path = f"{log_dir}/{last_log_file}"
        # 确保日志目录存在
        import os
        os.makedirs(log_dir, exist_ok=True)
        last_fh = logging.FileHandler(last_log_path)
        last_fh.setLevel(level)
        last_fh.setFormatter(formatter)
        logger.addHandler(last_fh)

    # 如果 last_log 文件存在且不为空，则将其内容转移到新的日志文件中
    if last_log and os.path.exists(last_log_path) and os.path.getsize(last_log_path) > 0:
        new_log_path = f"{log_dir}/{log_file}"
        with open(last_log_path, 'r', encoding='utf-8') as last_log_fh, open(new_log_path, 'a', encoding='utf-8') as new_log_fh:
            new_log_fh.write(last_log_fh.read())

    return logger

def get_logger(name: str = __name__) -> logging.Logger:
    """
    获取日志记录器。
    :param name: 日志记录器名称
    :return: 日志记录器
    """
    return logging.getLogger(name)