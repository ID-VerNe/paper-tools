"""File system utility functions.

Provides common file-system operations used throughout the litrev-extract
pipeline: path resolution, document scanning with extension filtering and
glob-based exclusion, safe directory creation, and normalised relative paths.

Exclusion logic
---------------
The ``scan_documents`` function uses ``fnmatch.fnmatch`` to test file paths
against a list of glob patterns. A file or directory is excluded if *any*
pattern matches (OR logic). This means exclusion patterns can target both:

- Whole directories (e.g. ``*/archived/*``)
- Specific files (e.g. ``*draft*``)
- File extensions (e.g. ``*.tmp``)

Exclusion is applied at both the file level and (for recursive scans) the
directory level: directories matching an exclusion pattern are pruned from
the walk so their contents are never visited.
"""

import fnmatch
import os
from pathlib import Path
from typing import List, Optional


def resolve_project_path(
    path: str, project_root: Optional[str] = None
) -> str:
    """Resolve a path relative to the project root.

    If *path* is already absolute, it is returned unchanged. Otherwise it is
    joined with *project_root* (or the current working directory as fallback)
    to produce an absolute path.

    Args:
        path:         A filesystem path, absolute or relative.
        project_root: Optional root directory for resolving relative paths.
                      If ``None``, the current working directory is used.

    Returns:
        An absolute path string with the OS-native separator.
    """
    p = Path(path)
    if p.is_absolute():
        return str(p)
    base = Path(project_root) if project_root else Path.cwd()
    return str(base / p)


def scan_documents(
    input_dir: str,
    extensions: List[str],
    recursive: bool = True,
    exclude_patterns: Optional[List[str]] = None,
) -> List[str]:
    """Scan a directory for document files matching the given extensions.

    Walks *input_dir* (recursively or not) and collects all files whose
    extension appears in *extensions*. Files and directories whose path
    matches any of the *exclude_patterns* globs are skipped.

    When *recursive* is ``True``, the function mutates ``dirs`` in-place
    during the walk (``os.walk`` convention) to prune excluded directories
    early, avoiding unnecessary filesystem access.

    Args:
        input_dir:        Root directory to scan for documents.
        extensions:       List of allowed file extensions with leading dot,
                          lower-case (e.g. ``['.md', '.txt']``).
        recursive:        If ``True``, descend into subdirectories. If
                          ``False``, scan only the top-level directory.
        exclude_patterns: Optional list of ``fnmatch`` glob patterns. Any
                          file or directory matching a pattern is skipped.

    Returns:
        Sorted list of absolute file paths matching the criteria.

    Example:
        >>> scan_documents("papers", [".md", ".txt"], exclude_patterns=["*/draft/*", "*.tmp"])
        ['papers/paper1.md', 'papers/subdir/paper2.txt']
    """
    exclude_patterns = exclude_patterns or []
    matches: List[str] = []

    # Validate input directory before walking
    if not os.path.isdir(input_dir):
        import logging
        logging.getLogger(__name__).warning(
            "Input directory '%s' does not exist or is not a directory", input_dir
        )
        return matches

    if recursive:
        # Recursive walk: os.walk yields (root, dirs, files) for each directory
        for root, dirs, files in os.walk(input_dir):
            # Prune excluded directories in-place so os.walk does not descend into them.
            # This is a documented os.walk pattern: mutating dirs affects future iterations.
            dirs[:] = [
                d
                for d in dirs
                if not _matches_any(os.path.join(root, d), exclude_patterns)
            ]
            for fname in files:
                # Check extension match first (cheaper) before checking exclusion patterns
                if any(fname.endswith(ext) for ext in extensions):
                    fpath = os.path.join(root, fname)
                    if not _matches_any(fpath, exclude_patterns):
                        matches.append(fpath)
    else:
        # Non-recursive: just list the top-level directory
        for fname in os.listdir(input_dir):
            fpath = os.path.join(input_dir, fname)
            # Only match regular files (skip subdirectories)
            if os.path.isfile(fpath) and any(
                fname.endswith(ext) for ext in extensions
            ):
                if not _matches_any(fpath, exclude_patterns):
                    matches.append(fpath)

    # Sort for deterministic ordering across runs and platforms
    return sorted(matches)


def _matches_any(path: str, patterns: List[str]) -> bool:
    """Check if a path matches any of the given glob patterns.

    Uses ``fnmatch.fnmatch`` which follows Unix shell-style wildcards:
    ``*`` matches everything, ``?`` matches any single character,
    ``[seq]`` matches any character in *seq*.

    On Windows, ``os.walk`` yields backslash-delimited paths while
    ``fnmatch`` patterns typically use forward slashes.  The path is
    normalised to use forward slashes before matching to ensure
    cross-platform consistency.

    Args:
        path:     The filesystem path to test.
        patterns: List of ``fnmatch`` glob patterns.

    Returns:
        ``True`` if the path matches at least one pattern, ``False`` if none
        match or if *patterns* is empty.
    """
    # Normalise backslashes to forward slashes for cross-platform fnmatch
    normalised = path.replace("\\", "/")
    for p in patterns:
        if fnmatch.fnmatch(normalised, p):
            return True
    return False


def safe_makedirs(path: str) -> None:
    """Create a directory and any missing parent directories.

    This is a thin wrapper around ``os.makedirs`` with ``exist_ok=True``,
    so it does not raise if the directory already exists. Useful for
    ensuring output directories are ready before writing files.

    Args:
        path: Filesystem path to create. Intermediate directories are
              created as needed.
    """
    os.makedirs(path, exist_ok=True)


def relpath(path: str, start: str) -> str:
    """Compute the relative path from *start* to *path* using forward slashes.

    On Windows, ``os.path.relpath`` uses backslashes. This function
    normalises them to forward slashes for cross-platform consistency,
    which is important when paths are used as identifiers or keys.

    Args:
        path:  The target absolute or relative path.
        start: The reference directory from which to compute the relative path.

    Returns:
        Relative path string with forward slashes as separators.
    """
    return os.path.relpath(path, start).replace("\\", "/")