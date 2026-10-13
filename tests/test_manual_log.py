from wankangbao.manual_log import maintain, parse_blocks


def test_creates_three_days_and_moves_past(tmp_path):
    p = tmp_path / "手动记录.md"
    assert maintain(p, "2026-01-04")            # 周日：训练日
    text = p.read_text(encoding="utf-8")
    assert set(parse_blocks(text)) == {"2026-01-04", "2026-01-05", "2026-01-06"}
    assert "**InBody**" in text                 # 01-05 是周一
    text = text.replace("- 体重:: \n", "- 体重:: 79.6\n", 1)
    text = text.replace("| 器械推胸 | — |  |  |", "| 器械推胸 | — | 第5格 | 12/12/12 |")
    p.write_text(text, encoding="utf-8")

    maintain(p, "2026-01-07")
    text = p.read_text(encoding="utf-8")
    future, history = text.split("# 历史")
    assert "2026-01-04" in history and "79.6" in history
    assert "| 器械推胸 | 第5格 × 12/12/12 → 加一格 |" in future   # 上次做满了 → 提示加重
    assert not maintain(p, "2026-01-07")        # 再跑一次没有变化


def test_custom_training_days(tmp_path):
    p = tmp_path / "m.md"
    maintain(p, "2026-01-05", train_days=[0], inbody_day=3, exercises=[["深蹲", 3, 10, "加 2.5kg"]])
    text = p.read_text(encoding="utf-8")
    assert "| 深蹲 | — |  |  |" in text and "**InBody**" not in text.split("## 2026-01-06")[0]
