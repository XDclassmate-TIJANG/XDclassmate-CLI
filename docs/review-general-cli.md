# XD-CLI 通用型 CLI 评审报告

> 评审视角：把 XD-CLI 当作**面向第三方开发者的通用命令行框架**（对标 Click / Typer / Cobra / kubectl 插件生态），而非"某个项目自带的命令行工具"。
> 评审基准：当前工作区代码（`046cdf4` 之后的本地改动），含实测复现。

---

## 0. 结论摘要

**架构骨架是对的，甚至比大多数自研 CLI 更清晰**：事件总线解耦、命令空间树（commandspace）、插件热插拔、带错误码的异常体系、统一日志、可切换视图——这套组合已经超出"原型"水准，具备成为框架的地基。

**但按通用型 CLI 的标准，目前卡在"能跑"到"能用"之间**，缺四块门槛能力：

| 缺失 | 后果 |
| --- | --- |
| 声明式参数模型（类型/必填/数量/校验） | 每个命令都要手写解析与校验，错误消息还会暴露内部函数名 |
| 帮助与补全体系（per-command `--help`、usage、shell completion） | 第三方无法自助了解命令，只能靠 `help` 翻列表 |
| 插件生命周期（依赖拓扑、卸载/重载、依赖声明、隔离） | 一个插件的依赖写错，整个 CLI 崩溃（已实测） |
| 分发与工程化（打包入口、配置分层、可测试性） | 只能 `python -m core.main` 在源码目录里跑 |

此外有 **4 个当前就能复现的阻断问题**（见第 1 节），建议先修再谈演进。

---

## 1. 阻断问题（P0，建议立即修复）

### P0-1 插件内部模块无法导入，导致插件必然加载失败

`plugins/image/main.py` 中 `from tools.size import cmd_size` 直接失败：

```
File "plugins/image/main.py", line 9, in <module>
    from tools.size import cmd_size
ModuleNotFoundError: No module named 'tools.size'
```

两个叠加原因：

1. 加载器用 `spec_from_file_location` 把入口当成**顶层模块**执行，插件根目录不在 `sys.path`，所以插件内的子包（`tools/`）不可导入；
2. 更糟的是 `tools` 会被解析到**项目根目录的 `tools/`**（放 `plugin_hash.py` 的那个），即插件子包名与宿主包名撞车——`plugins/image/tools` 与 `./tools` 同名，属于命名空间污染。

**影响**：插件只能写成单文件，无法拆包；插件作者踩坑且报错信息不友好。这是"能不能写插件"的问题，不是"写得好不好"的问题。

**建议**：

* 加载时把插件根目录临时加入 `sys.path`，并把插件模块注册到独立命名空间（如 `xdclassmate.plugin.<plugin_name>.*`），彻底避免与宿主/其他插件撞名；
* 或要求插件入口使用相对导入（`from .tools.size import ...`），由加载器以**包**的方式加载（`spec.submodule_search_locations`）；
* 加载失败时把原始 `ModuleNotFoundError` 透出到错误消息（现在只有一句"入口加载失败"，看不到真实原因）。

### P0-2 前置插件缺失 → 整个 CLI 崩溃，而非"跳过该插件"

实测（临时插件声明 `pre_plugins: {"missing-dep": "1.0"}`，该插件不存在）：

```
结果: 抛出 PluginNotFoundError: [XD-CLI-2001] 插件 needs-dep 的前置插件 missing-dep 未找到
```

异常从 `Plugins.__init__` 抛出；而 `pl = Plugins()` 在 `core/plugins.py` **导入时**执行 → 直接以 traceback 终止进程。

这与 README 承诺的"单个插件校验失败只跳过该插件"相悖：**一个插件的依赖写错，会让整个 CLI 无法启动**（包括 `help`）。

**建议**：把依赖校验放进 `load_plugins` 的 per-plugin try 块内，或校验失败时移除依赖方并记录错误，保证 CLI 始终可启动。

### P0-3 根目录 `configs/config.json` 是"幽灵配置"

根目录存在 `configs/config.json`（内容只有 `plugin_dir`），但**没有任何代码读取它**——真正生效的是 `core/configs/config.json`。任何人改了根目录这份都不会生效，且极难排查。

**建议**：删除，或明确它为用户级配置并真正实现"项目配置 ← 用户配置 ← 环境变量 ← 命令行"的分层覆盖。

### P0-4 当前工作区零个可用插件

`plugins/` 下只剩 `image`（且处于加载失败状态），原 `hello`、`space-demo` 已删除；`plugins` 命令输出"已加载 0 个插件"。加上系统命令里 `echo` 已被移除，目前实际可用命令只有 `help / plugins / clear / about` 四个。

**建议**：至少保留一个可运行的示例插件作为"冒烟基准"，并在 CI/冒烟测试里断言它可加载（现有测试用临时目录构造插件，覆盖了加载器，但没覆盖仓库内真实插件）。

---

## 2. 与通用 CLI 框架的能力对照

| 维度 | 现状 | 评价 | 关键缺口 |
| --- | --- | --- | --- |
| 命令组织 | 命令空间树，20 层嵌套，跨空间重名 | ⚠️ 优秀但超前 | 空间名与命令名同层冲突时命令不可达；缺别名/隐藏命令 |
| 命令选项 | `register_option`，支持长短名/值/开关/内联 `--k=v`/`--` | ⚠️ 半成品 | 无类型转换、无必填、无 choices、无重复/追加语义、无 env 绑定 |
| 位置参数 | `*args` 字符串透传 | ❌ | 无类型/数量/必填声明，错误暴露内部函数名（见 §3-P1-1） |
| 帮助系统 | 全局 `help` + 三种视图 + 单命令详情 | ⚠️ | 无 per-command `--help`、无 usage 行、docstring 规范已出现不一致 |
| 补全 | 无 | ❌ | 无 bash/zsh/PowerShell 补全生成 |
| REPL | `input()` 循环 | ⚠️ | 无引号/转义解析（`shlex`）、无历史与行编辑、无多行、无 `!shell` |
| 全局选项 | `-h/-V/--log-level` | ⚠️ | 缺 `--config`、`-q/-v`、`--json`（结构化输出）、`--no-color` |
| 配置 | 单文件 JSON，键读取 | ⚠️ | 无分层、无 schema 校验、`plugin_dir` 相对 cwd、导入时即解析 |
| 插件加载 | 目录 + `.xdplug`，hash 校验、路径遍历防护 | ✅ 完成度高 | 见 P0-1/P0-2；无依赖声明、无卸载/重载、无隔离 |
| 插件依赖 | 仅"存在且版本相等"校验 | ❌ | 无拓扑排序（初始化顺序 = 文件名排序）、无版本范围 |
| 插件安全 | hash 防篡改 + 路径遍历防护 | ⚠️ | hash 可重算 ≠ 签名；无解压数量/体积上限（zip bomb）；入口代码导入即执行 |
| 错误体系 | 错误码 + 结构化 details | ✅ | 缺退出码规范；用户可见消息混入内部实现名 |
| 日志 | `xdclassmate.*`，stderr，可选文件 | ✅ | 缺 `--verbose/-q` 与日志级别的联动 |
| 输出层 | 命令直接 `print` | ❌ | 无 stdout/stderr 约定、无 TTY/颜色检测、无分页、无结构化输出 |
| 分发 | `python -m core.main` | ❌ | 无 `pyproject.toml`、无 console entry point、`core/` 无 `__init__.py`（依赖隐式命名空间包，打包时易踩坑） |
| 可测试性 | 54 项冒烟测试 | ⚠️ | `registry` / `pl` 为模块级单例且导入有副作用（实测在 `/tmp` 下凭空创建 `plugins/` 目录） |
| 国际化 | `core/i18n/` 仅有空文件与空 `languages/` 目录 | ❌ | 半成品；错误消息硬编码中文，尚未 key 化 |
| 异步/并发 | 同步事件总线 | ⚠️ | 长任务无法取消（无 SIGINT 处理），无 async 支持 |

---

## 3. 详细问题清单（按优先级）

### P1-1 参数模型缺失：错误消息暴露内部实现（实测）

```
xd> about "a b"
错误: [XD-CLI-3008] 命令 system/about 的参数不匹配:
CommandRegistry._register_system_commands.<locals>.cmd_about()
takes 0 positional arguments but 2 were given
```

三层问题：引号未解析（`shlex`）、参数个数未校验、错误文案把 `<locals>.cmd_about` 这种内部名字抛给用户。

**建议**：引入统一的 `Argument` 声明（与 `Option` 同构），携带 `type / required / nargs / default / help / metavar`；执行前做**声明式校验**，错误统一为"用法错误 + usage 行 + 期望格式"，并统一退出码 2。

### P1-2 帮助系统不成体系

* 无 per-command `--help`：`help --help` 报"不接受选项 --help"；
* 无 usage 行：用户不知道必填参数长什么样；
* docstring 规范出现分歧：`help` 仍是 `help [命令路径] [-t|--theme ...]：查看命令。`，而 `plugins/clear/about` 已改为纯描述句，混用会让 `help` 输出参差。

**建议**：命令注册时支持 `usage=` 与 `arguments=`；为每个命令自动注册 `--help` 选项；统一 docstring 规范（首行摘要，其余 `Args:`/`Options:` 分段）。

### P1-3 插件依赖只校验、不排序

初始化顺序 = 目录扫描顺序（按文件名排序），与依赖关系无关。`b` 依赖 `a` 恰好能用只因为 `a < b`；反过来就会在依赖方初始化之后才初始化被依赖方，且**不报错**——这类问题极难排查。

**建议**：按 `pre_plugins` 做拓扑排序后再注册 `plugin_init`；检测循环依赖并报专门错误码。

### P1-4 单例与导入副作用

`registry`（command.py）与 `pl`（plugins.py）都是模块级单例；`plugins.py` 在导入时即扫描并可能创建 `./plugins` 目录（实测：在 `/tmp` 下导入即生成 `/tmp/plugins`）。插件代码 `from core.command import registry` 直接绑定全局单例。

**后果**：无法在同一进程内创建两个独立 CLI 实例，无法做真正的隔离测试，库化（被其他程序嵌入）几乎不可能。

**建议**：抽象 `Cli` 对象持有 registry/plugins/config，单例仅作为默认入口保留；插件入口改为接收上下文参数（如 `def main(cli): ...`）或通过事件传递，避免插件直接 import 全局单例。

### P1-5 插件缺少依赖声明与隔离

`plugins/image` 依赖 PIL（本机已装 12.3.0），但清单里**没有任何地方声明第三方依赖**；所有插件共享同一个解释器环境，两个插件要求不同版本的同一库时必然冲突。同时这与项目"纯标准库"的约定产生了分歧——需要明确：这条约束是针对**内核**还是**插件**。

**建议**：清单增加 `dependencies`（如 `{"Pillow": ">=10"}`），加载时校验并给出可执行的安装提示；内核继续保持零依赖。

### P2 体验与工程化

* **配置分层缺失**：无环境变量/命令行/项目/用户四级覆盖，无 `--config` 指向其他配置文件，无 schema 校验。
* **退出码无约定**：目前只有 0/1/2 且未文档化，建议固定 0 成功 / 1 运行时错误 / 2 用法错误 / 130 中断。
* **输出层未抽象**：命令直接 `print`；建议提供 `ctx.echo()`（自动处理 TTY 颜色、`--json`、`-q`）。
* **打包缺失**：无 `pyproject.toml`、`core/` 缺 `__init__.py`；建议补上并暴露 `xd = "core.main:main"` 入口。
* **i18n 半成品**：`core/i18n/i18n.py` 为空、`languages/` 为空目录。要么落地（消息 key 化 + 语言包 + `--lang`），要么先移除，避免给人"已支持"的错觉。
* **安全增强**：hash 可重算，只能防意外改动不能防恶意伪造；可选支持签名/指纹白名单；解压时限制文件数与总体积（防 zip bomb）。
* **Windows 细节**：若后续引入颜色输出，需要启用 VT 模式（或提供 `--no-color`）；REPL 建议接入 `readline`/`pyreadline3` 以获得历史与行编辑。

---

## 4. 建议的演进路线（按阶段）

**阶段 1 · 修地基（先止血）**
修 P0-1～P0-4；依赖校验纳入 per-plugin 容错；拓扑排序 + 循环依赖检测；补 `core/__init__.py`；恢复一个可运行的示例插件并纳入冒烟测试。

**阶段 2 · 参数与帮助（通用 CLI 的门槛）**
`Argument` 声明式参数 + 类型转换 + 必填/数量校验；per-command `--help` 与 usage 行；统一"用法错误"文案与退出码；统一 docstring 规范。

**阶段 3 · 体验层**
REPL 用 `shlex` 解析 + 历史 + Tab 补全；全局选项 `--config / -v / -q / --json / --no-color`；输出层 `ctx.echo()`；shell 补全脚本生成（`xd completion bash|zsh|pwsh`）。

**阶段 4 · 生态层**
`Cli` 实例化的官方 API + 依赖注入；`plugin install / list / enable / disable / reload` 命令；依赖声明与校验；打包分发（pyproject + entry point）；i18n 落地或移除。

---

## 5. 定位建议：不要照搬 Click，要做"插件即命令空间"

Click/Typer 的装饰器模型已被充分占据，模仿没有优势。你手上有两个真正差异化的东西：

1. **命令空间树**——天然映射 `kubectl`/`git` 式的多级子命令，且跨空间重名共存是 Click 很难做到的；
2. **事件总线 + 热插拔插件**——接近 VS Code 扩展模型，适合做成"平台 + 插件市场"。

**建议把它写成一等设计并对外承诺**：插件名 = 顶层命令空间（`image` 插件下的命令一律挂在 `image` 空间），插件之间天然不会撞名；插件可以声明自己依赖哪些能力（事件）而非依赖具体实现。这条主线跑通后，XD-CLI 的定位就从"又一个 Python CLI 框架"变成"可扩展的命令平台"。

---

## 6. 立即可执行的修复清单

- [ ] 插件导入模型：插件根目录入 `sys.path` + 独立命名空间前缀（修 `image` 插件）
- [ ] 依赖校验纳入 per-plugin 容错，保证 CLI 永远能启动
- [ ] 依赖拓扑排序 + 循环依赖检测
- [ ] 删除或启用根目录 `configs/config.json`
- [ ] 恢复一个示例插件，并在冒烟测试中断言其可加载
- [ ] 补充 `core/__init__.py` 与 `pyproject.toml`
- [ ] REPL 改用 `shlex` 解析命令行
- [ ] 统一系统命令 docstring 规范（目前 `help` 与 `plugins/clear/about` 不一致）
- [ ] 明确第三方依赖约束的适用范围（内核 vs 插件）
- [ ] 决定 `core/i18n/` 的落地时间或移除
