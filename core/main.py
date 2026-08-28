from .event_bus import bus
from .plugins import pl

def init_cli():
    """Initialize packaged plugins and broadcast the CLI startup event."""
    bus.emit("init_cli")
    bus.emit("plugin_init")

if __name__ == "__main__":
    init_cli()