# ruff: noqa
# mypy: ignore-errors
import subprocess, sys, os, concurrent.futures as cf

ids = [l.strip() for l in open(sys.argv[1]) if "::" in l]
env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.abspath(__file__)))


def run(i):
    try:
        r = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "pytest",
                "-p",
                "pytest_plugin",
                i,
                "-n",
                "0",
                "-q",
            ],
            env=env,
            capture_output=True,
            timeout=40,
        )
        return ("pass" if r.returncode == 0 else "FAIL"), i
    except subprocess.TimeoutExpired:
        return "HANG", i


with cf.ThreadPoolExecutor(6) as ex:
    for st, i in ex.map(run, ids):
        if st != "pass":
            print(st, i.split("::", 1)[1][:110])
print("done", len(ids))
