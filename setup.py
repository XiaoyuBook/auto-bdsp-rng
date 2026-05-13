import sys
from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext

extra_compile_args = []
if sys.platform == "win32":
    extra_compile_args.append("/utf-8")  # MSVC 中文环境强制 UTF-8 源文件编码

ext_modules = [
    Pybind11Extension(
        "auto_bdsp_rng.rng_core._native",
        [
            "src/auto_bdsp_rng/rng_core/native/bindings.cpp",
            "src/auto_bdsp_rng/rng_core/native/generator.cpp",
        ],
        cxx_std=17,
        include_dirs=["src/auto_bdsp_rng/rng_core/native"],
        extra_compile_args=extra_compile_args,
    ),
]

setup(
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
