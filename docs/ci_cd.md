# CI/CD & Deployment

The repository ships with a GitHub Actions workflow (`.github/workflows/ci.yml`)
that enforces quality gates on every push and pull request.

## Pipeline Stages

1. **Linting** – `ruff check loan_monitor tests` catches style and static
   analysis issues.
2. **Unit tests** – `pytest` runs the test suite across services, exchanges,
   hedging, metrics, and dashboard helpers.
3. **Documentation build** – `mkdocs build --strict` validates that the
   Markdown documentation compiles without warnings or broken links.
4. **Container build** – `docker build . -t loan-monitor:test` ensures the
   Dockerfile remains deployable.

The workflow targets Python 3.11. Adjust the matrix if you need to validate
against additional interpreters.

## Local Verification

Before opening a pull request you can replicate the workflow locally:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install ruff
ruff check loan_monitor tests
pytest
mkdocs build --strict
```

## Deployment

The provided Dockerfile bundles the Python package, configuration templates, and
entrypoint scripts. Build and run the container like so:

```bash
docker build -t loan-monitor:latest .
docker run --rm -p 9000:9000 -v "$PWD/config.yaml:/app/config.yaml" loan-monitor:latest
```

Expose `observability.metrics_port` from the container when you want Prometheus
scrapes. Mount configuration and database volumes to persist state across
restarts.

## Release Automation

Teams that want continuous deployment can extend the workflow with additional
jobs that publish the container image or upload packaged distributions. The
structured logging, metrics, and alert rules in `ops/` are designed to plug into
standard platform tooling without extra code changes.
