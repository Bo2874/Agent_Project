"""Send plain-text mail from one host-configured mailbox; no credentials in tool input."""

import asyncio
import smtplib
import socket
import ssl
import threading
from collections.abc import Mapping
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

from agent_core.contracts import CapabilityDescriptor
from agent_core.tools.registry import ToolRegistry
from agent_core.tools.types import ToolContext, ToolExecutionPolicy, ToolResult, create_context_tool

SEND_EMAIL_DESCRIPTION = (
    "Send one plain-text email from the configured system mailbox to a specified recipient. "
    "Use only when the user requests actual email delivery, not merely drafting an email. "
    "Use the conversation to resolve recipient and content; ask for missing essential details "
    "and never invent a recipient. Execution requires approval. "
    "If delivery status is unknown, do not resend automatically."
)


class SMTPConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    security: Literal["ssl", "starttls"] = "ssl"
    username: str = Field(min_length=1, max_length=320, repr=False)
    password: SecretStr = Field(repr=False)
    from_email: EmailStr
    timeout_seconds: float = Field(default=20, gt=0, le=60)

    @field_validator("host", "username")
    @classmethod
    def valid_connection_text(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 33 or ord(char) == 127 for char in value):
            raise ValueError("SMTP host/username must not contain whitespace or control characters")
        return value

    @field_validator("password")
    @classmethod
    def password_required(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("SMTP_PASSWORD is required when email is enabled")
        return value


class SendEmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    to: EmailStr = Field(
        description="One recipient email address, explicitly supplied by the user."
    )
    subject: str = Field(min_length=1, max_length=200, description="Email subject.")
    body: str = Field(min_length=1, max_length=100_000, description="Plain-text email body.")

    @field_validator("to", "subject", mode="before")
    @classmethod
    def single_line_headers(cls, value: Any) -> Any:
        if isinstance(value, str) and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Email headers must not contain control characters")
        return value

    @field_validator("subject", "body")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Email subject/body must contain text without null characters")
        return value


def _failure(
    code: str, message: str, *, unknown: bool = False, smtp_code: int | None = None
) -> ToolResult:
    data: dict[str, Any] = {"status": "delivery_unknown" if unknown else "not_sent"}
    if smtp_code is not None:
        data["smtp_code"] = smtp_code
    return ToolResult.failure(
        message,
        error_code=code,
        data=data,
    )


def _authenticate(client: smtplib.SMTP, config: SMTPConfig) -> None:
    # Prefer challenge/response LOGIN when advertised. Some servers close a PLAIN
    # exchange instead of returning the useful authentication error code.
    if "LOGIN" not in client.esmtp_features.get("auth", "").upper().split():
        client.login(config.username, config.password.get_secret_value())
        return
    responses = iter((config.username, config.password.get_secret_value()))

    def respond(challenge: bytes | None) -> str:
        try:
            return next(responses)
        except StopIteration:
            raise smtplib.SMTPException("Unexpected SMTP authentication challenge") from None

    client.auth("LOGIN", respond, initial_response_ok=False)


class _SMTPDelivery:
    """One blocking attempt. Cancellation closes the socket and prevents a later submission."""

    def __init__(self, config: SMTPConfig):
        self.config = config
        self.cancelled = threading.Event()
        self.client: smtplib.SMTP | None = None
        self.submitting = False

    def cancel(self) -> None:
        self.cancelled.set()
        client = self.client
        sock = client.sock if client is not None else None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def send(self, request: SendEmailInput) -> ToolResult:
        config = self.config
        message = EmailMessage()
        message["From"] = str(config.from_email)
        message["To"] = str(request.to)
        message["Subject"] = request.subject
        message["Date"] = formatdate(localtime=False, usegmt=True)
        message_id = make_msgid()
        message["Message-ID"] = message_id
        message.set_content(request.body)
        client = None
        try:
            if self.cancelled.is_set():
                return _failure("email_cancelled", "Đã hủy trước khi gửi email.")
            tls = ssl.create_default_context()
            client = (
                smtplib.SMTP_SSL(
                    config.host, config.port, timeout=config.timeout_seconds, context=tls
                )
                if config.security == "ssl"
                else smtplib.SMTP(config.host, config.port, timeout=config.timeout_seconds)
            )
            self.client = client
            if self.cancelled.is_set():
                return _failure("email_cancelled", "Đã hủy trước khi gửi email.")
            client.ehlo()
            if config.security == "starttls":
                client.starttls(context=tls)
                client.ehlo()
            _authenticate(client, config)
            if self.cancelled.is_set():
                return _failure("email_cancelled", "Đã hủy trước khi gửi email.")
            self.submitting = True
            # Recheck after publishing the phase so a racing cancellation is conservative.
            if self.cancelled.is_set():
                return _failure("email_cancelled", "Đã hủy trước khi gửi email.")
            refused = client.send_message(
                message, from_addr=str(config.from_email), to_addrs=[str(request.to)]
            )
            if refused:
                return _failure("email_recipient_refused", "SMTP từ chối địa chỉ người nhận.")
            return ToolResult.success(
                "SMTP đã chấp nhận email để gửi. Chưa xác nhận thư đã tới inbox người nhận.",
                data={
                    "status": "accepted",
                    "from": str(config.from_email),
                    "to": str(request.to),
                    "message_id": message_id,
                },
            )
        except smtplib.SMTPAuthenticationError as exc:
            # Inspect known markers only; never expose the raw authentication reply.
            reply = (
                exc.smtp_error.decode("utf-8", "replace")
                if isinstance(exc.smtp_error, bytes)
                else str(exc.smtp_error)
            ).lower()
            if "5.7.9" in reply or "application-specific password required" in reply:
                return _failure(
                    "email_app_password_required",
                    "Gmail yêu cầu App Password (mật khẩu ứng dụng). Admin cần tạo App Password "
                    "từ đúng tài khoản SMTP_USERNAME, cập nhật SMTP_PASSWORD rồi khởi động lại "
                    "backend. Email chưa được gửi.",
                    smtp_code=exc.smtp_code,
                )
            if "5.7.14" in reply or "log in via your web browser" in reply:
                return _failure(
                    "email_account_verification_required",
                    "Gmail yêu cầu xác minh tài khoản. Admin cần đăng nhập và kiểm tra yêu cầu "
                    "bảo mật của tài khoản gửi trước khi thử lại. Email chưa được gửi.",
                    smtp_code=exc.smtp_code,
                )
            return _failure(
                "email_authentication_failed",
                "Xác thực SMTP thất bại; admin cần kiểm tra SMTP_USERNAME và mật khẩu ứng dụng "
                "của đúng tài khoản gửi. Email chưa được gửi.",
                smtp_code=exc.smtp_code,
            )
        except smtplib.SMTPRecipientsRefused:
            return _failure("email_recipient_refused", "SMTP từ chối địa chỉ người nhận.")
        except smtplib.SMTPSenderRefused:
            return _failure("email_sender_refused", "SMTP từ chối địa chỉ gửi của hệ thống.")
        except smtplib.SMTPDataError:
            return _failure("email_data_rejected", "SMTP từ chối nội dung email.")
        except smtplib.SMTPNotSupportedError:
            return _failure(
                "email_smtp_not_supported",
                "SMTP không hỗ trợ tính năng xác thực/TLS/email cần dùng.",
            )
        except (OSError, smtplib.SMTPException, ValueError) as exc:
            if self.submitting:
                return _failure(
                    "email_delivery_unknown",
                    "Chưa xác định email đã được gửi hay chưa. Không tự gửi lại; "
                    "hãy kiểm tra hộp thư đã gửi trước.",
                    unknown=True,
                )
            if isinstance(exc, ssl.SSLCertVerificationError):
                return _failure(
                    "email_tls_verification_failed",
                    "Không xác minh được chứng chỉ TLS của SMTP; email chưa được gửi.",
                )
            if isinstance(exc, smtplib.SMTPServerDisconnected):
                return _failure(
                    "email_smtp_disconnected",
                    "Máy chủ SMTP đóng kết nối trước khi gửi thư. Admin cần kiểm tra kết nối "
                    "và xác thực SMTP; email chưa được gửi.",
                )
            return _failure(
                "email_connection_failed", "Không kết nối/xác thực được SMTP; email chưa được gửi."
            )
        finally:
            if client is not None:
                # Avoid a QUIT failure masking a successful DATA response or prolonging a timeout.
                try:
                    client.close()
                except OSError:
                    pass


class SendEmailHandler:
    def __init__(self, config: SMTPConfig):
        self.config = config

    async def __call__(self, context: ToolContext, arguments: Mapping[str, Any]) -> ToolResult:
        request = SendEmailInput.model_validate(dict(arguments))
        delivery = _SMTPDelivery(self.config)
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                return await asyncio.to_thread(delivery.send, request)
        except TimeoutError:
            delivery.cancel()
            return _failure(
                "email_delivery_unknown" if delivery.submitting else "email_timeout",
                "Gửi email vượt thời gian chờ. Không tự gửi lại; hãy kiểm tra hộp thư đã gửi."
                if delivery.submitting
                else "Kết nối SMTP vượt thời gian chờ; email chưa được gửi.",
                unknown=delivery.submitting,
            )
        except asyncio.CancelledError:
            delivery.cancel()
            raise


def register_email_tool(registry: ToolRegistry, config: SMTPConfig) -> None:
    registry.register(
        create_context_tool(
            name="send_email",
            description=SEND_EMAIL_DESCRIPTION,
            args_schema=SendEmailInput,
            coroutine=SendEmailHandler(config),
        ),
        policy=ToolExecutionPolicy(
            timeout_seconds=config.timeout_seconds + 5,
            max_retries=0,
            idempotent=False,
            allowed_roles=frozenset({"user", "admin"}),
            requires_approval=True,
        ),
        capability=CapabilityDescriptor(
            capability_id="send_email",
            kind="tool",
            name="send_email",
            description=SEND_EMAIL_DESCRIPTION,
            version="1.0.0",
            tags=("email", "smtp"),
            side_effect="write",
            requires_approval=True,
        ),
    )
