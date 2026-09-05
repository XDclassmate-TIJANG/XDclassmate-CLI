# Changelog

> 注释：此文档在 **Alpha 1.10** 开始维护，此前的修改见 Commits。所有未发布条目按段号顺次递进；已发布版本后不再变动。

## Alpha 1.10

### 修复（Breaking Bugs）

- **插件 i18n 全线失效**：`core/plugins.py` 之前只识别 `{plugin_dir}/` 一种占位符，又对同一插件实例化两套 I18n，导致示例插件输出裸翻译键（如 `plugin.image.size.fail`）。
  - 新增 `strip_plugin_dir_placeholder()`，同时兼容 `{plugin_dir}/`、`%(plugin_dir)s/`、`${plugin_dir}/`、`/languages`、`languages` 五种写法，老清单不会因为占位符风格不同而被静默跳过。
  - `plugins/image` 改回走全局 `core.i18n.t`，消除双实例问题；`image size` 现在按当前语言输出中/英文。
  - 不再用 `ConfigManager` 反复读插件清单（性能 + 行为双重修正）。
- **摘要文件无法匹配**：`tools/plugin_hash.py` 之前摘要文件第二列固定写插件目录名，而 `core/remote.py` 按 `<名称>-<版本>.xdplug` 过滤，导致按 README 流程打包→安装必报 `XD-CLI-2008`。
  - `core/integrity.parse_expected_digest()` 在 filename 匹配不到时，回退到第一条有效摘要并提示警告。
  - `plugin_hash.py` 新增 `--label` 参数；`pack_plugin.py --update-hash` 默认把第二列写成目标 `.xdplug` 文件名，老摘要文件依然兼容。
- **退出码约定崩盘**：`CommandExecutionError`(3007) 是 `XDclassmateCLIException` 子类，被通用捕获分支先吃掉，文档中的"2 命令执行异常"永远到不了。
  - 内核 `run_once()` 改为：`0` 成功 / `XDclassmateCLIException` → `1` / 非框架异常 → `2`。
  - 新增 `coerce_exit_code()` 与 `EXIT_OK / EXIT_FRAMEWORK_ERROR / EXIT_EXEC_ERROR` 常量；**命令函数可返回整数作为退出码**（业务失败 `return 1` 现在能正确退 1）。
- **更新模块是地雷**：`core/update.py` 与 `core/network.py` 之前无人 import 但引入 `requests` / `packaging` / `winotify`，违反"纯标准库"承诺，且含模块级网络请求与多处崩溃点。
  - 全部重写：`core/network.py` 改用 `urllib`（无新依赖），新增 `core/version.py` 集中版本常量；`core/update.py` 接入启动流程但默认静默失败，失败不阻塞 CLI。
- **远端仓库与解压无安全防线**：
  - 新增 `_validate_relative_path()`：拒绝绝对路径（POSIX/Windows 盘符/UNC）、拒绝 `..` 逃逸段。
  - `SUPPORTED_ALGORITHMS` 白名单，未知算法直接 `4003`。
  - `.xdplug` 解压加配额：最多 4096 个条目、累计 ≤ 256 MiB、单条目 ≤ 32 MiB。
- **插件根与标准库同名隐患**：`sys.path.insert(0, root)` 永不移除，若插件根包含 `os`、`json` 等同名模块，会**遮蔽**标准库。
  - `_prepare_sys_path()` 加载前扫描插件根，若发现 stdlib 同名模块则打 WARNING 提醒。

### 改进（Ecosystem & DX）

- **工程化入口**：
  - 新增 `pyproject.toml`，提供 `xd` / `xdclassmate-cli` 入口；`xd help` = `python -m core.main help`。
  - 三个可选 extras：`pip install ".[image]"`（Pillow） / `.[test]`（pytest） / `.[dev]`（build, twine）。
- **多层级配置**：
  - 新增用户级配置路径（Windows `%APPDATA%\xdclassmate\config.json`，POSIX `~/.config/xdclassmate/config.json`），`pip install` 后无需仓库即可运行。
  - `XDCLI_CONFIG` 环境变量可临时覆盖路径。
  - 查找顺序：`$XDCLI_CONFIG` > 项目级 > 用户级。
- **插件身份多叫法兼容**：
  - 新增 `Plugins.find_by_path_alias()`：同时接受清单名（`Image Processing`）、目录名（`image`）、压缩包名（`image-1.0.0.xdplug`）。
  - `system/install`、`system/upgrade`、`system/uninstall` 默认按已装插件的 `path` 反查，不再要求插件名与目录名一致。
- **系统空间可改名**：`register_system_commands(..., system_space="core")` 不再因 `register_option` 硬编码 `"system/help"` 而崩溃——选项键名按入参空间变量拼装。
- **示例插件更稳**：`image size` 在依赖缺失时也能输出本地化错误；菜单命令缺失 Pillow 时给出显式降级提示。

### 新增（New Capabilities）

- **`return int` 作为退出码**：命令函数可以返回整数作为进程退出码，配合 `coerce_exit_code()` 自动归一化（返回 `True` 或省略视为 0）。
- **`coerce_exit_code()`**：把任意命令返回（`None`/`True`/数字/异常）映射到合法 [0,255] 范围。
- **`core/version.CLI_VERSION` 单一来源**：避免各处硬编码版本字符串。
- **`USER_DATA_DIR` / `user_config_path()`**：跨平台用户级数据根（`%APPDATA%/xdclassmate`、`~/.local/share/xdclassmate`、`~/.config/xdclassmate`）。

### 测试 / 质量门

- **新增 `tests/integration_test.py`（43 项）**：退出码、`return int` 通道、`languages_dir` 三种占位符、真实 `plugins/image` 加载并验证不再输出裸键、`plugin_hash` → `pack_plugin` → `remote.install_package` 全链路、远端路径校验、算法白名单、系统空间重命名、用户级配置路径解析。
- **冒烟测试**：54/54 仍全部通过。
- **端到端安装测试**：全部通过。
- **风格检查 (`tools/check_style.py`)**：从 19 处问题降为 **0 处**（修整缺换行、缺函数间隔、E302、F401 等历史遗留）。

### 文档

- `README.md` / `README_cn.md` 更新到与实现一致：用户级配置、`xd` 入口、退出码可被命令返回值覆盖、协议改为 **Apache-2.0**（与 `LICENSE` 一致，之前 `pyproject.toml` 错写为 MIT）。
- `CHANGELOG.md` 自本版本开始维护。

### 兼容性承诺

所有 P0 修复均**向后兼容**：旧插件（不规范 `languages_dir`）、旧摘要文件（第二列写目录名）、旧清单字段写法都可继续工作，新行为以 fallback 形式生效。仅当输入触及真正的安全底线（路径逃逸 / 未知摘要算法 / 压缩包超配额）时才会硬拒——拒绝时给出结构化错误码，可被外层脚本识别。
