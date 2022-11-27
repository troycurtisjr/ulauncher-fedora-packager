import logging
import os.path
import requests
from datetime import datetime, timedelta
import koji

from ulauncher.api.client.Extension import Extension
from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.shared.event import KeywordQueryEvent, ItemEnterEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.HideWindowAction import HideWindowAction
from ulauncher.api.shared.action.OpenUrlAction import OpenUrlAction

logger = logging.getLogger("fedora-packager")

NS_ORDER = {
    "rpms": 0,
    "fork": 1
}


class FedoraPackagerExtension(Extension):

    def __init__(self):
        super().__init__()
        self.subscribe(KeywordQueryEvent, KeywordQueryEventListener())

def search_pkg_src(search_term):
        if not search_term:
            return

        res = requests.get("https://src.fedoraproject.org/api/0/projects",
                           params={
                               "pattern": f"*{search_term.strip()}*",
                               "short": 1,
                               "per_page": 20
                           })
        logger.debug("Request URI: %s", res.url)
        items = []
        # Only ever use the first page, since more than 20 results won't be
        # useful in ulauncher anyway.
        for project in res.json().get("projects", []):
            items.append(ExtensionResultItem(icon='images/icon.png',
                                             name=project["fullname"],
                                             description=project["description"],
                                             on_enter=OpenUrlAction(f"https://src.fedoraproject.org/{project['fullname']}")))

        items = sorted(items,
                       key=lambda x: (NS_ORDER.get(x.name.split("/")[0], 2), len(x.name)))
        if not items:
            items.append(ExtensionResultItem(icon='images/icon.png',
                                             name="Nothing found",
                                             description=res.url,
                                             on_enter=HideWindowAction()))

        return items


def get_this_user():
    with open(os.path.expanduser("~/.fedora.upn")) as fp:
        return fp.read().strip()


def fetch_user_projects(user):
    projects = []
    fetch_url = f"https://src.fedoraproject.org/api/0/user/{user}"
    session = requests.session()
    while fetch_url:
        rsp = session.get(fetch_url)
        rsp_data = rsp.json()
        for repo in rsp_data.get("repos"):
            projects.append(repo)
        fetch_url = rsp_data.get("repos_pagination", {}).get("next")

    return projects

def return_project_list():
    projects = fetch_user_projects(get_this_user())
    items = []
    for project in projects:
        items.append(ExtensionResultItem(icon="images/icon.png",
                                         name=project["fullname"],
                                         description=project["description"],
                                         on_enter=OpenUrlAction(project["full_url"])
                                         ))

    return items


def get_builds(package):
    session = koji.ClientSession("https://koji.fedoraproject.org/kojihub")
    pkginfo = session.getPackage(package)
    if not pkginfo:
        return [ExtensionResultItem(icon="images/icon.png",
                                    name=f"Nothing found for {package}",
                                    description="",
                                    on_enter=HideWindowAction()
                                    )]
    pkg_id = pkginfo["id"]
    cutoff_dt = datetime.now() - timedelta(days=7)
    cutoff_ts = int(cutoff_dt.timestamp())
    items = []
    for build in session.listBuilds(packageID=pkg_id, createdAfter=cutoff_ts):
        name = "%s [%s]" % (build.get("nvr", "Unknown"), koji.BUILD_STATES[build["state"]])
        useful_time_ts = build.get("completion_ts") or build.get("start_ts") or build.get("creation_ts")
        useful_time_dt = datetime.fromtimestamp(useful_time_ts).astimezone()
        useful_time = useful_time_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        user = build.get("owner_name") or build.get("owner_id", "Unknown User")
        item = ExtensionResultItem(
            icon="images/icon.png",
            name=name,
            description=f"{user} - {useful_time}",
            on_enter=OpenUrlAction(f"https://koji.fedoraproject.org/koji/buildinfo?buildID={build['build_id']}")
        )
        item.sort_key = (datetime.now().astimezone() - useful_time_dt).total_seconds()
        items.append(item)

    return sorted(items, key=lambda x: x.sort_key)


def pkg_actions(arg):
    if not arg:
        return return_project_list()

    return get_builds(arg.strip())


def wrap_results(items, keyword):
    """
    Wrap a raw item list with RenderResultListAction, and make sure each item has the keyword
    for proper highlighting.
    """
    if not items:
        return None
    for item in items:
        item.keyword = keyword
    return RenderResultListAction(items)


class KeywordQueryEventListener(EventListener):

    def on_event(self, event, extension):
        kw = event.get_keyword()
        arg = event.get_argument()
        if kw == "fpkg":
            return wrap_results(search_pkg_src(arg), kw)

        if kw == "finfo":
            return wrap_results(pkg_actions(arg), kw)


if __name__ == '__main__':
    FedoraPackagerExtension().run()
