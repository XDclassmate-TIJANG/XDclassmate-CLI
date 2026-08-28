# XDclassmate-CLI

XDclassmate-CLI 是一个由事件总线和插件组成的 Python CLI 原型。插件可以是插件目录，也可以是 `.xdplug` ZIP 压缩包。

## 插件格式

插件根目录必须包含 `xdclassmate.cli.setting.json`，并声明以下字段：

```json
{
  "name": "example",
  "entry": "main.py:main",
  "version": "1.0.0",
  "author": "Author",
  "cli_version": "1.0",
  "description": "Example plugin",
  "events": ["plugin_init"],
  "pre_plugins": {},
  "hash": "sha256-of-plugin-content"
}
```

`hash` 是插件内容的 SHA-256：按相对 POSIX 路径排序，逐个写入路径长度、路径、文件长度和文件内容；清单文件自身和 `__pycache__` 不参与计算。目录和 `.xdplug` 使用相同算法。

插件入口必须是清单中 `module.py:function` 格式的可调用对象。加载器会校验清单字段、版本、前置插件、内容 hash 和压缩包路径，校验失败则拒绝加载。

默认插件目录是当前工作目录下的 `plugins`，可在 `core/configs/config.json` 中通过 `plugin_dir` 修改。目录插件与 `.xdplug` 压缩包插件可以共存；同名插件只有先被扫描到的那个会生效（按名称排序）。

### 打包 `.xdplug`

```text
py -3 tools/plugin_hash.py --write plugins/hello      # 重算并回填清单 hash
py -3 tools/pack_plugin.py plugins/hello --update-hash # 打包到 build/hello-1.0.0.xdplug
```

`--update-hash` 会在打包前先回填 hash，避免忘记同步导致加载被拒绝；`__pycache__` 与 `.pyc` 会自动排除。把生成的 `.xdplug` 放进插件目录即可加载，其内部清单、hash 校验、路径安全检查与目录插件完全一致。

## 命令空间（commandspace）

命令以树形结构组织，`default` 是根空间，也是 `commandspace` 的缺省值。

```text
hello                                  # 根空间命令：无需写空间名
space1 command1 [参数...]               # 单层空间
space1 space2 space3 command1 --opt 1   # 多层嵌套空间，选项原样传给命令函数
```

规则：

* **可选空间**：注册时不传 `commandspace` 即进入 `default`，调用时无需前缀；
* **任意嵌套**：空间可逐层叠加，解析时每层优先匹配子空间，再匹配当前空间的命令；
* **嵌套上限**：最多 20 层显式空间（`MAX_COMMAND_SPACE_DEPTH`，根空间不计入），超限抛 `XD-CLI-3005`；
* **允许重名**：不同空间内的命令可以同名，只有同一空间内重名才报错（`XD-CLI-3002`）；
* **系统命令回退**：`system` 空间存放内置命令（`help`/`plugins`/`echo`/`clear`），在根空间找不到时会回退查找，因此 `help` 与 `system help` 等价；
* **路径写法**：API 中可接受 `"space1/space2"`、`"space1 space2"` 或 `["space1", "space2"]`，以 `default` 开头会被自动归一。

插件注册命令示例：

```python
from core.command import registry

def main():                       # 清单 entry 指向本函数，plugin_init 时执行
    registry.register_command_space("space1/space2/space3")   # 一次创建三层
    registry.register("command1", handler)                     # 进入 default，裸调用
    registry.register("command1", handler2, commandspace="space1/space2")
```

## 运行

```text
py -3 -m core.main                          # 进入交互式 REPL（提示符 xd>）
py -3 -m core.main <命令> [参数]             # 单次执行一条命令后退出
py -3 -m core.main [<空间>...] <命令> [选项] # 带命令空间的调用
py -3 -m core.main --version                # 显示 CLI 版本
```

从第一个非开关 token 开始的内容（含 `-v`、`--opt`）原样透传给命令处理函数；`-h/--help`、`-V/--version` 只在命令行最前面生效。REPL 中输入 `exit` 或 `quit` 退出。

仓库附带两个示例插件：`plugins/hello`（同名命令跨空间注册）与 `plugins/space-demo`（三层嵌套空间演示）。

## 错误定义

所有异常定义在 `core/exceptions.py`，统一继承 `XDclassmateCLIException`，自带错误码与结构化上下文：

```text
[XD-CLI-3001] 命令 default/hello 未找到 (command=default/hello)
```

| 分段 | 范围 | 代表异常 |
| --- | --- | --- |
| 1xxx | 配置 | `ConfigException`、`ConfigFileError` |
| 2xxx | 插件 | `PluginManifestError`、`PluginHashMismatchError`、`PluginEntryError`、`PluginArchiveError`、`PluginVersionMismatchError`、`PluginNotFoundError`、`DuplicatePluginNamesError` |
| 3xxx | 命令 | `CommandNotFoundError`、`DuplicateCommandNamesError`、`CommandSpaceNotFoundError`、`DuplicateCommandSpaceNamesError`、`CommandSpaceDepthExceededError`、`InvalidCommandSpaceNameError`、`CommandExecutionError` |

捕获时只需 `except XDclassmateCLIException` 即可兜底全部框架异常；命令函数抛出的非框架异常会被包装为 `CommandExecutionError`，原始异常保留在 `__cause__` 中。单个插件校验失败只会跳过该插件并在 stderr 打印，不影响其余插件与 CLI 运行。

## 测试

```text
py -3 tests/smoke_test.py
```

标准库实现的冒烟测试，覆盖空间路径归一化、嵌套解析、重名规则、20 层上限、命令增删改迁移、异常体系，以及目录插件与 `.xdplug` 压缩包插件的加载和 hash 校验。

项目只使用 Python 标准库，不包含第三方代码或受版权限制的资源。
