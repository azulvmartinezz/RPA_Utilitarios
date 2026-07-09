import contextlib
import io
import sys


class _LoggerWriter(io.TextIOBase):
    def __init__(self, logger):
        self.logger = logger
        self._buffer = ""

    def write(self, value):
        if not value:
            return 0

        self._buffer += value
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line:
                self.logger(line)
        return len(value)

    def flush(self):
        if self._buffer:
            self.logger(self._buffer)
            self._buffer = ""


@contextlib.contextmanager
def redirect_std_streams(logger):
    if logger is print:
        yield
        return

    stdout_writer = _LoggerWriter(logger)
    stderr_writer = _LoggerWriter(logger)
    with contextlib.redirect_stdout(stdout_writer), contextlib.redirect_stderr(stderr_writer):
        try:
            yield
        finally:
            stdout_writer.flush()
            stderr_writer.flush()
