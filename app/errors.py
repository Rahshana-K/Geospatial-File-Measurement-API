"""Domain errors. Each carries the HTTP status the API should answer with."""
from __future__ import annotations


class GeoFileError(Exception):
    status_code = 422

    def __init__(self, message: str, file_id: str | None = None):
        super().__init__(message)
        self.message = message
        self.file_id = file_id


class UnsupportedFileTypeError(GeoFileError):
    status_code = 415


class FileTooLargeError(GeoFileError):
    status_code = 413


class InvalidFileError(GeoFileError):
    """The file is the right type but cannot be read (corrupt, empty, no layers...)."""

    status_code = 422


class MissingCRSError(InvalidFileError):
    """The file declares no coordinate reference system and none was supplied."""
