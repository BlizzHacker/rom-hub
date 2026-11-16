"""Search GameBanana items."""

from rom_hub_sdk import SearchProvider, SearchResult

USER_AGENT = "ROM-Hub/1.0 (https://github.com/BlizzHacker/rom-hub)"


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        models = str(self.ctx.config.get("models") or "Mod,Tool,Skin").strip()
        params: dict[str, str | int] = {
            "_sSearchString": (query or "").strip(),
            "_csvModelNames": models,
            "_nPerpage": min(max(1, limit), 50),
        }

        game_id = str(self.ctx.config.get("game_id") or "").strip()
        if game_id:
            params["_idGameRow"] = game_id

        headers = {"User-Agent": USER_AGENT}

        resp = self.ctx.http.get(
            "https://gamebanana.com/apiv11/Util/Search/Results",
            params=params,
            headers=headers,
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        records = []
        if isinstance(data, dict):
            records = data.get("_aRecords", [])
        elif isinstance(data, list):
            records = data

        results: list[SearchResult] = []
        for rec in records[:limit]:
            if not isinstance(rec, dict):
                continue

            id_row = rec.get("_idRow")
            if id_row is None:
                continue

            model = rec.get("_sModelName", "Mod")
            title = rec.get("_sName") or f"{model} {id_row}"
            profile_url = rec.get("_sProfileUrl") or f"https://gamebanana.com/{model.lower()}s/{id_row}"

            game_obj = rec.get("_aGame") or {}
            game_name = game_obj.get("_sName", "") if isinstance(game_obj, dict) else ""

            submitter_obj = rec.get("_aSubmitter") or {}
            author = submitter_obj.get("_sName", "") if isinstance(submitter_obj, dict) else ""

            source_id = f"{model}:{id_row}"
            results.append(
                SearchResult(
                    source_id=source_id,
                    title=title,
                    platform="pc",
                    url=profile_url,
                    extra={
                        "model": str(model),
                        "id": str(id_row),
                        "game": str(game_name),
                        "author": str(author),
                    },
                )
            )

        return results
