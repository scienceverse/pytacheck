def test_package_exposes_version() -> None:
    import pytacheck

    assert pytacheck.__version__ == "0.1.0"
