'''Tests for resource utilities and terminal charts.'''

import contextlib
import io
import unittest
from unittest import mock

import plotext

from pdf_remediation.utilities.resources import get_page_count_chunks


class PageCountChartTests(unittest.TestCase):
    '''Exercise chart rendering with the installed plotext API.'''

    def test_chart_renders_and_resets_between_distributions(self) -> None:
        '''Rendering another distribution does not leave old bars in the next chart.'''
        rendered_charts = []
        for page_counts in ([1, 5, 25], [1] * 20, [1, 5, 25]):
            file_paths = [(f"file-{index}.pdf",) for index in range(len(page_counts))]
            page_count_lookup = {
                file_path[0]: page_count
                for file_path, page_count in zip(file_paths, page_counts)
            }
            output = io.StringIO()
            # Capture the rendered matrix; plotext normally prints through native code.
            with (
                contextlib.redirect_stdout(output),
                mock.patch.object(
                    plotext.matrix,
                    "print",
                    new=lambda matrix, **_: print(matrix.string(colorless=True)),
                ),
            ):
                chunks = get_page_count_chunks(
                    file_paths,
                    page_count_lookup,
                    lambda *parts: parts,
                )
            self.assertEqual(sum(len(bucket) for bucket in chunks.values()), len(file_paths))
            rendered_charts.append(output.getvalue())

        chart = rendered_charts[0]
        self.assertIn("File Distribution by Page Count", chart)
        self.assertIn("Range", chart)
        self.assertIn("# of Files", chart)
        self.assertIn("█", chart)
        self.assertNotEqual(chart, rendered_charts[1])
        self.assertEqual(chart, rendered_charts[2])


if __name__ == "__main__":
    unittest.main()
