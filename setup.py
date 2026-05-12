from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext

ext_modules = [
    Pybind11Extension(
        "auto_bdsp_rng.rng_core._native",
        [
            "src/auto_bdsp_rng/rng_core/native/bindings.cpp",
            "src/auto_bdsp_rng/rng_core/native/generator.cpp",
        ],
        cxx_std=17,
        include_dirs=["src/auto_bdsp_rng/rng_core/native"],
    ),
]

setup(
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
