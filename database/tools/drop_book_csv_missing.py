"""
Compatibility shim for older import path used by tools.fetch_cover_url.
Exports load_titles and drop_titles from database.tools.db_tools.
"""

from .db_tools import drop_titles, load_titles

__all__ = ["drop_titles", "load_titles"]
