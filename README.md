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

默认插件目录是当前工作目录下的 `plugins`，可在 `core/configs/config.json` 中通过 `plugin_dir` 修改。

## 运行

```text
py -3 -m core.main
```

项目只使用 Python 标准库和现有的 Typer 示例代码，不包含第三方代码或受版权限制的资源。