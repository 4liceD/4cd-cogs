from __future__ import annotations

import io
import random
import string

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_code(length: int) -> str:
    length = max(4, min(10, int(length)))
    return "".join(random.choice(ALPHABET) for _ in range(length))


def render_captcha(code: str) -> io.BytesIO:
    width = max(220, 42 * len(code))
    height = 90
    image = Image.new("RGB", (width, height), color=(245, 247, 250))
    draw = ImageDraw.Draw(image)

    for _ in range(10):
        x1, y1 = random.randint(0, width), random.randint(0, height)
        x2, y2 = random.randint(0, width), random.randint(0, height)
        draw.line((x1, y1, x2, y2), fill=(210, 216, 224), width=1)

    font = ImageFont.load_default()
    padding_x = 18
    char_w = (width - (padding_x * 2)) // max(1, len(code))
    for idx, ch in enumerate(code):
        x = padding_x + (idx * char_w) + random.randint(0, 6)
        y = random.randint(20, 38)
        draw.text((x, y), ch, fill=(32, 35, 42), font=font)

    for _ in range(450):
        draw.point(
            (random.randint(0, width - 1), random.randint(0, height - 1)),
            fill=(random.randint(110, 225), random.randint(110, 225), random.randint(110, 225)),
        )

    image = image.filter(ImageFilter.SMOOTH)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
