"""Enforce a shared wall-clock budget around native solver work in a child process."""
import multiprocessing
import time
import traceback


class BudgetExpired(TimeoutError):
    pass


def _worker(connection, function, args, kwargs):
    try:
        connection.send(('ok', function(*args, **kwargs)))
    except BaseException:
        connection.send(('error', traceback.format_exc()))
    finally:
        connection.close()


class RuntimeBudget:
    def __init__(self, seconds=None):
        if seconds is not None and seconds <= 0:
            raise ValueError('Time budget must be positive.')
        self.deadline = None if seconds is None else time.monotonic() + seconds

    def run(self, function, *args, **kwargs):
        if self.deadline is None:
            return function(*args, **kwargs)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BudgetExpired('Overall solver budget exhausted.')
        context = multiprocessing.get_context('spawn')
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_worker, args=(send, function, args, kwargs))
        process.start()
        send.close()
        try:
            remaining = max(0, self.deadline - time.monotonic())
            if not receive.poll(remaining):
                raise BudgetExpired('Overall solver budget exhausted.')
            try:
                status, result = receive.recv()
            except EOFError as error:
                raise RuntimeError('Solver worker exited without a result.') from error
            if status != 'ok':
                raise RuntimeError(result)
            return result
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            receive.close()
