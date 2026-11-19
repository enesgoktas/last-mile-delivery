"""Build script for the Cython evaluator extension.

Run from the backend/ directory:
    python app/algorithms/rich_vrp/local_search/evaluator_cpp/setup.py build_ext --inplace

Or via pip editable install if a top-level setup.py / pyproject.toml extension
list is configured.  For local development the --inplace form is simplest.
"""
import numpy as np
from Cython.Build import cythonize
from setuptools import Extension, setup

_PKG = "app.algorithms.rich_vrp.local_search.evaluator_cpp"
_SRC = "app/algorithms/rich_vrp/local_search/evaluator_cpp/_evaluator_core.pyx"

extensions = [
    Extension(
        f"{_PKG}._evaluator_core",
        sources=[_SRC],
        include_dirs=[np.get_include()],
        extra_compile_args=["-O3"],
    )
]

setup(
    name="rich_vrp_evaluator",
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
            "nonecheck": False,
        },
        annotate=False,
    ),
)
