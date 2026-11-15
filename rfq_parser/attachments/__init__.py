"""Reading what is inside an attachment: code readers per file type, the model for pictures and scans."""

from .extractor import AttachmentExtractor, unreadable

__all__ = ["AttachmentExtractor", "unreadable"]
