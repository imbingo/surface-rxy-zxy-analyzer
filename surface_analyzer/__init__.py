"""Surface Rxy/Zxy Analyzer V4.5.6 public package."""

from .version import APP_VERSION, SOURCE_BASE_VERSION, SOURCE_COMMIT, __version__


_API_EXPORTS = {
    "AnalysisOptions", "AnalysisResult", "analyze_xyz", "analyze_file", "compare_plane_results",
}


def __getattr__(name):
    if name not in _API_EXPORTS:
        raise AttributeError(name)
    from . import api
    value = getattr(api, name)
    globals()[name] = value
    return value

__all__ = [
    "APP_VERSION",
    "SOURCE_BASE_VERSION",
    "SOURCE_COMMIT",
    "__version__",
    "AnalysisOptions",
    "AnalysisResult",
    "analyze_xyz",
    "analyze_file",
    "compare_plane_results",
]
