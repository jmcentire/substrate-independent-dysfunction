"""
Shared error response formats.
Convention: {error: {code: "...", message: "...", details: {}}}
"""
from fastapi import HTTPException
from fastapi.responses import JSONResponse


def error_response(status_code: int, code: str, message: str, details: dict = None):
    """Create a structured error response."""
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=body)


class ServiceError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, details: dict = None):
        detail = {"code": code, "message": message}
        if details:
            detail["details"] = details
        super().__init__(status_code=status_code, detail=detail)
