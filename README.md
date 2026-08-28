# XDclassmate-CLI

**English** | [简体中文](README_cn.md)

XDclassmate-CLI is a Python CLI framework built on a **micro-kernel + event bus + plugin** architecture. It depends only on the Python standard library and natively supports command spaces, command options, internationalization (i18n), and URL-based plugin integrity verification. A plugin can be either a directory or a `.xdplug` ZIP archive.

## Quick Start

```text
py -3 -m core.main                          # Enter interactive REPL (prompt: xd>)
py -3 -m core.main <command> [args]         # Run a single command, then exit
py -3 -m core.main [<space>...] <command> [options]  # Call with command spaces
py -3 -m core.main --version                # Show CLI version
py -3 -m core.main --lang en_US help        # Show help in English
py -3 -m core.main --log-level DEBUG help   # Show help with DEBUG logging
```

Behavior when launched with no arguments is controlled by the `startup_mode` config key (see "Configuration" below): `repl` enters interactive mode, `help` prints the command view and exits.

## Architecture (Micro-Kernel)

`Kernel` in `core/kernel.py` is the single assembly point. It does exactly four things:

1. **Assemble**: config → logging → i18n → command registry → plugin manager;
2. **Boot**: load built-in commands → load plugins → broadcast `init_cli` / `plugin_init` in order;
3. **Dispatch**: hand user input to the command registry, handle exceptions and exit codes uniformly;
4. **Interact**: enter the REPL or print help according to the startup mode.

The boot order is fixed: `load_builtins → load_plugins → emit(init_cli) → emit(plugin_init)`. Built-in commands register before plugins so that plugin entries can call `help`; plugin scanning finishes before `plugin_init` so plugins can subscribe to events.

The kernel itself contains **no commands** — `help` / `plugins` / `clear` / `about` are registered in the `system` space as "built-in plugins" (`core/builtins/system_commands.py`), going through exactly the same registration channel as third-party plugins. Exit code convention: `0` success / `1` framework exception / `2` command execution exception.

Module responsibilities:

| Module | Responsibility |
| --- | --- |
| `core/kernel.py` | Micro-kernel: assembly, boot, dispatch, REPL |
| `core/command.py` | Command space tree; command/option registration and parsing |
| `core/plugins.py` | Plugin scanning, dependency topological sort, manifest validation |
| `core/integrity.py` | URL-based plugin content integrity verification |
| `core/i18n/` | i18n (language packs + detection + placeholder translation) |
| `core/views.py` | Three command views: list / tree / table |
| `core/logger.py` | Unified logging `xdclassmate.*` (stderr) |
| `core/event_bus.py` | Event bus (init_cli / plugin_init) |
| `core/config.py` | Config read/write (configs/config.json) |
| `core/args.py` | Global flag parsing at the head of the command line |
| `core/exceptions.py` | Error-coded exception hierarchy |
| `core/builtins/` | Built-in commands (system space) |

## Plugin Format

Every plugin root directory must contain `xdclassmate.cli.setting.json` declaring the following fields:

```json
{
  "name": "Image Processing",
  "entry": "main.py:main",
  "version": "1.0.0",
  "author": "XDclassmate",
  "cli_version": "1.0",
  "description": "A tool for processing images.",
  "events": ["plugin_init"],
  "pre_plugins": {},
  "url": "hashes/image.hash256",
  "algorithm": "sha256"
}
```

The plugin entry must be a callable in `module.py:function` format as declared in the manifest. The loader validates manifest fields, versions, pre-plugins, content integrity, and archive paths; a plugin that fails validation is rejected. `pre_plugins` declarations form a dependency graph loaded in topological order — a single failing plugin is skipped with an ERROR log and does not affect the rest of the CLI.

The default plugin directory is `plugins` under the current working directory; it can be changed via `plugin_dir` in `configs/config.json`. Directory plugins and `.xdplug` archives can coexist; for same-named plugins, only the first one scanned takes effect (sorted by name).

### Integrity Verification (url)

`url` points to the file that holds the **expected digest**. It supports `http(s)://`, `file://`, local absolute paths, and paths relative to the project root. A `url` of `null` skips verification (local development/testing). The algorithm is set by the manifest `algorithm` field (default `sha256`) and can also be inferred from the URL file name suffix (`.hash256` / `.sha512` / `.md5`, etc.).

Digest files support two formats:

```text
6d5edd2c1241c5a151cc5d34433e5f5b0cfd13f4e896fb578af7fed7e8421766        # bare digest
6d5edd2c1241c5a151cc5d34433e5f5b0cfd13f4e896fb578af7fed7e8421766  image  # sha256sum style
```

The repository ships an example: `plugins/image` (image size query with graceful degradation when Pillow is missing) plus `hashes/image.hash256`.

### Packing `.xdplug` and Maintaining Digests

```text
py -3 tools/plugin_hash.py plugins/image --emit        # Generate hashes/<name>.hash256
py -3 tools/plugin_hash.py plugins/image --write       # Write digest back per manifest url
py -3 tools/pack_plugin.py plugins/image --update-hash # Pack into build/<name>-<version>.xdplug
```

`--update-hash` refreshes the digest before packing, preventing rejection caused by forgetting to sync; `__pycache__` and `.pyc` are excluded automatically. Drop the generated `.xdplug` into the plugin directory to load it — its manifest validation, integrity checks, and path safety checks are identical to directory plugins.

Digest algorithm: sort by relative POSIX path, then write path length, path, file length, and file content for each file; the manifest itself and `__pycache__` are excluded. Directories and `.xdplug` use the same algorithm.

## Command Spaces (commandspace)

Commands are organized as a tree. `default` is the root space and the default value of `commandspace`.

```text
hello                                  # Root-space command: no space prefix needed
space1 command1 [args...]               # Single-level space
space1 space2 space3 command1 --opt 1   # Deeply nested space; options pass through
```

Rules:

* **Optional space**: registering without `commandspace` puts the command into `default`, callable without a prefix;
* **Arbitrary nesting**: spaces stack level by level; at each level the resolver first matches sub-spaces, then commands in the current space;
* **Nesting limit**: at most 20 explicit levels (`MAX_COMMAND_SPACE_DEPTH`, root not counted); exceeding raises `XD-CLI-3005`;
* **Duplicate names allowed**: commands in different spaces may share a name; only duplicates within the same space raise an error (`XD-CLI-3002`);
* **System command fallback**: built-in commands (`help`/`plugins`/`clear`/`about`) live in the `system` space and are found via fallback when lookup in the root space fails — so `help` equals `system help`;
* **Path syntax**: the API accepts `"space1/space2"`, `"space1 space2"`, or `["space1", "space2"]`; a leading `default` is normalized away.

Registering commands from a plugin:

```python
from core.command import registry

def main():                       # Manifest entry points here; runs on plugin_init
    registry.register_command_space("space1/space2/space3")   # Create three levels at once
    registry.register("command1", handler)                     # Goes into default, callable bare
    registry.register("command1", handler2, commandspace="space1/space2")
```

## Command Options (Option)

Declare options with `register_option`; at execution time they are parsed into **keyword arguments** passed to the command function:

```python
def main():
    entry = registry.register("greet", cmd_greet)          # Returns a CommandEntry
    registry.register_option(entry, "-n", "--name",
                             takes_value=True, default="world", help="Who to greet")
    registry.register_option(entry, "-l", "--loud", help="Uppercase the output")

def cmd_greet(name="world", loud=False):     # option -> same-named kwarg
    print(f"Hello, {name}!".upper() if loud else f"Hello, {name}!")
```

```text
greet                      # Hello, world! (default value)
greet -n XD --loud         # HELLO, XD!
greet --name=XD            # Inline = value supported
greet -- -not-option       # Everything after -- is a positional argument
```

Rules:

* Option names must start with `-`/`--`; the kwarg name (dest) is derived from the long option, or set explicitly via `dest=`;
* Flag options (without `takes_value`) are `True` when present and default to `False` when absent;
* Short options, long options, and aliases are supported; short-option bundling (e.g. `-nl`) is not, to keep semantics unambiguous;
* Commands that declare **no options** stay permissive — every token is passed through as a positional argument (backward compatible). Once options are declared, unknown options, missing values, and flags with values raise `XD-CLI-3008`;
* The view theme `--theme` is itself implemented with this mechanism (`help -t tree`).

## Views (theme)

The `help` command switches between three rendering modes via `--theme/-t` (`core/views.py`):

```text
help                 # list: flat groups, each headed by the full space path
help --theme tree    # tree: hierarchical connectors (ASCII fallback on non-UTF-8 terminals)
help -t table        # table: space/command/description columns, auto-truncated
```

| Theme | Use case |
| --- | --- |
| `list` | Default; grouped by space, full paths, uniformly indented commands |
| `tree` | Inspecting nesting; commands first, sub-spaces after, aligned per level |
| `table` | Comparing many commands; aligned and truncated by display width (CJK counted as 2) |

## Internationalization (i18n)

Built-in commands and framework messages are all translated through `core/i18n`. Current language packs: `zh_CN`, `en_US` (`core/i18n/languages/`). Detection priority:

```text
--lang flag > XDCLI_LANG env var > language config > system locale > default zh_CN
```

Using translation in a plugin:

```python
from core.i18n import t

print(t("cli.title"))
print(t("cli.startup.repl_hint", plugins=2))   # Placeholders supported
```

To add a language, just drop a JSON pack into `core/i18n/languages/` — no code changes needed.

## Configuration

The config file is `configs/config.json` at the repository root:

```json
{
  "plugin_dir": "./plugins",
  "log_level": "INFO",
  "log_file": "",
  "startup_mode": "repl",
  "language": "zh_CN"
}
```

| Key | Description |
| --- | --- |
| `plugin_dir` | Plugin directory (relative to the working directory) |
| `log_level` | Log level: `DEBUG/INFO/WARNING/ERROR/CRITICAL`; `--log-level` on the command line takes precedence |
| `log_file` | Optional log file path (UTF-8); empty means no file logging |
| `startup_mode` | No-argument launch behavior: `repl` interactive mode / `help` print help and exit |
| `language` | UI language code, e.g. `zh_CN` / `en_US` |

## Logging

`core/logger.py` provides unified logging: loggers live in the `xdclassmate.*` namespace and write to stderr (so they never interfere with user-facing output on stdout), with optional file logging.

```python
from .logger import get_logger

LOGGER = get_logger("command")
LOGGER.debug("Executing command %s", path)
```

Every key stage — kernel assembly, plugin scanning, integrity verification, the REPL loop — is logged. Use `--log-level DEBUG` to observe the full boot chain.

## Error Definitions

All exceptions are defined in `core/exceptions.py`, uniformly inheriting from `XDclassmateCLIException` with an error code and structured context, translatable into the current language:

```text
[XD-CLI-3001] Command default/hello not found (command=default/hello)
```

| Segment | Scope | Exceptions (error code) |
| --- | --- | --- |
| 1xxx | Configuration | `ConfigException` (1000), `ConfigFileError` (1001) |
| 2xxx | Plugins | `PluginException` (2000), `PluginNotFoundError` (2001), `DuplicatePluginNamesError` (2002), `PluginManifestError` (2003), `PluginHashMismatchError` (2004), `PluginEntryError` (2005), `PluginArchiveError` (2006), `PluginVersionMismatchError` (2007), `PluginIntegrityError` (2008), `PluginDependencyError` (2009) |
| 3xxx | Commands | `CommandException` (3000), `CommandNotFoundError` (3001), `DuplicateCommandNamesError` (3002), `CommandSpaceNotFoundError` (3003), `DuplicateCommandSpaceNamesError` (3004), `CommandSpaceDepthExceededError` (3005), `InvalidCommandSpaceNameError` (3006), `CommandExecutionError` (3007), `CommandArgumentException` (3008), `DuplicateOptionNamesError` (3009) |

A single `except XDclassmateCLIException` catches every framework exception. Non-framework exceptions raised inside command functions are wrapped as `CommandExecutionError`; argument mismatches are wrapped as `CommandArgumentException`; the original exception is always preserved in `__cause__`.

## Testing and Code Style

```text
py -3 tests/smoke_test.py      # Smoke test: 54 checks
py -3 tools/check_style.py     # PEP 8 style check (standard library only)
```

The smoke test covers space path normalization, nested resolution, duplicate-name rules, the 20-level limit, command CRUD and migration, option parsing, all three views, the exception hierarchy, the logging system, plus directory and `.xdplug` plugin loading with integrity verification.

The style check covers common PEP 8 issues — line width (79 columns), tab indentation, trailing whitespace, consecutive blank lines, blank lines before top-level definitions, and spacing after commas — while skipping string literals and comments to avoid false positives. The entire codebase follows PEP 8 without third-party formatters.

For detailed architecture notes, module design decisions, and development guides, see [docs/development.md](docs/development.md) (Chinese).

The project uses only the Python standard library and contains no third-party code or copyright-restricted resources.
