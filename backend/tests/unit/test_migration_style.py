from pathlib import Path


def test_migrations_do_not_use_runtime_model_metadata() -> None:
    versions_dir = Path(__file__).resolve().parents[2] / "migrations" / "versions"
    migration_sources = [path.read_text() for path in versions_dir.glob("*.py")]

    forbidden_patterns = (
        "Base.metadata.create_all",
        "Base.metadata.drop_all",
        "import app.models",
    )

    for pattern in forbidden_patterns:
        assert all(pattern not in source for source in migration_sources), (
            f"Found forbidden migration pattern: {pattern}"
        )
