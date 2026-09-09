"""Run pytest against an isolated PostgreSQL/pgvector cluster, then stop and remove it."""

import argparse
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
LOCAL = ROOT / ".local"
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def emit(output: str | bytes | None, password: str) -> None:
    if isinstance(output, bytes):
        output = output.decode(errors="replace")
    if output:
        output = re.sub(
            r"postgresql(?:\+[a-z0-9_]+)?://[^\s'\"<>]+", "[database URL redacted]", output
        )
        print(output.replace(password, "[redacted]"), end="" if output.endswith("\n") else "\n")


def command(
    arguments: list[str],
    environment: dict[str, str],
    password: str,
    *,
    timeout: int = 60,
    check: bool = True,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        # On Windows a daemon can retain its parent's pipe handles after pg_ctl exits.
        # Files let us wait for the command itself without waiting for daemon pipe EOF.
        with (
            tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout,
            tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr,
        ):
            completed = subprocess.run(
                arguments,
                cwd=BACKEND,
                env=environment,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
            stdout.seek(0)
            stderr.seek(0)
            result = subprocess.CompletedProcess(
                arguments, completed.returncode, stdout.read(), stderr.read()
            )
    except subprocess.TimeoutExpired as error:
        emit(error.stdout, password)
        emit(error.stderr, password)
        raise RuntimeError(f"{Path(arguments[0]).name} exceeded {timeout} seconds.") from None
    if not quiet or (check and result.returncode):
        emit(result.stdout, password)
        emit(result.stderr, password)
    if check and result.returncode:
        raise RuntimeError(f"{Path(arguments[0]).name} exited with code {result.returncode}.")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=LOCAL / "postgres-runtime",
        help="Existing PostgreSQL environment containing Library/bin (or bin).",
    )
    options, pytest_arguments = parser.parse_known_args()
    if pytest_arguments[:1] == ["--"]:
        pytest_arguments = pytest_arguments[1:]
    runtime = options.runtime_dir.resolve()
    binary_directory = runtime / "Library" / "bin" if os.name == "nt" else runtime / "bin"
    suffix = ".exe" if os.name == "nt" else ""
    binaries = {
        name: str(binary_directory / f"{name}{suffix}")
        for name in ("initdb", "pg_ctl", "createdb", "psql")
    }
    missing = [name for name, path in binaries.items() if not Path(path).is_file()]
    if missing:
        print(f"PostgreSQL runtime is missing {', '.join(missing)} in {binary_directory}.")
        return 2

    local = LOCAL.resolve()
    if not local.is_relative_to(ROOT.resolve()):
        print("Refusing a .local directory outside the project workspace.")
        return 2
    local.mkdir(exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="verify-postgres-", dir=local, delete=False)
    directory = Path(temporary.name).resolve()
    if not directory.is_relative_to(local):
        print("Refusing a temporary cluster outside the project .local directory.")
        return 2

    password = secrets.token_urlsafe(32)
    password_file = directory / "password.txt"
    password_file.write_text(password + "\n", encoding="utf-8")
    data = directory / "data"
    log = directory / "postgres.log"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    database_url = f"postgresql+psycopg://resolveai_test:{password}@127.0.0.1:{port}/resolveai_test"
    environment = {
        **os.environ,
        "PATH": str(binary_directory) + os.pathsep + os.environ.get("PATH", ""),
        "PGHOST": "127.0.0.1",
        "PGPORT": str(port),
        "PGUSER": "resolveai_test",
        "PGPASSWORD": password,
        "PGDATABASE": "resolveai_test",
        "APP_ENV": "test",
        "DATABASE_URL": database_url,
        "TEST_DATABASE_URL": database_url,
    }
    start_attempted = False
    safe_to_remove = True
    exit_code = 1
    try:
        command(
            [
                binaries["initdb"],
                "-D",
                str(data),
                "-U",
                "resolveai_test",
                "--encoding=UTF8",
                "--locale=C",
                "--auth=scram-sha-256",
                f"--pwfile={password_file}",
            ],
            environment,
            password,
            quiet=True,
        )
        password_file.unlink()
        start_attempted = True
        safe_to_remove = False
        command(
            [
                binaries["pg_ctl"],
                "-D",
                str(data),
                "-l",
                str(log),
                "-o",
                f"-h 127.0.0.1 -p {port}",
                "-t",
                "30",
                "-w",
                "start",
            ],
            environment,
            password,
        )
        command([binaries["createdb"], "resolveai_test"], environment, password)
        command(
            [
                binaries["psql"],
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-At",
                "-c",
                "CREATE EXTENSION vector; "
                "SELECT 'PostgreSQL ' || current_setting('server_version'); "
                "SELECT 'pgvector ' || extversion FROM pg_extension WHERE extname = 'vector';",
            ],
            environment,
            password,
        )
        print("Running pytest with an isolated PostgreSQL database.", flush=True)
        result = command(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(directory / "pytest"),
                *pytest_arguments,
            ],
            environment,
            password,
            timeout=300,
            check=False,
        )
        exit_code = result.returncode
    except (OSError, RuntimeError) as error:
        emit(str(error), password)
        if log.exists():
            emit(log.read_text(encoding="utf-8", errors="replace")[-8000:], password)
    finally:
        if start_attempted:
            try:
                command(
                    [binaries["pg_ctl"], "-D", str(data), "-m", "fast", "-t", "30", "-w", "stop"],
                    environment,
                    password,
                    check=False,
                    quiet=True,
                )
                status = command(
                    [binaries["pg_ctl"], "-D", str(data), "status"],
                    environment,
                    password,
                    check=False,
                    quiet=True,
                )
                safe_to_remove = status.returncode in (3, 4)
            except (OSError, RuntimeError) as error:
                emit(str(error), password)
        if safe_to_remove:
            try:
                if directory.resolve().is_relative_to(local):
                    temporary.cleanup()
                    print("PostgreSQL stopped; temporary cluster removed.")
                else:
                    raise RuntimeError("Temporary cluster path changed; cleanup refused.")
            except (OSError, RuntimeError) as error:
                emit(str(error), password)
                exit_code = exit_code or 1
        else:
            print(f"PostgreSQL shutdown could not be confirmed; cluster preserved at {data}.")
            exit_code = exit_code or 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
