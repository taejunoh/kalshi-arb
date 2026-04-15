import pytest

pytestmark = pytest.mark.skip(
    reason="shutdown_fade V1 deprecated — see docs/future-work/shutdown-fade-redesign.md"
)


def test_deprecated_instantiation_raises():
    """Placeholder — V1 tests are skipped; redesign will add new tests."""
    pass
