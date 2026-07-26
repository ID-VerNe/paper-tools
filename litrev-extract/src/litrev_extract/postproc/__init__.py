"""Post-processor __init__ — imports built-in processors so decorators fire."""

from .aggregate import AggregateProcessor
from .stats import StatsProcessor
from .export_csv import CsvExportProcessor
from .report_md import MarkdownReportProcessor