"""Published setup instructions must describe commands the release contains."""

from pathlib import Path


README = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")


def test_ggrequestz_points_at_romarr_until_the_standalone_receiver_ships():
    assert "released ROM Hub CLI does **not** include a `webhook` command" in README
    assert "REQUEST_WEBHOOK_URL=http://romarr:6868/api/v1/webhook/ggrequestz" in README
    assert "System → GG Requestz requests" in README
    assert "do not follow examples using `rom-hub webhook serve`" in README
