"""Document reader abstract base and implementations.

Provides a pluggable reader system for different input document formats.
The architecture uses a ReaderFactory pattern: each file format has a dedicated
reader class, and the factory selects the correct one based on file extension.

Supported formats:
    - Markdown (.md, .markdown)
    - Plain text (.txt)
    - Pre-extracted PDF text (.pdf_text, .pdf.txt)

Note on PDF handling:
    This module does NOT parse PDF files directly. PDFs must be pre-processed
    by external tools (pdftotext, Nougat, Marker) into .pdf_text files, which
    are then read as plain text.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class DocumentReader(ABC):
    """Abstract base class for all document readers.

    Defines the interface that every reader implementation must satisfy.
    Subclasses register themselves by declaring which file extensions they
    support; the ReaderFactory uses this information for automatic dispatch.

    Attributes:
        (No instance attributes -- subclasses are stateless by convention.)

    Example:
        >>> reader = MarkdownReader()
        >>> text = reader.read("chapter.md")
        >>> reader.supported_extensions()
        ['.md', '.markdown']
    """

    @abstractmethod
    def read(self, path: str) -> str:
        """Read a document from disk and return its full text content.

        Args:
            path: Absolute or relative filesystem path to the document.

        Returns:
            The complete text content of the document as a single string.

        Raises:
            FileNotFoundError: If the file at *path* does not exist.
            UnicodeDecodeError: If the file cannot be decoded as UTF-8.
            PermissionError: If the file cannot be read due to OS permissions.
        """
        ...

    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """Return the list of file extensions this reader handles.

        Extensions should be lower-case and include the leading dot (e.g.,
        ``.md``, ``.txt``). A reader may declare multiple extensions when
        different suffixes map to the same underlying format.

        Returns:
            A list of extension strings, e.g. ``['.md', '.markdown']``.
        """
        ...


class MarkdownReader(DocumentReader):
    """Reader for Markdown files (.md, .markdown).

    Reads the file verbatim as UTF-8 plain text. No Markdown parsing or
    rendering is performed -- the raw source text is returned as-is.
    Downstream processing (frontmatter stripping, section splitting) is
    handled by the extraction pipeline, not by this reader.
    """

    def read(self, path: str) -> str:
        """Read a Markdown file as plain text.

        Args:
            path: Path to a .md or .markdown file.

        Returns:
            Raw file content as a string.
        """
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def supported_extensions(self) -> List[str]:
        """Return extensions for Markdown files.

        Returns:
            ``['.md', '.markdown']``
        """
        return [".md", ".markdown"]


class PlainTextReader(DocumentReader):
    """Reader for plain text files (.txt).

    The simplest reader -- opens the file, reads it, returns it. No encoding
    negotiation is attempted; UTF-8 is assumed for all input files.
    """

    def read(self, path: str) -> str:
        """Read a plain text file.

        Args:
            path: Path to a .txt file.

        Returns:
            Raw file content as a string.
        """
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def supported_extensions(self) -> List[str]:
        """Return extensions for plain text files.

        Returns:
            ``['.txt']``
        """
        return [".txt"]


class PdfTextReader(DocumentReader):
    """Reader for pre-extracted PDF text (.pdf_text, .pdf.txt).

    IMPORTANT -- This is NOT a PDF parser.
    It reads text that was *already* extracted from a PDF by an external tool
    such as ``pdftotext``, Nougat, or Marker and saved as a companion text
    file. Users must run the PDF extraction step before using this reader.

    The two supported extensions are:
        - ``.pdf_text`` -- dedicated extracted-text file
        - ``.pdf.txt``  -- alternative naming convention (PDF + .txt suffix)

    Example workflow::

        # Pre-processing step (external tool):
        $ pdftotext paper.pdf paper.pdf_text

        # Then in litrev-extract:
        reader = PdfTextReader()
        text = reader.read("paper.pdf_text")
    """

    def read(self, path: str) -> str:
        """Read a pre-extracted PDF text file.

        Args:
            path: Path to a .pdf_text or .pdf.txt file.

        Returns:
            The previously extracted text content as a string.
        """
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def supported_extensions(self) -> List[str]:
        """Return extensions for pre-extracted PDF text files.

        Returns:
            ``['.pdf_text', '.pdf.txt']``
        """
        return [".pdf_text", ".pdf.txt"]


class ReaderFactory:
    """Factory that selects the appropriate document reader based on file extension.

    The factory is configured at construction time with the set of allowed
    extensions. It then registers only those built-in readers whose extensions
    appear in that set. This prevents accidentally picking up a reader for a
    format the user has not opted into.

    Attributes:
        _readers: Internal mapping ``{extension: DocumentReader instance}``.
                  Built at ``__init__`` time and immutable thereafter.

    Example:
        >>> factory = ReaderFactory(extensions=[".md", ".txt", ".pdf_text"])
        >>> reader = factory.get_reader("paper.pdf_text")
        >>> text = reader.read("paper.pdf_text")
        >>> print(text[:100])
    """

    def __init__(self, extensions: List[str]):
        """Initialise the factory and register matching built-in readers.

        Only readers whose ``supported_extensions()`` overlap with the
        provided *extensions* list are registered. This means the factory
        will silently skip readers for formats the caller hasn't enabled.

        Args:
            extensions: List of allowed file extensions (with leading dot,
                        lower-case), e.g. ``['.md', '.txt']``.
        """
        self._readers: Dict[str, DocumentReader] = {}

        # Register built-in readers if their extensions are in the allowed set
        builtin_readers: List[DocumentReader] = [
            MarkdownReader(),
            PlainTextReader(),
            PdfTextReader(),
        ]

        for reader in builtin_readers:
            for ext in reader.supported_extensions():
                # Only register if this extension was explicitly allowed
                if ext in extensions:
                    self._readers[ext] = reader

    def get_reader(self, file_path: str) -> Optional[DocumentReader]:
        """Get the appropriate reader for a given file path.

        The lookup follows a two-step strategy:

        1. Try an exact match on the file extension.
        2. If no match is found, try appending ``.txt`` to the extension.
           This handles the ``.pdf.txt`` naming convention where a PDF's
           extracted text is saved as ``document.pdf.txt`` -- the primary
           extension is ``.pdf`` but the reader is registered under
           ``.pdf.txt``.

        Args:
            file_path: Full or relative path to a document file. The file
                       does not need to exist at lookup time -- only the
                       extension matters.

        Returns:
            A ``DocumentReader`` instance if one is registered for the
            file's extension, or ``None`` if no matching reader exists.
        """
        # Extract the trailing extension (e.g. '.pdf', '.md', '.pdf.txt')
        fname_lower = file_path.lower()
        ext = os.path.splitext(fname_lower)[1]

        # Step 0: check compound extensions first (e.g. '.pdf.txt', '.pdf_text').
        # os.path.splitext only strips the LAST suffix, so "foo.pdf.txt" would
        # yield ext=".txt", missing the PdfTextReader registered under ".pdf.txt".
        for registered_ext in self._readers:
            if fname_lower.endswith(registered_ext):
                return self._readers[registered_ext]

        # Step 1: exact extension match (covers .md, .txt, .pdf_text)
        if ext in self._readers:
            return self._readers[ext]

        # Step 2: compound extension match (e.g. '.pdf' + '.txt' -> '.pdf.txt')
        # This catches the case where the input file is "document.pdf" but the
        # reader was registered under ".pdf.txt".
        if ext + ".txt" in self._readers:
            return self._readers[ext + ".txt"]

        # No reader found for this file extension
        return None