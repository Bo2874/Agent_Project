from typing import Any

FIELD_NAMES = {
    "email": "Email",
    "full_name": "Tên hiển thị",
    "password": "Mật khẩu",
    "content": "Tin nhắn",
    "title": "Tiêu đề",
}


def translate_validation_error(error: dict[str, Any]) -> str:
    location = error.get("loc", ())
    field = str(location[-1]) if location else "Dữ liệu"
    field_name = FIELD_NAMES.get(field, field.replace("_", " ").capitalize())
    error_type = error.get("type", "")
    context = error.get("ctx") or {}

    if error_type == "missing":
        return f"{field_name} là bắt buộc."
    if field == "email":
        return "Email không đúng định dạng. Ví dụ: ten@example.com."
    if error_type == "string_too_short":
        minimum = context.get("min_length")
        return f"{field_name} phải có ít nhất {minimum} ký tự."
    if error_type == "string_too_long":
        maximum = context.get("max_length")
        return f"{field_name} không được vượt quá {maximum} ký tự."
    if error_type == "json_invalid":
        return "Dữ liệu gửi lên không đúng định dạng."
    return f"{field_name} không hợp lệ."
