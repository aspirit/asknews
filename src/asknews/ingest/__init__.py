"""Ingestion of official publications into the archive.

Each source has its own module. They all share the cached HTTP fetcher and
the store helpers, so a source module only has to know how to list items,
fetch them and turn them into documents and segments.
"""
