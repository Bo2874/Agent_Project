class GoogleServiceError(Exception):
    def __init__(self, status_code: int, detail: str, code: str = "google_error"):
        super().__init__(detail)
        self.status_code, self.detail, self.code = status_code, detail, code
