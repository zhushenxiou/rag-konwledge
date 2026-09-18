"""图形验证码绘制（Pillow 手绘，不引入第三方验证码库）。

本模块只负责"画图"与"生成随机码"；验证码的存储 / 一次性校验 / 过期
在 `app/services/auth.py`。

安全性说明：验证码是访问控制的一环，故码值用 `secrets` 而非 `random`
（后者是梅森旋转，观察到足够输出可预测后续码值）。图像侧只做轻度干扰
（随机字号 / 颜色 / 位移 / 干扰线 / 噪点），Demo 定位下够用，仍需防 OCR
的话应换成更强的扭曲或行为式验证。
"""
import base64
import io
import random
import secrets

from PIL import Image, ImageDraw, ImageFont

WIDTH = 120
HEIGHT = 44
CODE_LENGTH = 4
CODE_ALPHABET = "0123456789"  # 需求：随机四位阿拉伯数字

# 候选字体：优先系统 TTF（字形清晰、可缩放）。全部缺失时回退 Pillow 内置字体，
# 保证换一台没有这些字体的机器也不会炸。
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
)


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue  # 文件不存在或不是合法 TTF，试下一个
    try:
        return ImageFont.load_default(size)  # Pillow >= 10.1 支持指定字号
    except TypeError:
        return ImageFont.load_default()


def _random_color(low: int, high: int) -> tuple[int, int, int]:
    return tuple(random.randint(low, high) for _ in range(3))  # type: ignore[return-value]


def generate_code(length: int = CODE_LENGTH) -> str:
    """生成验证码明文（默认四位数字）。"""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def render(code: str) -> str:
    """把验证码画成 PNG，返回 data URI（前端可直接塞进 <img src>）。"""
    image = Image.new("RGB", (WIDTH, HEIGHT), (245, 247, 250))
    draw = ImageDraw.Draw(image)

    # 干扰线与噪点画在文字之前，避免糊住数字影响人眼识别
    for _ in range(random.randint(2, 4)):
        draw.line(
            (
                (random.randint(0, WIDTH), random.randint(0, HEIGHT)),
                (random.randint(0, WIDTH), random.randint(0, HEIGHT)),
            ),
            fill=_random_color(120, 200),
            width=1,
        )
    for _ in range(random.randint(30, 60)):
        draw.point(
            (random.randint(0, WIDTH - 1), random.randint(0, HEIGHT - 1)),
            fill=_random_color(120, 200),
        )

    # 逐字绘制：字号、颜色、纵向偏移各自随机，字符横向均匀分布
    step = WIDTH / (len(code) + 1)
    for index, char in enumerate(code):
        font = _load_font(random.randint(24, 30))
        # textbbox 的 y0 对数字通常为 0，但按字体不同会浮动，用 bbox 做垂直居中更稳
        bbox = draw.textbbox((0, 0), char, font=font)
        char_width = bbox[2] - bbox[0]
        char_height = bbox[3] - bbox[1]
        x = step * (index + 1) - char_width / 2 - bbox[0]
        y = (HEIGHT - char_height) / 2 - bbox[1] + random.randint(-4, 4)
        draw.text((x, y), char, font=font, fill=_random_color(0, 110))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
