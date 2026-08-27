"""Published setup instructions must describe commands the release contains."""

from pathlib import Path


README = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")


def test_ggrequestz_points_at_romarr_until_the_standalone_receiver_ships():
    assert "released ROM Hub CLI does **not** include a `webhook` command" in README
    assert "REQUEST_WEBHOOK_URL=http://romarr:6868/api/v1/webhook/ggrequestz" in README
    assert "System → GG Requestz requests" in README
    assert "rom-hub webhook serve" not in README
    assert "rom-hub webhook url" not in README
    assert "ROM_HUB_WEBHOOK_TOKEN" not in README
    assert "No listening socket in the released CLI" in README
