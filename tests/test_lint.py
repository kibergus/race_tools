import subprocess
import os
import sys


def test_flake8_compliance() -> None:
    """
    Run flake8 on the race_tools library to ensure code style compliance.
    """
    # Current file is in race_tools/tests/test_lint.py
    # We want to run flake8 on race_tools relative to it
    race_tools_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Run flake8 on the current directory (race_tools root)
    result = subprocess.run(
        [sys.executable, '-m', 'flake8', '--exclude=venv,.venv,utils/venv,build,dist', '.'],
        cwd=race_tools_root,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        # If there are errors, we fail the test and show the output
        print('\n--- Flake8 Linter Errors ---\n')
        print(result.stdout)
        print('\n--- Flake8 Stderr ---\n')
        print(result.stderr)
        assert result.returncode == 0, f'Flake8 found lint errors:\n{result.stdout}'
