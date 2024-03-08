# ---------------------------------------------------------------------------
# schedule.py
# ---------------------------------------------------------------------------
import datetime
import functools
import random
import re
import time
import json
import uasyncio as asyncio
from datetime import timezone

try:
    import logging
    log = logging.getLogger(__name__)
except ImportError:
    class logging:
        def critical(self, entry):
            print('CRITICAL: ' + entry)
        def error(self, entry):
            print('ERROR: ' + entry)
        def warning(self, entry):
            print('WARNING: ' + entry)
        def info(self, entry):
            print('INFO: ' + entry)
        def debug(self, entry, value=None):
            if value:
                print('DEBUG: ' + entry % value)
            else:
                print('DEBUG: ' + entry)
    log = logging()

class ScheduleError(Exception):
    """Base schedule exception"""

    pass


class ScheduleValueError(ScheduleError):
    """Base schedule value error"""

    pass


class IntervalError(ScheduleValueError):
    """An improper interval was used"""

    pass


class CancelJob(object):
    """
    Can be returned from a job to unschedule itself.
    """
    pass


class Scheduler(object):
    """
    Objects instantiated by the :class:`Scheduler <Scheduler>` are
    factories to create jobs, keep record of scheduled jobs and
    handle their execution.
    """

    def __init__(self) -> None:
        self.jobs = []
        self.tasks = []
        self.tz = timezone.utc

    async def run_pending(self) -> None:
        """
        Run all jobs that are scheduled to run.

        Please note that it is *intended behavior that run_pending()
        does not run missed jobs*. For example, if you've registered a job
        that should run every minute and you only call run_pending()
        in one hour increments then your job won't be run 60 times in
        between but only once.
        """
        runnable_jobs = (job for job in self.jobs if job.should_run)
        for job in sorted(runnable_jobs):
            self._run_job(job)

        self.tasks = [task for task in self.tasks if not task.done()]

        if self.tasks:
            log.debug(f"Jobs to await: {len(self.tasks)}")
            await asyncio.gather(*self.tasks)


    def run_all(self, delay_seconds: int = 0) -> None:
        """
        Run all jobs regardless if they are scheduled to run or not.

        A delay of `delay` seconds is added between each job. This helps
        distribute system load generated by the jobs more evenly
        over time.

        :param delay_seconds: A delay added between every executed job
        """
        log.debug(
            "Running *all* %i jobs with %is delay in between",
            len(self.jobs),
            delay_seconds,
        )
        for job in self.jobs[:]:
            self._run_job(job)
            time.sleep(delay_seconds)

    def get_jobs(self):
        """
        Gets scheduled jobs
        """
        return self.jobs

    def clear(self) -> None:
        """
        Deletes scheduled jobs marked with the given tag, or all jobs
        if tag is omitted.

        :param tag: An identifier used to identify a subset of
                    jobs to delete
        """

        log.debug("Deleting *all* jobs")
        del self.jobs[:]

    def cancel_job(self, job: "Job") -> None:
        """
        Delete a scheduled job.

        :param job: The job to be unscheduled
        """
        try:
            log.debug('Cancelling job "%s"', str(job))
            self.jobs.remove(job)
        except ValueError:
            log.debug('Cancelling not-scheduled job "%s"', str(job))

    def every(self, interval: int = 1) -> "Job":
        """
        Schedule a new periodic job.

        :param interval: A quantity of a certain time unit
        :return: An unconfigured :class:`Job <Job>`
        """
        job = Job(interval, self)
        return job

    def _run_job(self, job: "Job") -> None:
        ret = job.run()
        if isinstance(ret, CancelJob) or ret is CancelJob:
            self.cancel_job(job)

    def get_next_run(self):
        """
        Datetime when the next job should run.

        :return: A :class:`~datetime.datetime` object
                 or None if no jobs scheduled
        """
        if not self.jobs:
            return None
        jobs_filtered = self.get_jobs()
        if not jobs_filtered:
            return None
        return min(jobs_filtered).next_run

    next_run = property(get_next_run)

    @property
    def idle_seconds(self):
        """
        :return: Number of seconds until
                 :meth:`next_run <Scheduler.next_run>`
                 or None if no jobs are scheduled
        """
        if not self.next_run:
            return None
        return (self.next_run - datetime.datetime.now(tz=self.tz)).total_seconds()


class Job(object):
    """
    A periodic job as used by :class:`Scheduler`.

    :param interval: A quantity of a certain time unit
    :param scheduler: The :class:`Scheduler <Scheduler>` instance that
                      this job will register itself with once it has
                      been fully configured in :meth:`Job.do()`.

    Every job runs at a given fixed time interval that is defined by:

    * a :meth:`time unit <Job.second>`
    * a quantity of `time units` defined by `interval`

    A job is usually created and returned by :meth:`Scheduler.every`
    method, which also defines its `interval`.
    """

    def __init__(self, interval: int, scheduler: Scheduler = None):
        self.interval: int = interval  # pause interval * unit between runs
        self.latest = None  # upper limit to the interval
        self.job_func = None  # the job job_func to run

        # Callable args
        self.args = None

        # Callable kwargs
        self.kwargs = None

        # Callable name
        self.job_func_name = None

        # time units, e.g. 'minutes', 'hours', ...
        self.unit = None

        # optional time at which this job runs
        self.at_time = None

        # optional time zone of the self.at_time field. Only relevant when at_time is not None
        self.at_time_zone = None

        # datetime of the last run
        self.last_run = None

        # datetime of the next run
        self.next_run = None

        # timedelta between runs, only valid for
        self.period = None

        # Specific day of the week to start on
        self.start_day = None

        # optional time of final run
        self.cancel_after = None

        self.scheduler = scheduler  # scheduler to register with

    def __lt__(self, other) -> bool:
        """
        PeriodicJobs are sortable based on the scheduled time they
        run next.
        """
        return self.next_run < other.next_run

    @property
    def second(self):
        if self.interval != 1:
            raise IntervalError("Use seconds instead of second")
        return self.seconds

    @property
    def seconds(self):
        self.unit = "seconds"
        return self

    @property
    def minute(self):
        if self.interval != 1:
            raise IntervalError("Use minutes instead of minute")
        return self.minutes

    @property
    def minutes(self):
        self.unit = "minutes"
        return self

    @property
    def hour(self):
        if self.interval != 1:
            raise IntervalError("Use hours instead of hour")
        return self.hours

    @property
    def hours(self):
        self.unit = "hours"
        return self

    @property
    def day(self):
        if self.interval != 1:
            raise IntervalError("Use days instead of day")
        return self.days

    @property
    def days(self):
        self.unit = "days"
        return self

    @property
    def week(self):
        if self.interval != 1:
            raise IntervalError("Use weeks instead of week")
        return self.weeks

    @property
    def weeks(self):
        self.unit = "weeks"
        return self

    @property
    def monday(self):
        if self.interval != 1:
            raise IntervalError(
                "Scheduling .monday() jobs is only allowed for weekly jobs. "
                "Using .monday() on a job scheduled to run every 2 or more weeks "
                "is not supported."
            )
        self.start_day = "monday"
        return self.weeks

    @property
    def tuesday(self):
        if self.interval != 1:
            raise IntervalError(
                "Scheduling .tuesday() jobs is only allowed for weekly jobs. "
                "Using .tuesday() on a job scheduled to run every 2 or more weeks "
                "is not supported."
            )
        self.start_day = "tuesday"
        return self.weeks

    @property
    def wednesday(self):
        if self.interval != 1:
            raise IntervalError(
                "Scheduling .wednesday() jobs is only allowed for weekly jobs. "
                "Using .wednesday() on a job scheduled to run every 2 or more weeks "
                "is not supported."
            )
        self.start_day = "wednesday"
        return self.weeks

    @property
    def thursday(self):
        if self.interval != 1:
            raise IntervalError(
                "Scheduling .thursday() jobs is only allowed for weekly jobs. "
                "Using .thursday() on a job scheduled to run every 2 or more weeks "
                "is not supported."
            )
        self.start_day = "thursday"
        return self.weeks

    @property
    def friday(self):
        if self.interval != 1:
            raise IntervalError(
                "Scheduling .friday() jobs is only allowed for weekly jobs. "
                "Using .friday() on a job scheduled to run every 2 or more weeks "
                "is not supported."
            )
        self.start_day = "friday"
        return self.weeks

    @property
    def saturday(self):
        if self.interval != 1:
            raise IntervalError(
                "Scheduling .saturday() jobs is only allowed for weekly jobs. "
                "Using .saturday() on a job scheduled to run every 2 or more weeks "
                "is not supported."
            )
        self.start_day = "saturday"
        return self.weeks

    @property
    def sunday(self):
        if self.interval != 1:
            raise IntervalError(
                "Scheduling .sunday() jobs is only allowed for weekly jobs. "
                "Using .sunday() on a job scheduled to run every 2 or more weeks "
                "is not supported."
            )
        self.start_day = "sunday"
        return self.weeks

    def at(self, time_str: str):

        """
        Specify a particular time that the job should be run at.

        :param time_str: A string in one of the following formats:

            - For daily jobs -> `HH:MM:SS` or `HH:MM`
            - For hourly jobs -> `MM:SS` or `:MM`
            - For minute jobs -> `:SS`

            The format must make sense given how often the job is
            repeating; for example, a job that repeats every minute
            should not be given a string in the form `HH:MM:SS`. The
            difference between `:MM` and `:SS` is inferred from the
            selected time-unit (e.g. `every().hour.at(':30')` vs.
            `every().minute.at(':30')`).


        :return: The invoked job instance
        """
        if self.unit not in ("days", "hours", "minutes") and not self.start_day:
            raise ScheduleValueError(
                "Invalid unit (valid units are `days`, `hours`, and `minutes`)"
            )

        if not isinstance(time_str, str):
            raise TypeError("at() should be passed a string")
        if self.unit == "days" or self.start_day:
            if not re.match(r"^[0-2]\d:[0-5]\d(:[0-5]\d)?$", time_str):
                raise ScheduleValueError(
                    "Invalid time format for a daily job (valid format is HH:MM(:SS)?)"
                )
        if self.unit == "hours":
            if not re.match(r"^([0-5]\d)?:[0-5]\d$", time_str):
                raise ScheduleValueError(
                    "Invalid time format for an hourly job (valid format is (MM)?:SS)"
                )

        if self.unit == "minutes":
            if not re.match(r"^:[0-5]\d$", time_str):
                raise ScheduleValueError(
                    "Invalid time format for a minutely job (valid format is :SS)"
                )
        time_values = time_str.split(":")
        hour = None
        minute = None
        second = None
        if len(time_values) == 3:
            hour, minute, second = time_values
        elif len(time_values) == 2 and self.unit == "minutes":
            hour = 0
            minute = 0
            _, second = time_values
        elif len(time_values) == 2 and self.unit == "hours" and len(time_values[0]):
            hour = 0
            minute, second = time_values
        else:
            hour, minute = time_values
            second = 0
        if self.unit == "days" or self.start_day:
            hour = int(hour)
            if not (0 <= hour <= 23):
                raise ScheduleValueError(
                    "Invalid number of hours ({} is not between 0 and 23)"
                )
        elif self.unit == "hours":
            hour = 0
        elif self.unit == "minutes":
            hour = 0
            minute = 0
        hour = int(hour)
        minute = int(minute)
        second = int(second)
        self.at_time = datetime.time(hour, minute, second)
        return self

    def to(self, latest: int):
        """
        Schedule the job to run at an irregular (randomized) interval.

        The job's interval will randomly vary from the value given
        to  `every` to `latest`. The range defined is inclusive on
        both ends. For example, `every(A).to(B).seconds` executes
        the job function every N seconds such that A <= N <= B.

        :param latest: Maximum interval between randomized job runs
        :return: The invoked job instance
        """
        self.latest = latest
        return self

    def until(
        self,
        until_time,
    ):
        """
        Schedule job to run until the specified moment.

        The job is canceled whenever the next run is calculated and it turns out the
        next run is after the until_time. The job is also canceled right before it runs,
        if the current time is after until_time. This latter case can happen when the
        the job was scheduled to run before until_time, but runs after until_time.

        If until_time is a moment in the past, ScheduleValueError is thrown.

        :param until_time: A moment in the future representing the latest time a job can
           be run. If only a time is supplied, the date is set to today.
           The following formats are accepted:

           - datetime.datetime
           - datetime.timedelta
           - datetime.time
           - String in one of the following formats: "%Y-%m-%d %H:%M:%S",
             "%Y-%m-%d %H:%M", "%Y-%m-%d", "%H:%M:%S", "%H:%M"
             as defined by strptime() behaviour. If an invalid string format is passed,
             ScheduleValueError is thrown.

        :return: The invoked job instance
        """

        if isinstance(until_time, datetime.datetime):
            self.cancel_after = until_time
        elif isinstance(until_time, datetime.timedelta):
            self.cancel_after = datetime.datetime.now(tz=timezone.utc) + until_time
        elif isinstance(until_time, datetime.time):
            self.cancel_after = datetime.datetime.combine(
                datetime.datetime.now(tz=timezone.utc), until_time
            )
        elif isinstance(until_time, str):
            cancel_after = self._decode_datetimestr(
                until_time,
                [
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d %H:%M",
                    "%Y-%m-%d",
                    "%H:%M:%S",
                    "%H:%M",
                ],
            )
            if cancel_after is None:
                raise ScheduleValueError("Invalid string format for until()")
            if "-" not in until_time:
                # the until_time is a time-only format. Set the date to today
                now = datetime.datetime.now(tz=timezone.utc)
                cancel_after = cancel_after.replace(
                    year=now.year, month=now.month, day=now.day
                )
            self.cancel_after = cancel_after
        else:
            raise TypeError(
                "until() takes a string, datetime.datetime, datetime.timedelta, "
                "datetime.time parameter"
            )
        if self.cancel_after < datetime.datetime.now(tz=timezone.utc):
            raise ScheduleValueError(
                "Cannot schedule a job to run until a time in the past"
            )
        return self

    def do(self, job_func, *args, **kwargs):
        """
        Specifies the job_func that should be called every time the
        job runs.

        Any additional arguments are passed on to job_func when
        the job runs.

        :param job_func: The function to be scheduled
        :return: The invoked job instance
        """
        self.args = args
        self.kwargs = kwargs
        self.job_func_name = job_func.__name__

        self._schedule_next_run()
        if self.scheduler is None:
            raise ScheduleError(
                "Unable to a add job to schedule. "
                "Job is not associated with an scheduler"
            )
        self.scheduler.jobs.append(self)
        return self

    @property
    def should_run(self) -> bool:
        """
        Check if a job is due to be run
        :return: ``True`` if the job should be run now.
        """
        assert self.next_run is not None, "must run _schedule_next_run before"
        return datetime.datetime.now(tz=timezone.utc) >= self.next_run

    def run(self):
        """
        Run the job and immediately reschedule it.
        If the job's deadline is reached (configured using .until()), the job is not
        run and CancelJob is returned immediately. If the next scheduled run exceeds
        the job's deadline, CancelJob is returned after the execution. In this latter
        case CancelJob takes priority over any other returned value.

        :return: The return value returned by the `job_func`, or CancelJob if the job's
                 deadline is reached.

        """
        if self._is_overdue(datetime.datetime.now(tz=timezone.utc)):
            log.debug("Cancelling job %s", self)
            return CancelJob

        log.debug("Running job %s", self)
        self.job_func = functools.partial(eval(self.job_func_name), *self.args, **self.kwargs)
        task = asyncio.create_task(self.job_func())
        self.scheduler.tasks.append(task)

        self.last_run = datetime.datetime.now(tz=timezone.utc)
        self._schedule_next_run()

        if self._is_overdue(self.next_run):
            log.debug("Cancelling job %s", self)
            return CancelJob
        return task

    def _schedule_next_run(self) -> None:
        """
        Compute the instant when this job should run next.
        """
        if self.unit not in ("seconds", "minutes", "hours", "days", "weeks"):
            raise ScheduleValueError(
                "Invalid unit (valid units are `seconds`, `minutes`, `hours`, "
                "`days`, and `weeks`)"
            )

        if self.latest is not None:
            if not (self.latest >= self.interval):
                raise ScheduleError("`latest` is greater than `interval`")
            interval = random.randint(self.interval, self.latest)
        else:
            interval = self.interval

        self.period = datetime.timedelta(**{self.unit: interval})
        if self.last_run:
            self.next_run = self.last_run + self.period
        elif self.next_run and not self.last_run:
            pass
        else:
            self.next_run = datetime.datetime.now(tz=timezone.utc) + self.period
        if self.start_day is not None:
            if self.unit != "weeks":
                raise ScheduleValueError("`unit` should be 'weeks'")
            weekdays = (
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            )
            if self.start_day not in weekdays:
                raise ScheduleValueError(
                    "Invalid start day (valid start days are {})".format(weekdays)
                )
            weekday = weekdays.index(self.start_day)
            days_ahead = weekday - self.next_run.weekday()
            if days_ahead <= 0:  # Target day already happened this week
                days_ahead += 7
            self.next_run += datetime.timedelta(days_ahead) - self.period
        if self.at_time is not None:
            if self.unit not in ("days", "hours", "minutes") and self.start_day is None:
                raise ScheduleValueError("Invalid unit without specifying start day")
            kwargs = {"second": self.at_time.second, "microsecond": 0}
            if self.unit == "days" or self.start_day is not None:
                kwargs["hour"] = self.at_time.hour
            if self.unit in ["days", "hours"] or self.start_day is not None:
                kwargs["minute"] = self.at_time.minute
            self.next_run = self.next_run.replace(**kwargs)  # type: ignore


            # Make sure we run at the specified time *today* (or *this hour*)
            # as well. This accounts for when a job takes so long it finished
            # in the next period.
            if not self.last_run or (self.next_run - self.last_run) > self.period:
                now = datetime.datetime.now(tz=timezone.utc)
                if (
                    self.unit == "days"
                    and self.at_time > now.time()
                    and self.interval == 1
                ):
                    self.next_run = self.next_run - datetime.timedelta(days=1)
                elif self.unit == "hours" and (
                    self.at_time.minute > now.minute
                    or (
                        self.at_time.minute == now.minute
                        and self.at_time.second > now.second
                    )
                ):
                    self.next_run = self.next_run - datetime.timedelta(hours=1)
                elif self.unit == "minutes" and self.at_time.second > now.second:
                    self.next_run = self.next_run - datetime.timedelta(minutes=1)
        if self.start_day is not None and self.at_time is not None:
            # Let's see if we will still make that time we specified today
            if (self.next_run - datetime.datetime.now(tz=timezone.utc)).days >= 7:
                self.next_run -= self.period

    def _is_overdue(self, when: datetime.datetime):
        return self.cancel_after is not None and when > self.cancel_after

    def _decode_datetimestr(
        self, datetime_str: str, formats
    ):
        for f in formats:
            try:
                return datetime.datetime.strptime(datetime_str, f)
            except ValueError:
                pass
        return None

    def asdict(self):
        # TODO: Document and add error handling

        data= {'interval': self.interval,
                'latest': self.latest,
                'args': self.args,
                'kwargs': self.kwargs,
                'job_func_name': self.job_func_name,
                'unit': self.unit,
                'start_day': self.start_day,
                'cancel_after': self.cancel_after}

        if self.at_time is not None:
            data['at_time'] = self.at_time.isoformat()

        if self.last_run is not None:
            data['last_run'] = self.last_run.isoformat()

        if self.next_run is not None:
            data['next_run'] = self.next_run.isoformat()

        return data


# The following methods are shortcuts for not having to
# create a Scheduler instance:

#: Default :class:`Scheduler <Scheduler>` object
default_scheduler = Scheduler()

#: Default :class:`Jobs <Job>` list
jobs = default_scheduler.jobs  # todo: should this be a copy, e.g. jobs()?


def every(interval: int = 1) -> Job:
    """Calls :meth:`every <Scheduler.every>` on the
    :data:`default scheduler instance <default_scheduler>`.
    """
    return default_scheduler.every(interval)


async def run_pending() -> None:
    """Calls :meth:`run_pending <Scheduler.run_pending>` on the
    :data:`default scheduler instance <default_scheduler>`.
    """
    await default_scheduler.run_pending()

def run_all(delay_seconds: int = 0) -> None:
    """Calls :meth:`run_all <Scheduler.run_all>` on the
    :data:`default scheduler instance <default_scheduler>`.
    """
    default_scheduler.run_all(delay_seconds=delay_seconds)


def get_jobs():
    """Calls :meth:`get_jobs <Scheduler.get_jobs>` on the
    :data:`default scheduler instance <default_scheduler>`.
    """
    return default_scheduler.get_jobs()


def clear() -> None:
    """Calls :meth:`clear <Scheduler.clear>` on the
    :data:`default scheduler instance <default_scheduler>`.
    """
    default_scheduler.clear()


def cancel_job(job: Job) -> None:
    """Calls :meth:`cancel_job <Scheduler.cancel_job>` on the
    :data:`default scheduler instance <default_scheduler>`.
    """
    default_scheduler.cancel_job(job)


def next_run():
    """Calls :meth:`next_run <Scheduler.next_run>` on the
    :data:`default scheduler instance <default_scheduler>`.
    """
    return default_scheduler.get_next_run()


def idle_seconds():
    """Calls :meth:`idle_seconds <Scheduler.idle_seconds>` on the
    :data:`default scheduler instance <default_scheduler>`.
    """
    return default_scheduler.idle_seconds


def repeat(job, *args, **kwargs):
    """
    Decorator to schedule a new periodic job.

    Any additional arguments are passed on to the decorated function
    when the job runs.

    :param job: a :class:`Jobs <Job>`
    """

    def _schedule_decorator(decorated_function):
        job.do(decorated_function, *args, **kwargs)
        return decorated_function

    return _schedule_decorator

def to_json():
    # TODO: Document and add error handling
    data = []

    for j in jobs:
        data.append(j.asdict())

    return json.dumps(data)

# datetime.EPOCH is 2000 on ESP32 vs 1970
# allow for scheduling and saving on unix port and uploading to ESP32
# or downloading to test
def parse_float_or_isotime(str):
    dt = None
    if (str is not None):
        try:

            # parse possible timestamp float value
            ts = float(str)
            dt = datetime.datetime.fromtimestamp(ts, tz=timezone.utc)
        except (TypeError, ValueError):

            # try ISO format
            try:
                dt = datetime.datetime.fromisoformat(str)
            except (ValueError):
                log.error("invalid date/time: %s", str)
    return dt

def from_json(data):
    # TODO: Document and add error handling

    for j in data:
        job = Job(interval=j['interval'], scheduler=default_scheduler)
        job.latest = j['latest']
        job.args = j['args']
        job.kwargs = j['kwargs']
        job.job_func_name = j['job_func_name']
        job.unit = j['unit']
        job.start_day = j['start_day']
        job.cancel_after = j['cancel_after']

        try:
            job.at_time = datetime.time.fromisoformat(j['at_time'])
        except:
            pass

        if ('last_run' in j):
            job.last_run = parse_float_or_isotime(j['last_run'])

        # next_run should have already been set and saved
        # avoid calling _schedule_next_run again, because if last_run is not set and the system is restarted then the job will wait for another interval
        if ('next_run' in j):
            job.next_run = parse_float_or_isotime(j['next_run'])
        if (job.next_run is None):
            try:
                job._schedule_next_run()
            except Exception as e:
                log.error("error calculating next_run: %s", repr(e))

        if (job.next_run is not None):
            #log.debug("next_run (scheduled): %s", job.next_run.isoformat())
            jobs.append(job)

    return jobs

async def run_forever(interval=1):
    while True:
        await default_scheduler.run_pending()
        await asyncio.sleep(interval)

def save_flash():
    # TODO: Document and add error handling
    f = open('schedule.json', "w+")
    f.write(to_json())
    f.close()

def load_flash(keep_existing=False):
    # TODO: Document
    # Clear existing jobs
    if not keep_existing:
        for jb in jobs:
            default_scheduler.cancel_job(jb)
    # Read jobs from flash
    try:
        from_json(json.load(open('schedule.json', "r")))
    except OSError:
        log.warning("Schedule file not found")
    except Exception as e:
        log.error("Error reading schedule file: %s", repr(e))