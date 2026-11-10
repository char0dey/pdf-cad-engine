# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Command-line entry point.

    python -I -m cad_engine <command> --in <file> [--out <file>] [--preview <png>]
                            [--meta <path>] [--budget-ms N] [--tmp <dir>]
                            [pdf2dxf: --arcs --hatch-fills --linetypes --merge --raster-fallback
                                      --scale asis|1-50|1-100|1-200 --units mm|inch]
                            [dxf2pdf: --page auto|a4|a3|a2|a1 --colors color|mono --layouts]
                            [probe-dxf: --light]
    python -I -m cad_engine --version

stdout carries exactly one JSON line (the meta) at the end of every run;
everything else (library chatter included) goes to stderr. See README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import traceback

from cad_engine import __version__, outputs
from cad_engine.budget import Budget
from cad_engine.errors import (
    EXIT_BUDGET,
    EXIT_FAILURE,
    EXIT_INPUT,
    EXIT_MEMORY,
    EXIT_OK,
    BudgetExceeded,
    EngineError,
    InputError,
    MemoryLimitExceeded,
)

COMMANDS = ("pdf2dxf", "dxf2pdf", "probe-pdf", "probe-dxf", "preview-dxf")
NEEDS_OUT = frozenset({"pdf2dxf", "dxf2pdf", "preview-dxf"})
PDF2DXF_FLAGS = ("arcs", "hatch_fills", "linetypes", "merge", "raster_fallback", "scale", "units")
DXF2PDF_FLAGS = ("page", "colors", "layouts")
PROBE_DXF_FLAGS = ("light",)
DEFAULT_BUDGET_MS = 90_000
DEFAULT_MEMORY_MB = 2048
MEMORY_ENV = "CAD_ENGINE_MEMORY_MB"  # operational override of the address-space limit
_OOM_MARKERS = ("malloc", "out of memory", "cannot allocate", "memory allocation")


class UsageError(EngineError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse would exit 2, which means "bad input" here
        raise UsageError(f"usage error: {message}")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="cad-engine", description="PDF <-> DXF conversion engine")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--in", dest="input", required=True, help="input file")
    parser.add_argument("--out", help="output file (pdf2dxf, dxf2pdf, preview-dxf)")
    parser.add_argument("--preview", help="PNG preview of the result (pdf2dxf)")
    parser.add_argument("--meta", help="also write the JSON meta to this file ('-' = stdout only)")
    parser.add_argument("--budget-ms", type=int, default=DEFAULT_BUDGET_MS, help="time budget in ms")
    parser.add_argument("--tmp", help="directory for temporary files")
    pdf = parser.add_argument_group("pdf2dxf options (all off by default)")
    pdf.add_argument("--arcs", action="store_true", default=None,
                     help="Bézier circles/arcs -> CIRCLE/ARC (0.5 %% of the radius)")
    pdf.add_argument("--hatch-fills", action="store_true", default=None,
                     help="fill-only paths -> solid HATCH")
    pdf.add_argument("--linetypes", action="store_true", default=None,
                     help="dash runs and PDF dash arrays -> DASHED/DOT linetypes")
    pdf.add_argument("--merge", action="store_true", default=None,
                     help="join collinear segments and curve pieces, drop duplicates")
    pdf.add_argument("--raster-fallback", action="store_true", default=None,
                     help="trace image-only pages (needs OpenCV, extra [raster])")
    pdf.add_argument("--scale", choices=("asis", "1-50", "1-100", "1-200"),
                     help="drawing scale (default asis)")
    pdf.add_argument("--units", choices=("mm", "inch"), help="DXF units (default mm)")
    dxf = parser.add_argument_group("dxf2pdf options")
    dxf.add_argument("--page", choices=("auto", "a4", "a3", "a2", "a1"), help="paper size (default auto)")
    dxf.add_argument("--colors", choices=("color", "mono"), help="colours (default color)")
    dxf.add_argument("--layouts", action="store_true", default=None,
                     help="paper-space layouts as extra pages after Model space")
    probe = parser.add_argument_group("probe-dxf options")
    probe.add_argument("--light", action="store_true", default=None,
                       help="content scan only, without loading the drawing (big files)")
    return parser


def new_meta() -> dict:
    return {
        "version": __version__,
        "command": None,
        "pages": None,
        "entities": None,
        "layers": None,
        "text_entities": None,
        "images": None,
        "has_vectors": None,
        "text_as_outlines": None,
        "traced_pages": None,
        "recognized": None,
        "scale": None,
        "units": None,
        "empty": None,
        "dxf_version": None,
        "page_size_mm": None,
        "layouts": None,
        "scanned": None,
        "missing": None,
        "ms_entities": None,
        "ms_drawable": None,
        "empty_reason": None,
        "simplified_hatches": None,
        "model_blank": None,
        "warnings": [],
        "ms": None,
        "code": None,
        "message": None,
    }


# -- process setup ---------------------------------------------------------------------------
def _isolate_stdout():
    """Point fd 1 at stderr (C libraries print there too) and keep a private stdout."""
    sys.stdout.flush()
    saved_fd = os.dup(1)
    os.dup2(2, 1)
    original = sys.stdout
    sys.stdout = sys.stderr
    return os.fdopen(saved_fd, "w", encoding="utf-8"), original


def _restore_stdout(stream, original) -> None:
    stream.flush()
    os.dup2(stream.fileno(), 1)
    stream.close()
    sys.stdout = original


def _limit_threads() -> None:
    # One conversion = one core; also keeps RLIMIT_AS away from per-thread BLAS arenas.
    for var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, "1")


def _limit_memory() -> None:
    try:
        import resource
    except ImportError:  # not Unix
        return
    try:
        limit_mb = int(os.environ.get(MEMORY_ENV, DEFAULT_MEMORY_MB))
    except ValueError:
        limit_mb = DEFAULT_MEMORY_MB
    if limit_mb <= 0:
        return
    limit = limit_mb * 1024 * 1024
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        if soft == resource.RLIM_INFINITY or soft > limit:
            resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    except (ValueError, OSError) as exc:  # macOS refuses or ignores RLIMIT_AS
        print(f"cad-engine: RLIMIT_AS not applied ({exc})", file=sys.stderr)


def _set_tmp(path: str | None) -> None:
    if not path:
        return
    os.makedirs(path, exist_ok=True)
    tempfile.tempdir = path
    for var in ("TMPDIR", "TMP", "TEMP"):
        os.environ[var] = path


def _check_paths(args: argparse.Namespace) -> None:
    if args.command in NEEDS_OUT and not args.out:
        raise UsageError(f"usage error: {args.command} requires --out")
    if args.preview and args.command != "pdf2dxf":
        raise UsageError("usage error: --preview is only supported by pdf2dxf")
    command_flags = (("pdf2dxf", PDF2DXF_FLAGS), ("dxf2pdf", DXF2PDF_FLAGS), ("probe-dxf", PROBE_DXF_FLAGS))
    for command, names in command_flags:
        used = [name for name in names if getattr(args, name) is not None]
        if used and args.command != command:
            flags = ", ".join("--" + name.replace("_", "-") for name in used)
            raise UsageError(f"usage error: {flags} only supported by {command}")
    src = os.path.abspath(args.input)
    for out in (args.out, args.preview):
        if out and os.path.abspath(out) == src:
            raise UsageError("usage error: output path equals the input path")
    if not os.path.isfile(args.input):
        raise EngineError(f"input file not found: {args.input}")
    try:
        with open(args.input, "rb") as fp:
            fp.read(1)
    except OSError as exc:
        raise EngineError(f"input file is not readable: {exc}") from exc


def _dispatch(args: argparse.Namespace, budget: Budget, meta: dict) -> None:
    if args.command == "pdf2dxf":
        from cad_engine.pdf2dxf import Options, convert

        options = Options(
            arcs=bool(args.arcs),
            hatch_fills=bool(args.hatch_fills),
            linetypes=bool(args.linetypes),
            merge=bool(args.merge),
            raster_fallback=bool(args.raster_fallback),
            scale=args.scale or "asis",
            units=args.units or "mm",
        )
        convert(args.input, args.out, budget=budget, meta=meta, preview_path=args.preview, options=options)
    elif args.command == "dxf2pdf":
        from cad_engine.dxf2pdf import convert as dxf_convert

        dxf_convert(args.input, args.out, budget=budget, meta=meta, page=args.page or "auto",
                    colors=args.colors or "color", layouts=bool(args.layouts))
    elif args.command == "probe-pdf":
        from cad_engine.probe import probe_pdf

        probe_pdf(args.input, budget=budget, meta=meta)
    elif args.command == "probe-dxf":
        from cad_engine.probe import probe_dxf

        probe_dxf(args.input, budget=budget, meta=meta, light=bool(args.light))
    elif args.command == "preview-dxf":
        from cad_engine.probe import preview_dxf

        preview_dxf(args.input, args.out, budget=budget, meta=meta)
    else:  # pragma: no cover - argparse restricts the choices
        raise UsageError(f"unknown command {args.command}")


def _is_oom(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _OOM_MARKERS)


def _fail(meta: dict, code: str, message: str) -> None:
    meta["code"] = code
    meta["message"] = message


def _run(argv: list[str], started: float, meta: dict, state: dict) -> int:
    """Parse, set limits, run the command. Returns the exit code."""
    budget: Budget | None = None
    try:
        args = build_parser().parse_args(argv)
        state["args"] = args
        meta["command"] = args.command
        _check_paths(args)
        if args.command in NEEDS_OUT:
            state["outputs"] = (args.out, args.preview)  # ours to remove if the run fails
        _limit_threads()
        _limit_memory()
        _set_tmp(args.tmp)
        budget = Budget(args.budget_ms, started)
        budget.arm()
        _dispatch(args, budget, meta)
        return EXIT_OK
    except InputError as exc:
        _fail(meta, exc.code, str(exc))
        return EXIT_INPUT
    except (MemoryError, MemoryLimitExceeded) as exc:
        detail = f": {exc}" if str(exc) else ""
        _fail(meta, MemoryLimitExceeded.code, f"memory limit exceeded{detail}")
        return EXIT_MEMORY
    except EngineError as exc:
        _fail(meta, EngineError.code, str(exc))
        print(f"cad-engine: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except Exception as exc:
        if _is_oom(exc):
            _fail(meta, MemoryLimitExceeded.code, f"memory limit exceeded: {exc}")
            return EXIT_MEMORY
        traceback.print_exc(file=sys.stderr)
        _fail(meta, EngineError.code, f"{type(exc).__name__}: {exc}")
        return EXIT_FAILURE
    finally:
        if budget is not None:
            budget.disarm()


def _emit(stream, meta: dict, meta_path: str | None) -> None:
    line = json.dumps(meta, ensure_ascii=True, separators=(",", ":"))
    stream.write(line + "\n")
    stream.flush()
    if meta_path and meta_path != "-":
        try:
            with open(meta_path, "w", encoding="utf-8") as fp:
                fp.write(line + "\n")
        except OSError as exc:
            print(f"cad-engine: cannot write --meta file: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--version" in argv:
        print(__version__)
        return EXIT_OK

    started = time.monotonic()
    stream, original_stdout = _isolate_stdout()
    meta = new_meta()
    state: dict = {}
    try:
        try:
            code = _run(argv, started, meta, state)
        except BudgetExceeded as exc:  # also catches an alarm that fires inside an except clause
            _fail(meta, BudgetExceeded.code, str(exc))
            code = EXIT_BUDGET
        args = state.get("args")
        if code != EXIT_OK and "outputs" in state:
            outputs.discard(*state["outputs"])
        meta["ms"] = int((time.monotonic() - started) * 1000)
        _emit(stream, meta, getattr(args, "meta", None))
        return code
    finally:
        _restore_stdout(stream, original_stdout)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
