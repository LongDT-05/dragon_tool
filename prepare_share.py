"""Remove generated user data from the copy staged for a share ZIP."""

import json
import shutil
import sys
from pathlib import Path


def clean_share_copy(root: Path) -> None:
    config_path = root / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    config.update({
        "window_title_keyword": "AUTO",
        "dragon_reference_window_size": [0, 0],
        "selected_parent_1": "",
        "selected_parent_2": "",
        "dragon_text_rules": {"bless": [], "keep": [], "sell": []},
        "active_profile": "Mặc định",
        "control_mode": "windows",
        "enable_remote_notify": False,
    })
    for key in ("telegram_token", "telegram_chat_id", "discord_webhook", "adb_path", "adb_game_package", "update_manifest_url"):
        config.pop(key, None)
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    for name in (
        "profiles", "lich_su_rong_quy", "ai_dataset", "templates/auto_learned",
        "templates/parents", "templates/bless", "templates/keep", "templates/sell",
        "templates/dust_targets", "templates/protect_dust",
    ):
        path = root / name
        if path.is_dir():
            shutil.rmtree(path)
    for name in ("pair_stats.json", "ocr_learned_map.json", "config.json.corrupt.bak"):
        (root / name).unlink(missing_ok=True)


if __name__ == "__main__":
    clean_share_copy(Path(sys.argv[1]))
