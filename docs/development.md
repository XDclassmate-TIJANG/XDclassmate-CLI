# XDclassmate-CLI 开发文档

面向本项目的开发者（以及未来的自己），说明架构决策、模块职责、启动链路、
扩展方式与验证流程。用户侧使用说明请阅读 [README_cn.md](../README_cn.md)
（中文版）或 [README.md](../README.md)（English）。

约定：全部源码与注释使用中文；只使用 Python 标准库；严格 PEP 8
（79 列、4 空格缩进、两层空行），用 `py -3 tools/check_style.py` 自查。

---

## 1. 目录结构

```text
XD-CLI/
├── core/                     # 框架本体（微内核与各子系统）
│   ├── kernel.py             # 微内核：装配、启动、分发、REPL
│   ├── main.py               # 薄入口：解析参数后委托内核
│   ├── args.py               # 命令行头部全局开关（--lang/--log-level）
│   ├── command.py            # 命令空间树 + 命令/选项注册与解析
│   ├── builtins/             # 内置命令（system 空间）
│   ├── plugins.py            # 插件管理器：扫描、依赖、校验
│   ├── integrity.py          # 基于 URL 的完整性校验
│   ├── remote.py             # 远程仓库：从 INSTALL_URL 拉取并校验插件
│   ├── i18n/                 # 多语言：i18n.py + languages/*.json
│   ├── views.py              # list/tree/table 三种视图
│   ├── logger.py             # 统一日志（xdclassmate.*，stderr）
│   ├── config.py             # 配置管理（configs/config.json）
│   ├── event_bus.py          # 事件总线
│   └── exceptions.py         # 错误码异常体系
├── plugins/                  # 插件目录（目录插件与 .xdplug 共存）
│   └── image/                # 示例插件：图片尺寸查询
├── configs/config.json       # 运行配置（唯一配置文件）
├── hashes/                   # 期望摘要文件（url 校验的数据源）
├── tools/                    # 开发工具（标准库实现）
│   ├── check_style.py        # PEP 8 风格检查
│   ├── plugin_hash.py        # 摘要计算/回填
│   └── pack_plugin.py        # .xdplug 打包
├── tests/smoke_test.py       # 冒烟测试（54 项，无 pytest）
├── tests/e2e_install.py      # install/upgrade/uninstall 端到端验证
├── build/                    # 打包输出（.xdplug）
└── docs/                      # 文档
```

## 2. 启动链路

`py -3 -m core.main` 的完整路径：

```text
main.main()
  └── kernel.create_kernel(log_level=..., language=...)
        ├── parse_arguments()            # 头部识别 --lang/--log-level，其余透传
        ├── Kernel.__init__
        │     ├── ConfigManager()          # 读取 configs/config.json
        │     ├── setup_logging()         # 级别：命令行 > 配置 > 默认 INFO
        │     ├── detect_language()       # --lang > XDCLI_LANG > 配置 > 系统 > zh_CN
        │     └── set_global_i18n()       # 全局翻译函数可用
        └── kernel.boot()
              ├── register_system_commands()   # 内置命令 -> system 空间
              ├── get_plugins()               # 扫描 plugin_dir，拓扑排序加载
              ├── bus.emit("init_cli")         # 全局初始化事件
              └── bus.emit("plugin_init")     # 插件入口事件
        └── kernel.dispatch(arguments)
              ├── 有命令  -> run_once()         # 退出码 0/1/2
              ├── startup_mode == help -> print_help()
              └── 否则     -> repl()            # 提示符 xd>，exit/quit 退出
```

设计要点：

* **顺序不可变**：内置命令必须先于插件注册（插件入口可调用 help）；
  插件扫描必须先于 `plugin_init`（插件才有机会订阅事件）。
* **退出码**：`0` 成功 / `1` 框架异常（`XDclassmateCLIException`）/
  `2` 命令执行中的未预期异常。
* **错误输出**：框架异常经 i18n 翻译后输出；未预期异常输出原文并记录
  `LOGGER.exception` 完整堆栈。

## 3. 模块设计决策

### 3.1 微内核（core/kernel.py）

内核只持有各子系统的引用并定义流程，不含业务命令。内置命令
（`help`/`plugins`/`clear`/`about`）在 `core/builtins/system_commands.py`
中以与第三方插件相同的 `registry.register(...)` 通道注册到 `system`
空间。收益：内核启动路径可审计；替换内置实现（如更丰富的 help）无需
触碰内核。历史遗留：`core/command.py` 中的全局单例 `registry` 仍是
插件代码的默认注册入口，内核缺省复用该实例。

### 3.2 命令空间树（core/command.py）

* `CommandSpace` 树：`default` 为根，`system` 存内置命令；
* 解析优先级：每层**先匹配子空间、再匹配命令**；根空间未命中时回退
  `system` 空间（内置命令可裸调用）；
* 嵌套上限 `MAX_COMMAND_SPACE_DEPTH = 20`，创建（`register_command_space`）
  与解析（`resolve`）双重校验；
* 同空间重名抛 `XD-CLI-3002`，跨空间允许重名；
* 路径归一化：`"a/b"`、`"a b"`、`["a","b"]` 均可，`default` 前缀剥离。

### 3.3 选项机制（Option / CommandEntry）

`registry.register(...)` 返回 `CommandEntry`，`register_option(entry 或
路径, ...)` 声明选项。执行时解析为 **kwargs 传给命令函数：

* 值选项 `takes_value=True` 支持 `--name=X` 内联与 `-- X` 分隔取值；
* 开关选项命中为 `True`，未出现为 `False`（不是 None）；
* 命令未声明任何选项时保持宽松：token 原样作位置参数（向后兼容）；
* 一旦声明选项，未知选项/缺值/开关带值抛 `XD-CLI-3008`；
* 不支持短选项捆绑（`-nl`），保证语义无歧义；
* 视图主题 `help --theme` 即用该机制实现。

### 3.4 插件管理器（core/plugins.py）

* **惰性单例**：`get_plugins()` 首次调用才扫描，`set_plugins()` 可注入
  测试替身。禁止在导入期做任何 I/O（历史版本曾因模块级实例化在导入时
  扫描目录产生副作用，已修复）。
* **依赖处理**：`pre_plugins` 声明前置插件与版本；按拓扑排序加载，
  依赖不可满足时逐个剔除并循环重检，被剔除插件记录 ERROR 日志，
  不影响其余插件（历史版本曾因前置缺失直接抛异常炸掉整个 CLI，已修复）。
* **sys.path 注入**：插件根目录会临时加入 `sys.path`，解决插件内
  相对导入/子包导入问题；同时做模块遮蔽检测（插件内 `import tools`
  不会遮蔽标准库之外的仓库顶层包时才放行）。
* **容错**：单个插件清单/校验/入口失败只跳过该插件并记录日志。

### 3.5 完整性校验（core/integrity.py）

清单用 `url`（期望摘要所在文件）+ `algorithm`（默认 sha256）代替早期
直接内嵌 `hash` 的做法：

* `url` 支持 `http(s)://`、`file://`、本地绝对路径、相对项目根路径；
* `url: null` 跳过校验（本地开发场景）；
* 摘要文件支持裸摘要行与 `<摘要>  <文件名>`（sha256sum 风格）两种格式，
  用正则提取 8~128 位十六进制串；
* 摘要算法：按相对 POSIX 路径排序，逐文件写入「路径长度+路径+文件长度+
  内容」，清单自身与 `__pycache__` 排除；目录与 `.xdplug` 同算法；
* 读取摘要文件限制 64 KiB、网络请求 10 秒超时，防止异常数据拖垮启动。

### 3.6 插件管理（core/remote.py + 内置命令）

`install` / `upgrade` / `uninstall` 三个内置命令实现「仓库拉取 → 校验 → 落盘」：

* **仓库约定**：`install_url` 指向一个目录（支持 `http(s)://`、`file://`、
  本地路径），其下 `index.json` 列出每个插件的 `version`/`file`/`hash`；
  `hash` 可以是相对 `install_url` 的摘要文件路径，也可以是内联的 64 位
  十六进制摘要。
* **校验复用**：`core/remote.py` 复用 `integrity.content_digest` 与
  `parse_expected_digest` 计算并比对下载包的内容摘要，与本地插件加载
  走同一套算法，保证一致性；压缩包非法路径会被拒绝（`..`/绝对路径）。
* **install**：拉取索引 → 下载包 → 解压到临时目录算摘要 → 比对 →
  校验通过后解压到 `plugin_dir/<名称>/`；`install_url` 为空时直接中止并提示。
* **upgrade**：遍历 `index.json` 与已加载插件求交集，仅当仓库版本更新
  （`compare_versions` 比较）才下载安装；省略名称则升级全部。
* **uninstall**：优先用已加载插件记录的 `path` 定位，否则回退到
  `plugin_dir/<名称>/` 目录或 `plugin_dir/<名称>.xdplug` 文件后删除。
* **错误出口**：未配置 `install_url`、插件不在仓库、`XD-CLI-2004` 校验失败
  等均以 `XDclassmateCLIException` 带 `key` 抛出，由内核按当前语言翻译，
  命令本身不直接 print 错误信息——这是「补全 i18n」的核心约定。
* `config.py` 新增 `install_url` 键（默认空串），`configs/config.json` 同步。

### 3.7 多语言（core/i18n）

* 语言包：`core/i18n/languages/<语言代码>.json`，扁平键值对；
* 探测优先级：`--lang` > 环境变量 `XDCLI_LANG` > 配置 `language` >
  系统区域 > 默认 `zh_CN`；未知语言回退 `zh_CN` 并告警；
* `t(key, **kwargs)` 支持占位符（如 `{plugins}`）；
* 全局单例 `set_global_i18n()`/`t()` 供视图层与内置命令使用；
* 新增语言只需新增 JSON 文件，无需改代码。

### 3.7 日志（core/logger.py）

* 命名空间 `xdclassmate.*`，输出 **stderr**——用户可见内容走 print
  （stdout），诊断走 logger，两者严格分离；
* 级别链：命令行 `--log-level` > 配置 `log_level` > `INFO`；
* 配置 `log_file` 后额外写 UTF-8 文件；
* 注意 `config` ↔ `logger` 的历史循环导入问题：logger 内部对 config
  采用**延迟导入**，新增代码时保持该模式。

### 3.8 视图（core/views.py）

三种主题（list/tree/table）共用 `display_width()` 宽度计算：中日韩
字符按 2 列宽，保证 table 对齐与截断正确；非 UTF-8 终端自动回退
ASCII 连接线。渲染输入是 `registry.root`（CommandSpace 根），
说明文本经 i18n 的 `description_key` 按当前语言解析。

## 4. 编写插件

1. 新建目录 `plugins/<你的插件>/`，写 `xdclassmate.cli.setting.json`：

```json
{
  "name": "My Plugin",
  "entry": "main.py:main",
  "version": "1.0.0",
  "author": "you",
  "cli_version": "1.0",
  "description": "...",
  "events": ["plugin_init"],
  "pre_plugins": {},
  "url": null,
  "algorithm": "sha256"
}
```

2. `main.py` 中注册命令（`plugin_init` 事件触发时执行）：

```python
from core.command import registry
from core.i18n import t

def main():
    entry = registry.register("greet", cmd_greet)
    registry.register_option(entry, "-n", "--name", takes_value=True)

def cmd_greet(name="world"):
    print(t("myplugin.greeting", name=name))
```

3. 开发期 `url` 留 `null` 跳过校验；发布前执行：

```text
py -3 tools/plugin_hash.py plugins/<你的插件> --emit   # 生成 hashes/<名称>.hash256
# 把清单 url 改为 "hashes/<名称>.hash256"
py -3 tools/pack_plugin.py plugins/<你的插件> --update-hash
```

4. 第三方依赖（如 Pillow）一律**函数内延迟导入**并捕获 ImportError
   优雅降级，参考 `plugins/image/tools/size.py`。

## 5. 验证流程（生成-验证-修正）

任何改动后按顺序执行：

```text
py -3 tools/check_style.py       # 1. PEP 8：必须 0 问题
py -3 tests/smoke_test.py        # 2. 冒烟测试：54 项必须全过
py -3 -m core.main help -t table # 3. 端到端：视图正常、插件加载
```

修改插件内容后必须重算摘要（`--write` 或 `--update-hash`），
否则加载被拒绝（`XD-CLI-2004` / `PluginHashMismatchError`）。

## 6. 已知技术债

* `core/command.py` 全局单例 `registry` 仍在模块级导出，插件直接 import
  使用；长期应改为内核注入；
* `core/plugins.py` 的 `get_plugins()` 单例在测试间共享状态，测试需注意
  `set_plugins()` 替身还原；
* `core/builtins/system_commands.py` 的 `cmd_about` 等命令说明
  `description_key` 依赖语言包键，插件自身描述目前不做多语言
  （清单 `description` 为静态文本）；
* 冒烟测试中直接构造 `CommandRegistry()` 的用例需要手动调用
  `register_system_commands(registry)`（微内核化后的新约定）。

## 7. 历史决策记录

| 时间 | 决策 | 原因 |
| --- | --- | --- |
| v1.0 初期 | 移除 Typer，改 argparse | 机器无外网，坚持纯标准库 |
| 空间改造 | 命令空间树上限 20 层 | 防御错误注册导致无限嵌套 |
| hash → url | 摘要与插件本体分离 | 插件改动不必重打包即可更新摘要 |
| 微内核化 | 内置命令改为"内置插件" | 内核最小化，注册通道统一 |
| logger 延迟导入 config | 修复循环导入 | config 需要 setup_logging，反向依赖成环 |
| 头部识别全局开关 | argparse 不再拦截命令选项 | `-v/--opt` 应透传给命令函数 |
| 开关选项默认 False | 未出现为 False 而非 None | 布尔语义明确，命令函数可直接判断 |
