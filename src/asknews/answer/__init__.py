"""Turning retrieved passages into an answer that cites them.

The language model sees only the question and the numbered passages. Its
reply is checked against those passages: citations that point nowhere are
removed, paragraphs left without any citation are dropped, and the result
records which sources were actually used.
"""
