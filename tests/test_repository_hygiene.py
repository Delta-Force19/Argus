from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_only_package_markers_are_empty() -> None:
    empty_files = sorted(
        path.relative_to(PROJECT_ROOT).as_posix()
        for root in (
            PROJECT_ROOT / "argus",
            PROJECT_ROOT / "docs",
            PROJECT_ROOT / "tests",
        )
        for path in root.rglob("*")
        if path.is_file()
        and path.stat().st_size == 0
    )

    assert empty_files
    assert all(
        path.endswith("/__init__.py")
        for path in empty_files
    )
