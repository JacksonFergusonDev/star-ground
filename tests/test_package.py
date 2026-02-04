def test_package_structure_is_acyclic() -> None:
    """
    Verifies that src.bom_lib can be imported without circular dependency errors.
    """
    import src.bom_lib

    # Basic check to ensure it's a valid package
    assert hasattr(src.bom_lib, "__path__")

    # Check that key functions are actually exposed
    assert hasattr(src.bom_lib, "parse_pedalpcb_pdf")
    assert hasattr(src.bom_lib, "calculate_net_needs")
