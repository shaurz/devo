import sys
import traceback
import threading
import functools

def call_task(task, func, args, kwargs):
    try:
        result = func(*args, **kwargs)
    except:
        exn = sys.exc_info()[1]
        tbstr = traceback.format_exc()
        task.set_failed(exn, tbstr)
    else:
        task.set_done(result)

def async_call_task(task, func, args, kwargs):
    thread = threading.Thread(target=call_task, args=(task, func, args, kwargs))
    thread.daemon = True
    thread.start()

class Scheduler(object):
    def do_call(self):
        raise NotImplementedError()

    def call(self, func, *args, **kwargs):
        self.do_call(func, args, kwargs)

    def async_call(self, func, *args, **kwargs):
        task = Task(self)
        async_call_task(task, func, args, kwargs)
        return task

WAITING, DONE, FAILED = range(3)

class TaskNotReady(Exception):
    pass

class TaskAlreadySet(Exception):
    pass

class Task(object):
    def __init__(self, scheduler, success=None, failure=None):
        self.scheduler = scheduler
        self.__on_success = success
        self.__on_failure = failure
        self.__status = WAITING
        self.__result = None
        self.__traceback = ""
        self.__cond = threading.Condition()

    def set_done(self, result):
        with self.__cond:
            if self.__status != WAITING:
                raise TaskAlreadySet()
            self.__result = result
            self.__status = DONE
            if self.__on_success is not None:
                self.scheduler.call(self.__on_success, result)

    def set_failed(self, exn, traceback=""):
        with self.__cond:
            if self.__status != WAITING:
                raise TaskAlreadySet()
            self.__result = exn
            self.__traceback = traceback
            self.__status = FAILED
            if self.__on_failure is not None:
                self.scheduler.call(self.__on_failure, exn, traceback)
            else:
                if traceback:
                    print traceback
                else:
                    print "Error: Task Failed:", exn

    def wait(self):
        with self.__cond:
            while self.__status == WAITING:
                self.__cond.wait()
            if self.__status == DONE:
                return self.__result
            elif self.__status == FAILED:
                raise self.__result

    @property
    def ready(self):
        with self.__cond:
            return self.__status != WAITING

    @property
    def failed(self):
        with self.__cond:
            if self.__status == WAITING:
                raise TaskNotReady()
            return self.__status == FAILED

    @property
    def result(self):
        with self.__cond:
            if self.__status == WAITING:
                raise TaskNotReady()
            return self.__result

    def success(self, success):
        with self.__cond:
            if self.__status == DONE:
                self.scheduler.call(success, self.__result)
            elif self.__status == WAITING:
                self.__on_success = success

    def failure(self, failure):
        with self.__cond:
            if self.__status == FAILED:
                self.scheduler.call(failure, self.__result, self.__traceback)
            elif self.__status == WAITING:
                self.__on_failure = failure

    success = property(fset=success)
    failure = property(fset=failure)

    def bind(self, success=None, failure=None):
        if success is not None:
            self.success = success
        if failure is not None:
            self.failure = failure

class CoroutineTask(Task):
    def __init__(self, scheduler, gen):
        Task.__init__(self, scheduler)
        self.__gen = gen
        self.__next(self.__gen.next)

    def __next(self, func, *args, **kwargs):
        try:
            ret = func(*args, **kwargs)
        except StopIteration:
            self.set_done(None)
        except Exception, exn:
            self.set_failed(exn, traceback.format_exc())
        else:
            if isinstance(ret, Task):
                ret.bind(self.on_success, self.on_failure)
            else:
                self.set_done(ret)

    def on_success(self, result):
        self.__next(self.__gen.send, result)

    def on_failure(self, exn, traceback):
        self.__next(self.__gen.throw, exn)

def coroutine(scheduler, func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return CoroutineTask(scheduler, func(*args, **kwargs))
    return wrapper

def coroutine_func(func):
    @functools.wraps(func)
    def wrapper(scheduler, *args, **kwargs):
        return CoroutineTask(scheduler, func(*args, **kwargs))
    return wrapper

def coroutine_method(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        return CoroutineTask(self.scheduler, method(self, *args, **kwargs))
    return wrapper
