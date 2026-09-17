"""Bounded XLSX reads using python-calamine."""

from __future__ import annotations

from typing import Any

from file_domain.models import FileResource


class XlsxAdapter:
    @staticmethod
    def _workbook(resource: FileResource):
        from python_calamine import CalamineWorkbook

        return CalamineWorkbook.from_path(resource.local_path)

    def inspect(self, resource: FileResource) -> dict[str, Any]:
        workbook = self._workbook(resource)
        return {"sheets": list(workbook.sheet_names)}

    def read_range(
        self,
        resource: FileResource,
        sheet: str,
        start_row: int,
        end_row: int,
        start_column: int,
        end_column: int,
        max_cells: int,
    ) -> dict[str, Any]:
        if min(start_row, start_column) < 1 or end_row < start_row or end_column < start_column:
            raise ValueError("invalid workbook range")
        requested_cells = (end_row - start_row + 1) * (end_column - start_column + 1)
        if requested_cells > max_cells:
            raise ValueError("workbook range exceeds cell limit")
        workbook = self._workbook(resource)
        if sheet not in workbook.sheet_names:
            raise ValueError("unknown workbook sheet")
        rows = workbook.get_sheet_by_name(sheet).to_python()
        values = [
            list(row[start_column - 1 : end_column])
            for row in rows[start_row - 1 : end_row]
        ]
        return {
            "sheet": sheet,
            "range": f"R{start_row}C{start_column}:R{end_row}C{end_column}",
            "rows": values,
            "truncated": False,
        }
