"""Generate the image fixtures used by `tests/input`.

Renders a realistic LeetCode-style problem-statement screenshot (title,
statement, worked example, constraints -- no code) as a PNG so the manual /
live tests have a real image to send through `/understand`.

Regenerate with:

    python -m tests.input.fixtures.make_fixtures
"""

from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw, ImageFont

__all__ = ["make_two_sum_fixture"]

FIXTURES_DIR: Final = Path(__file__).resolve().parent
TWO_SUM_PATH: Final = FIXTURES_DIR / "leetcode_two_sum.png"

_WIDTH: Final = 900
_MARGIN: Final = 40
_BODY_SIZE: Final = 20
_TITLE_SIZE: Final = 26
_HEADING_SIZE: Final = 21
_BG: Final = (255, 255, 255)
_FG: Final = (20, 20, 20)
_LINE_GAP: Final = 10


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    names = ["arialbd.ttf", "Arial Bold.ttf"] if bold else ["arial.ttf", "Arial.ttf"]
    for name in [*names, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)  # type: ignore[return-value]


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if font.getlength(candidate) <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def make_two_sum_fixture() -> bytes:
    """Render the "1. Two Sum" LeetCode-style screenshot and return PNG bytes."""
    title_font = _font(bold=True, size=_TITLE_SIZE)
    heading_font = _font(bold=True, size=_HEADING_SIZE)
    body_font = _font(bold=False, size=_BODY_SIZE)

    max_text_width = _WIDTH - 2 * _MARGIN

    statement = (
        "Given an array of integers nums and an integer target, return indices "
        "of the two numbers such that they add up to target. You may assume "
        "that each input would have exactly one solution, and you may not use "
        "the same element twice."
    )
    example_lines = [
        "Input: nums = [2,7,11,15], target = 9",
        "Output: [0,1]",
        "Explanation: Because nums[0] + nums[1] == 9, we return [0, 1].",
    ]
    constraint_lines = [
        "2 <= nums.length <= 10^4",
        "-10^9 <= nums[i] <= 10^9",
        "-10^9 <= target <= 10^9",
        "Only one valid answer exists.",
    ]

    blocks: list[tuple[ImageFont.FreeTypeFont, str, int]] = []
    blocks.append((title_font, "1. Two Sum", _TITLE_SIZE + _LINE_GAP + 10))
    for line in _wrap(statement, body_font, max_text_width):
        blocks.append((body_font, line, _BODY_SIZE + _LINE_GAP))
    blocks.append((body_font, "", _BODY_SIZE))
    blocks.append((heading_font, "Example 1:", _HEADING_SIZE + _LINE_GAP))
    for line in example_lines:
        blocks.append((body_font, line, _BODY_SIZE + _LINE_GAP))
    blocks.append((body_font, "", _BODY_SIZE))
    blocks.append((heading_font, "Constraints:", _HEADING_SIZE + _LINE_GAP))
    for line in constraint_lines:
        blocks.append((body_font, f"• {line}", _BODY_SIZE + _LINE_GAP))

    height = _MARGIN * 2 + sum(advance for _, _, advance in blocks)

    image = Image.new("RGB", (_WIDTH, height), color=_BG)
    draw = ImageDraw.Draw(image)

    y = _MARGIN
    for font, text, advance in blocks:
        if text:
            draw.text((_MARGIN, y), text, font=font, fill=_FG)
        y += advance

    buffer_path = TWO_SUM_PATH
    image.save(buffer_path, format="PNG", optimize=True)
    return buffer_path.read_bytes()


def main() -> None:
    data = make_two_sum_fixture()
    print(f"wrote {TWO_SUM_PATH} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
