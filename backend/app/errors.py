"""User-facing errors. Each carries a stable code and a message that is safe to show in the UI."""


class AppError(Exception):
    status_code = 400
    code = "error"

    def __init__(self, message: str, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail


class UnsupportedAudioError(AppError):
    code = "unsupported_audio"


class CorruptAudioError(AppError):
    code = "corrupt_audio"


class AudioTooShortError(AppError):
    code = "audio_too_short"


class AudioTooLongError(AppError):
    code = "audio_too_long"


class SilentAudioError(AppError):
    code = "silent_audio"


class FileTooLargeError(AppError):
    status_code = 413
    code = "file_too_large"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class InvalidCorrectionError(AppError):
    status_code = 422
    code = "invalid_correction"
