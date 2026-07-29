import pytest
from pydantic import ValidationError

from romm_hub.types import FetchFile, FetchPlan


def test_minimal_plan():
    p = FetchPlan(
        files=[FetchFile(url="https://archive.org/download/x/g.zip", filename="g.zip")],
        platform="dos",
    )
    assert p.files[0].filename == "g.zip"
    assert p.collection is None


def test_plan_requires_at_least_one_file():
    with pytest.raises(ValidationError):
        FetchPlan(files=[], platform="dos")


@pytest.mark.parametrize(
    "evil",
    ["../escape.zip", "a/b.zip", "a\\b.zip", "/abs.zip", "..", "", "."],
)
def test_filename_must_be_a_bare_name(evil):
    """A plugin must not be able to steer the host's writes with a filename."""
    with pytest.raises(ValidationError):
        FetchFile(url="https://archive.org/x", filename=evil)


def test_negative_size_rejected():
    with pytest.raises(ValidationError):
        FetchFile(url="https://archive.org/x", filename="g.zip", size_bytes=-1)
