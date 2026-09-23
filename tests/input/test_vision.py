"""Tests for `app.input.vision`.

No real LLM/network calls: a `FakeLLMClient` implementing the `LLMClient`
protocol stands in for the vision model, returning canned responses and
recording how it was called.
"""

import io
import json

import pytest
from PIL import Image

from app.input.vision import (
    ALLOWED_IMAGE_TYPES,
    MAX_IMAGE_BYTES,
    ImageValidationError,
    detect_image_type,
    extract_from_image,
    validate_image,
)
from tests.input.fakes import FakeLLMClient

# --------------------------------------------------------------------------
# Tiny in-memory test images
# --------------------------------------------------------------------------


def _make_image_bytes(fmt: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=(255, 0, 0)).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def png_bytes() -> bytes:
    return _make_image_bytes("PNG")


@pytest.fixture
def jpeg_bytes() -> bytes:
    return _make_image_bytes("JPEG")


@pytest.fixture
def webp_bytes() -> bytes:
    return _make_image_bytes("WEBP")


# --------------------------------------------------------------------------
# detect_image_type / validate_image
# --------------------------------------------------------------------------


def test_allowed_image_types_are_png_jpeg_webp() -> None:
    assert {"image/png", "image/jpeg", "image/webp"} == ALLOWED_IMAGE_TYPES


def test_detect_image_type_png(png_bytes: bytes) -> None:
    assert detect_image_type(png_bytes) == "image/png"


def test_detect_image_type_jpeg(jpeg_bytes: bytes) -> None:
    assert detect_image_type(jpeg_bytes) == "image/jpeg"


def test_detect_image_type_webp(webp_bytes: bytes) -> None:
    assert detect_image_type(webp_bytes) == "image/webp"


def test_detect_image_type_unrecognized_bytes() -> None:
    assert detect_image_type(b"just some plain text, not an image") is None


def test_validate_image_never_trusts_declared_mime(png_bytes: bytes) -> None:
    # Declared mime says jpeg, but the bytes are actually PNG -- detected wins.
    assert validate_image(png_bytes, "image/jpeg") == "image/png"


def test_validate_image_empty_raises() -> None:
    with pytest.raises(ImageValidationError) as exc_info:
        validate_image(b"", "image/png")
    assert exc_info.value.reason == "empty"


def test_validate_image_too_large_raises(png_bytes: bytes) -> None:
    oversized = png_bytes + b"\x00" * (MAX_IMAGE_BYTES + 1)
    with pytest.raises(ImageValidationError) as exc_info:
        validate_image(oversized, "image/png")
    assert exc_info.value.reason == "too_large"


def test_validate_image_unsupported_type_raises() -> None:
    with pytest.raises(ImageValidationError) as exc_info:
        validate_image(b"just some plain text, not an image", "text/plain")
    assert exc_info.value.reason == "unsupported_type"


# --------------------------------------------------------------------------
# extract_from_image
# --------------------------------------------------------------------------


async def test_extract_happy_path_problem_and_constraints_no_code(png_bytes: bytes) -> None:
    client = FakeLLMClient(
        vision_content=(
            '{"problem": "Two Sum: given an array...", "code": null, '
            '"code_language": null, "error": null, '
            '"constraints": ["2 <= nums.length <= 10^4"], "question": null}'
        )
    )

    result = await extract_from_image(client, png_bytes)

    assert result.source == "image"
    assert result.problem == "Two Sum: given an array..."
    assert result.code == []
    assert result.error is None
    assert result.constraints == ["2 <= nums.length <= 10^4"]
    assert result.question is None

    assert len(client.vision_calls) == 1
    assert client.vision_calls[0]["mime_type"] == "image/png"


async def test_extract_handles_json_fenced_in_code_block(png_bytes: bytes) -> None:
    client = FakeLLMClient(
        vision_content=(
            "```json\n"
            '{"problem": "A problem", "code": null, "code_language": null, '
            '"error": null, "constraints": [], "question": null}\n'
            "```"
        )
    )

    result = await extract_from_image(client, png_bytes)

    assert result.source == "image"
    assert result.problem == "A problem"


async def test_extract_malformed_output_degrades_to_normalize_text(png_bytes: bytes) -> None:
    client = FakeLLMClient(vision_content="not json at all, just some rambling prose")

    result = await extract_from_image(client, png_bytes)

    assert result.source == "image"
    # Degraded path still produces a valid StructuredInput via normalize_text.
    assert result.question is not None or result.problem is not None


async def test_extract_code_without_language_uses_guess_language(png_bytes: bytes) -> None:
    code = "def two_sum(nums, target):\n    for i in range(len(nums)):\n        return i"
    payload: dict[str, object] = {
        "problem": None,
        "code": code,
        "code_language": None,
        "error": None,
        "constraints": [],
        "question": None,
    }
    client = FakeLLMClient(vision_content=json.dumps(payload))

    result = await extract_from_image(client, png_bytes)

    assert len(result.code) == 1
    assert result.code[0].content == code
    assert result.code[0].language == "python"


async def test_extract_rejects_invalid_image_before_calling_vision() -> None:
    client = FakeLLMClient(vision_content="{}")

    with pytest.raises(ImageValidationError):
        await extract_from_image(client, b"")

    assert client.vision_calls == []


# --------------------------------------------------------------------------
# _VisionExtraction loose-type coercion (model doesn't always follow the
# requested shape exactly)
# --------------------------------------------------------------------------


async def test_extract_null_constraints_does_not_raise(png_bytes: bytes) -> None:
    payload = (
        '{"problem": "A problem", "code": null, "code_language": null, '
        '"error": null, "constraints": null, "question": null}'
    )
    client = FakeLLMClient(vision_content=payload)

    result = await extract_from_image(client, png_bytes)

    assert result.problem == "A problem"
    assert result.constraints == []


async def test_extract_single_string_constraints_becomes_list(png_bytes: bytes) -> None:
    payload = (
        '{"problem": "A problem", "code": null, "code_language": null, '
        '"error": null, "constraints": "2 <= n <= 10", "question": null}'
    )
    client = FakeLLMClient(vision_content=payload)

    result = await extract_from_image(client, png_bytes)

    assert result.constraints == ["2 <= n <= 10"]


async def test_extract_code_as_list_of_lines_is_joined(png_bytes: bytes) -> None:
    payload_obj: dict[str, object] = {
        "problem": None,
        "code": ["def f():", "    return 1"],
        "code_language": "python",
        "error": None,
        "constraints": [],
        "question": None,
    }
    client = FakeLLMClient(vision_content=json.dumps(payload_obj))

    result = await extract_from_image(client, png_bytes)

    assert len(result.code) == 1
    assert result.code[0].content == "def f():\n    return 1"


async def test_extract_non_str_scalar_fields_become_none(png_bytes: bytes) -> None:
    payload_obj: dict[str, object] = {
        "problem": 123,
        "code": None,
        "code_language": None,
        "error": True,
        "constraints": [],
        "question": ["not", "a", "string"],
    }
    client = FakeLLMClient(vision_content=json.dumps(payload_obj))

    result = await extract_from_image(client, png_bytes)

    assert result.problem is None
    assert result.error is None
    assert result.question is None


# --------------------------------------------------------------------------
# Malformed-output degrade path must never raise (truncated to MAX_TEXT_CHARS)
# --------------------------------------------------------------------------


async def test_extract_oversized_malformed_output_does_not_raise(png_bytes: bytes) -> None:
    client = FakeLLMClient(vision_content="not json, just rambling. " * 3000)  # ~75k chars

    result = await extract_from_image(client, png_bytes)

    assert result.source == "image"
