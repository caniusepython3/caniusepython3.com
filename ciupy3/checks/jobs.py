import uuid
from django.db.models import F, Sum
from django.utils.timezone import now
from django.utils.six.moves import xmlrpc_client

from pip._vendor import requests
from django_rq import job
from caniusepython3.dependencies import blocking_dependencies
from caniusepython3.pypi import all_py3_projects

from .models import Check, Project, get_redis

TROVE_KEY_NAME = 'trove_classifiers_key'
TROVE_COUNT_KEY = 'compatible_count'
ALL_KEY_NAME = 'all_key'
ALL_COUNT_KEY = 'all_count'
CHECKED_COUNT_KEY = 'checked_count'
OVERRIDE_KEY = 'override'
OVERRIDE_URL = ('https://raw.github.com/brettcannon/caniusepython3/'
                'master/caniusepython3/overrides.json')


def all_projects():
    client = xmlrpc_client.ServerProxy('http://pypi.python.org/pypi')
    return client.list_packages()


@job('default')
def fetch_overrides():
    """
    A job to fetch the caniusepython3 CLI override json file from Github
    to simplify the overrides.
    """
    try:
        override_response = requests.get(OVERRIDE_URL)
    except ValueError:
        # just ignore it since there might be something wrong with the
        # request or Github is down or something else horrible
        pass
    else:
        redis = get_redis()
        override_json = override_response.json()
        redis.hmset(OVERRIDE_KEY, override_json)


@job('default')
def fetch_all_py3_projects():
    """
    A job to be run periodically (e.g. daily) to update the
    Python 3 compatible projects from PyPI.
    """
    redis = get_redis()
    with redis.lock('fetch_trove_classifiers'):
        # try to get the old fetch id first
        old_key_name = redis.get(TROVE_KEY_NAME)

        # then populate a set of Python 3 projects in Redis
        new_key_name = uuid.uuid4().hex
        projects = all_py3_projects(get_overrides())
        for project in projects:
            redis.sadd(new_key_name, str(project))
        redis.set(TROVE_KEY_NAME, new_key_name)

        # get rid of the old fetch set if needed
        if old_key_name is not None:
            redis.delete(old_key_name)

        # return number of Python 3 projects
        compatible_count = len(projects)
        redis.set(TROVE_COUNT_KEY, compatible_count)
        return compatible_count


@job('low')
def fetch_all_projects():
    """
    A job to be run periodically (e.g. daily) to update the projects from PyPI.
    """
    redis = get_redis()
    with redis.lock('fetch_all'):
        # try to get the old fetch id first
        old_key_name = redis.get(ALL_KEY_NAME)

        # then populate a set of Python 3 projects in Redis
        new_key_name = uuid.uuid4().hex
        projects = all_projects()
        for project in projects:
            redis.sadd(new_key_name, str(project))
        redis.set(ALL_KEY_NAME, new_key_name)

        # get rid of the old fetch set if needed
        if old_key_name is not None:
            redis.delete(old_key_name)

        # return number of Python 3 projects
        compatible_count = len(projects)
        redis.set(ALL_COUNT_KEY, compatible_count)
        return compatible_count


def get_compatible():
    redis = get_redis()
    return redis.get(TROVE_COUNT_KEY) or None


def get_total():
    redis = get_redis()
    return redis.get(ALL_COUNT_KEY) or None


def get_checked():
    redis = get_redis()
    return redis.get(CHECKED_COUNT_KEY) or None


def decode_name(name, lower=False):
    name = name.decode('utf-8')
    if lower:
        return name.lower()
    else:
        return name


def handle_projects(project_list, lower):
    projects = {}
    for project in project_list:
        decoded_project = decode_name(project)
        lower_decoded_project = decoded_project.lower()
        if lower:
            decoded_project = lower_decoded_project
        projects[decoded_project] = lower_decoded_project
    return projects


def get_all_py3_projects(lower=False):
    """
    Return the list of projects compatible to Python 3 according
    to PyPI
    """
    redis = get_redis()
    key_name = redis.get(TROVE_KEY_NAME)
    return handle_projects(redis.smembers(key_name), lower)


def get_all_projects(lower=False):
    """
    Return the list of all projects on PyPI
    """
    redis = get_redis()
    key_name = redis.get(ALL_KEY_NAME)
    return handle_projects(redis.smembers(key_name), lower)


def get_overrides():
    redis = get_redis()
    return dict((decode_name(project), decode_name(url))
                for project, url in redis.hgetall(OVERRIDE_KEY).items())


def get_or_fetch_all_projects(lower=False):
    """
    Get all projects stored in redis and if not available
    try to fetch it synchronysly, then fail if that errors as well.
    """
    projects = get_all_projects(lower)
    if not projects:
        fetch_all_projects()
        projects = get_all_projects(lower)
        if not projects:
            raise ValueError('Something went wrong while fetching '
                             'all projects from PyPI')
    return projects


def get_or_fetch_all_py3_projects():
    """
    Get the compatible projects stored in redis and if not available
    try to fetch it synchronysly, then fail if that errors as well.
    """
    projects = get_all_py3_projects()
    if not projects:
        fetch_all_py3_projects()
        projects = get_all_py3_projects()
        if not projects:
            raise ValueError('Something went wrong while fetching '
                             'the Python 3 projects from PyPI')
    return projects


@job('high')
def run_check(pk):
    """
    The central job to run the check. Called after a check has been
    created.
    """
    check = Check.objects.get(pk=pk)
    check.started_at = now()

    all_py3_projects = get_or_fetch_all_py3_projects()
    blockers = blocking_dependencies(check.projects, all_py3_projects)
    blockers_mapping = {}
    for blocker in sorted(blockers, key=lambda x: tuple(reversed(x))):
        blockers_mapping[blocker[0]] = blocker[1:]
    check.blockers = blockers_mapping

    flattened_blockers = set()
    for blocker_reasons in blockers:
        for blocker in blocker_reasons:
            flattened_blockers.add(blocker)
    check.unblocked = len(flattened_blockers)

    check.finished_at = now()
    check.runs = F('runs') + 1
    check.save()

    redis = get_redis()

    # the number of "publicly" announced checks is the sum of all runs
    # and the number of projects that have been created lazily
    public_checks = (Check.objects.filter(public=True)
                                  .aggregate(Sum('runs')))
    project_count = Project.objects.count()
    redis.set(CHECKED_COUNT_KEY, public_checks['runs__sum'] + project_count)
    return blockers


@job('low')
def check_all_projects():
    for project in Project.objects.all():
        project.check()


def real_project_name(value):
    all_projects = get_or_fetch_all_projects()
    all_projects_flipped = dict((value, key)
                                for key, value in all_projects.items())
    return all_projects_flipped.get(value.lower(), None)
