# pcenergy

A light Linux tool that records the PC's energy use (CPU, GPU, and an estimate for the rest of the system),
keeps the history, and shows it per month, week and day in an interactive terminal UI with colored graphs.
A small read-only HTTP API exposes the same data for widgets and web pages.

> Work in progress. Full install and usage docs follow.

## Development

```bash
uv sync --all-extras
uv run pcenergy --help
uv run pytest
```

## License

MIT
