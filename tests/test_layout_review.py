"""Geometry regressions for report centering and narrow metric strips."""
import shutil
from pathlib import Path
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from mpl_toolkits.mplot3d import proj3d
from PyQt6.QtCore import Qt,QRect
from PyQt6.QtWidgets import QLabel,QWidget,QStyle,QStyleOptionGroupBox
from tests.test_v507 import Base
from surface_analyzer.widgets import AdaptiveMetricLabel
from surface_analyzer.result_views import draw_four_views
from surface_analyzer.judgment import JudgmentSpecDialog
from surface_analyzer.recipe_dialog import RecipeLibraryDialog
from PyQt6.QtGui import QPalette


def projected_box(ax):
    points=np.array([(x,y,z) for x in ax.get_xlim3d() for y in ax.get_ylim3d() for z in ax.get_zlim3d()])
    projected=np.column_stack(proj3d.proj_transform(*points.T,ax.get_proj())[:2])
    return ax.transData.transform(projected)


def test_shared_colorbar_does_not_right_anchor_3d():
    fig=Figure(figsize=(17,10));FigureCanvasAgg(fig)
    x,y=np.meshgrid(np.linspace(-3,3,7),np.linspace(-2,2,5))
    axes=draw_four_views(fig,x.ravel(),y.ravel(),(.2+.001*x).ravel())
    fig.canvas.draw();ax=axes[0]
    box=ax.get_position(original=True);points=projected_box(ax)
    center=(points[:,0].min()+points[:,0].max())/2/fig.bbox.width
    assert abs(center-(box.x0+box.width/2)) < .015
    assert ax.get_anchor() == 'C'


class LayoutReview(Base):
    def test_sigma_startup_off_but_explicit_recipe_restores_choice(self):
        w=self.w
        self.assertNotIn('std3',w._single_metric_config()['visible'])
        recipe=w._current_recipe_dict();recipe['single_metrics']['visible'].append('std3')
        w.apply_recipe(recipe,remap_current_data=False,show_message=False)
        self.assertIn('std3',w._single_metric_config()['visible'])
        self.assertFalse(w._single_cards['std3'].isHidden())
        dialog=JudgmentSpecDialog(w,{},w._single_metric_config())
        self.assertTrue(dialog._single_rows['std3'][0].isChecked());dialog.close()
        recipe['single_metrics']['visible'].remove('std3');recipe['single_metrics']['limits']['std3']=0
        w.apply_recipe(recipe,remap_current_data=False,show_message=False)
        self.assertNotIn('std3',w._single_metric_config()['visible'])
        self.assertTrue(w._single_cards['std3'].isHidden())
        self.assertEqual(w.current_judgment('single')['verdict'],'unset')

    def test_narrow_module_titles_and_values_are_not_clipped(self):
        w=self.w;w.resize(1100,900);w.show()
        for index in (0,1,2):
            w.tabs.setCurrentIndex(index)
            for _ in range(5):self.qt.processEvents()
            for label in w.findChildren(QLabel):
                if not label.isVisible() or label.objectName() not in ('metricTitle','metricTitleAccent'):continue
                bounds=label.fontMetrics().boundingRect(QRect(0,0,label.contentsRect().width(),10000),Qt.TextFlag.TextWordWrap,label.text())
                self.assertLessEqual(bounds.height(),label.contentsRect().height()+2,label.text())
            verdict=w._judgment_widgets[('single','gap','parallel')[index]][1]
            self.assertIsInstance(verdict,AdaptiveMetricLabel)
        gap=w.lbl_gap_state.parentWidget().parentWidget().layout()
        self.assertGreater(gap.getItemPosition(gap.indexOf(w.lbl_gap_state.parentWidget()))[0],0)

    def test_main_report_applies_updated_grid_to_unmanaged_figure(self):
        self.load('report.csv',.4+.001*self.x)
        w=self.w;x,y,z=w.get_final_transformed_data(w.df_raw)
        fig=w._render_report_figure('report.csv',x,y,z,w.active_idx,w.compute_plane_metrics(x,y,z),0,'原始状态','关闭',w.import_info)
        fig.canvas.draw()
        ax=next(a for a in fig.axes if a.name=='3d')
        expected=ax.get_subplotspec().get_position(fig)
        actual=ax.get_position(original=True)
        self.assertAlmostEqual(actual.x0,expected.x0)
        self.assertAlmostEqual(actual.width,expected.width)
        self.assertLess(actual.x0,.4)
        plot_titles=[a.title.get_position()[1] for a in fig.axes if a.get_subplotspec() is not None and a.get_title() and a.get_subplotspec().get_topmost_subplotspec().rowspan.start==0]
        self.assertTrue(all(abs(y-plot_titles[0])<1e-6 for y in plot_titles))
        fig.clear()

    def test_recipe_search_contrast_survives_theme_round_trip(self):
        def brightness(color):
            v=[color.redF(),color.greenF(),color.blueF()]
            v=[a/12.92 if a<=.04045 else ((a+.055)/1.055)**2.4 for a in v]
            return sum(a*b for a,b in zip(v,(.2126,.7152,.0722)))
        def ratio(a,b):
            a,b=brightness(a),brightness(b)
            return (max(a,b)+.05)/(min(a,b)+.05)
        dialog=RecipeLibraryDialog(self.w);dialog.show()
        try:
            for mode in ('light','dark','light'):
                self.w.set_theme_preference(mode,persist=False)
                dialog.search.setText('Recipe 搜索 ABC123');dialog.search.setFocus()
                for _ in range(4):self.qt.processEvents()
                palette=dialog.search.palette()
                for foreground,background in ((QPalette.ColorRole.Text,QPalette.ColorRole.Base),(QPalette.ColorRole.PlaceholderText,QPalette.ColorRole.Base),(QPalette.ColorRole.HighlightedText,QPalette.ColorRole.Highlight)):
                    self.assertGreaterEqual(ratio(palette.color(foreground),palette.color(background)),4.5)
                if mode == 'dark':
                    self.assertNotEqual(palette.color(QPalette.ColorRole.Text),palette.color(QPalette.ColorRole.PlaceholderText))
                dialog.search.clear();self.qt.processEvents()
                self.assertTrue(dialog.search.placeholderText())
        finally:dialog.close();self.w.set_theme_preference('light',persist=False)

    def test_parallel_preview_remains_visible_in_narrow_window(self):
        self.load('base.csv',.4+.0001*self.y);self.w.set_parallel_surface('base')
        self.load('measure.csv',.5+.001*self.x);self.w.set_parallel_surface('measure')
        w=self.w;w._apply_parallel_result(w._compute_parallel_result())
        w.resize(1100,900);w.show();w.tabs.setCurrentIndex(2)
        for _ in range(5):self.qt.processEvents()
        canvas=w.parallel_canvas;canvas.draw()
        for ax in (canvas.ax_base,canvas.ax_measure):
            box=ax.get_window_extent(canvas.get_renderer())
            self.assertGreater(box.width,60)
            self.assertGreater(box.height,60)
            self.assertGreater(len(ax.collections),0)
        for label, source in canvas._source_headers:
            bounds=label.get_window_extent(canvas.get_renderer())
            self.assertGreaterEqual(bounds.x0,0)
            self.assertLessEqual(bounds.x1,canvas.fig.bbox.width)

    def test_parallel_scenes_fit_their_allocated_columns(self):
        self.load('base.csv',.4+.0001*self.y);self.w.set_parallel_surface('base')
        self.load('measure.csv',.5+.001*self.x);self.w.set_parallel_surface('measure')
        fig=self.w._render_parallel_report_figure();fig.canvas.draw()
        for ax in fig.axes:
            if ax.name!='3d':continue
            points=projected_box(ax);box=ax.get_position(original=True)
            self.assertGreaterEqual(points[:,0].min()/fig.bbox.width,box.x0-.005)
            self.assertLessEqual(points[:,0].max()/fig.bbox.width,box.x1+.005)
        fig.clear()


class VigGroupLayout(Base):
    def prepare_extensions(self):
        shutil.copytree(Path(__file__).resolve().parents[1]/'extensions/vig-image',self.root/'SurfaceRxyZxyAnalyzer/module_extensions/vig-image')

    def test_group_titles_have_reserved_space_above_controls(self):
        w=self.w;w.resize(1100,900);w.show();w.tabs.setCurrentIndex(3)
        for _ in range(5):self.qt.processEvents()
        module=w._vig_composite_module()
        for key in ('two','three','up'):
            group=module.set_buttons[key].parentWidget()
            option=QStyleOptionGroupBox();group.initStyleOption(option)
            title=group.style().subControlRect(QStyle.ComplexControl.CC_GroupBox,option,QStyle.SubControl.SC_GroupBoxLabel,group)
            children=group.findChildren(QWidget,options=Qt.FindChildOption.FindDirectChildrenOnly)
            self.assertTrue(children)
            self.assertGreater(min(child.y() for child in children),title.bottom())
