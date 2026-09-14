from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from surface_analyzer.mixins.reporting import ReportingMixin


def test_long_report_sections_do_not_overlap():
    fig = Figure(figsize=(17, 9))
    FigureCanvasAgg(fig)
    grid = fig.add_gridspec(3, 3)
    axes = [fig.add_subplot(grid[i, 0]) for i in range(3)]
    contents = [('Source_' * 100 + '\n') * 5,
                'Results\n' + '1234567890 ' * 45,
                'Warning: ' * 100]
    for ax, content in zip(axes, contents):
        ax.text(.02, .98, content, va='top', transform=ax.transAxes,
                fontsize=11, linespacing=1.6)
    ReportingMixin._layout_report_text(fig, grid, axes)
    for dpi in (100, 150):
        fig.set_dpi(dpi)
        fig.canvas.draw()
        boxes = [ax.texts[0].get_window_extent(fig.canvas.get_renderer()) for ax in axes]
        assert boxes[0].y0 > boxes[1].y1
        assert boxes[1].y0 > boxes[2].y1
        assert all(box.x1 < fig.bbox.width * .34 for box in boxes)
        assert boxes[-1].y0 > 0
