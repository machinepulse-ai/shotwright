from app.services.session_titles import derive_session_title


def test_derive_session_title_from_first_meaningful_line() -> None:
    title = derive_session_title(
        "1. 用 TikTok 常用剪辑手法制作一个橘猫萌宠故事\n"
        "2. 加中文 TTS 解说和字幕\n"
    )

    assert title == "用 TikTok 常用剪辑手法制作一个橘猫萌宠故事"


def test_derive_session_title_falls_back_to_attachment_summary() -> None:
    title = derive_session_title("", [{"type": "image"}, {"type": "reference_video"}])

    assert title == "参考图片与视频制作"


def test_derive_session_title_ignores_greeting_until_meaningful_prompt() -> None:
    assert derive_session_title("你好") is None
    assert derive_session_title("你好\n制作一个橘猫 TVC") == "制作一个橘猫 TVC"
