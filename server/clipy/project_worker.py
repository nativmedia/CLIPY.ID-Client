"""Run one project in an isolated, interruptible process."""

import sys
import traceback
from pathlib import Path

from . import projects


def main() -> None:
    directory, project_id, *retry = sys.argv[1:]
    projects.DATA_DIR = Path(directory)
    projects._load()
    p = projects.get(project_id)
    if p is None:
        raise FileNotFoundError(project_id)
    try:
        if retry:
            projects._retry(p, int(retry[0]))
        else:
            projects._run(p)
    except Exception as e:
        traceback.print_exc()
        projects._fail(p, str(e) or type(e).__name__)


if __name__ == "__main__":
    main()
