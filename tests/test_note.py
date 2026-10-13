from wristwell.note import AI_END, read_frontmatter, render


def test_keeps_user_notes_and_replaces_ai_section():
    first = render(None, None, {"steps": 100, "hrv": None}, "旧报告")
    edited = first.replace("## 备注\n", "## 备注\n今天膝盖有点酸\n")
    second = render(edited, None, {"steps": 200}, "新报告")
    assert "今天膝盖有点酸" in second and "新报告" in second and "旧报告" not in second
    assert second.count(AI_END) == 1
    assert read_frontmatter(second)["steps"] == 200


def test_unknown_frontmatter_keys_are_kept():
    text = "---\nmood: 好\nsteps:\n---\n\n正文\n"
    out = render(text, None, {"steps": 5}, None)
    assert read_frontmatter(out) == {"mood": "好", "steps": 5}
    assert "正文" in out


def test_deleted_end_marker_does_not_eat_user_text():
    first = render(None, None, {"steps": 1}, "报告")
    broken = first.replace(AI_END, "") + "\n我在报告下面写的总结\n"
    out = render(render(broken, None, {"steps": 2}, "报告2"), None, {"steps": 3}, "报告3")
    assert "我在报告下面写的总结" in out and out.count(AI_END) == 1


def test_crlf_frontmatter_is_reused():
    out = render("---\r\nmood: 好\r\nsteps:\r\n---\r\n正文\r\n", None, {"steps": 5}, None)
    assert out.count("---\n") == 2 and read_frontmatter(out)["steps"] == 5
