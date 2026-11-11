def test_package_structure_is_acyclic() -> None:
    """
    Verifies that the package structure is sound and free of circular imports.

    Ensures that `star_ground.bom_lib` can be imported and that key public API functions
    (like `parse_pdf` and `calculate_net_needs`) are correctly exposed.
    """
    import star_ground.bom_lib

    # Basic check to ensure it's a valid package
    assert hasattr(star_ground.bom_lib, "__path__")

    # Check that key functions are actually exposed
    assert hasattr(star_ground.bom_lib, "parse_pdf")
    assert hasattr(star_ground.bom_lib, "calculate_net_needs")
