"""Independent oracle for the synthetic clamp contract; run after source review."""
import hashlib
import json
import platform
import py_compile
import sys
import time
from pathlib import Path


def main():
    candidate = Path(sys.argv[1]).resolve()
    evidence = Path(sys.argv[2]).resolve()
    started = time.perf_counter()
    raw = candidate.read_bytes()
    report = {"python": sys.version, "executable": sys.executable,
              "platform": platform.platform(), "candidate": str(candidate),
              "sha256": hashlib.sha256(raw).hexdigest(),
              "compile_passed": False, "cases": 0, "failures": []}
    try:
        py_compile.compile(str(candidate), doraise=True)
        report["compile_passed"] = True
        namespace = {}
        exec(compile(raw, str(candidate), "exec"), namespace)
        fn = namespace["clamp"]

        def check(value, lower, upper):
            report["cases"] += 1
            try:
                actual = fn(value, lower, upper)
            except ValueError:
                if lower <= upper:
                    report["failures"].append({"input": [value, lower, upper], "error": "unexpected ValueError"})
                return
            except Exception as exc:
                report["failures"].append({"input": [value, lower, upper], "error": repr(exc)})
                return
            if lower > upper:
                report["failures"].append({"input": [value, lower, upper], "error": "missing ValueError"})
                return
            # Oracle uses comparisons, independently of min/max implementation.
            expected = lower if value < lower else upper if value > upper else value
            if type(actual) is not int or actual != expected:
                report["failures"].append({"input": [value, lower, upper], "expected": expected, "actual": repr(actual)})

        for lower in range(-5, 6):
            for upper in range(-5, 6):
                for value in range(-10, 11):
                    check(value, lower, upper)
        huge = 10 ** 100
        for value, lower, upper in [(-huge, -huge + 1, huge), (huge, -huge, huge - 1),
                                   (0, -huge, huge), (huge, huge, huge),
                                   (-huge, -huge, -huge), (0, huge, -huge)]:
            check(value, lower, upper)
    except Exception as exc:
        report["setup_error"] = repr(exc)
    report["failure_count"] = len(report["failures"])
    report["elapsed_s"] = time.perf_counter() - started
    report["passed"] = report["compile_passed"] and report["cases"] == 2547 and not report["failures"] and "setup_error" not in report
    evidence.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ["candidate", "compile_passed", "cases", "failure_count", "passed", "elapsed_s"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
