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
from ulauncher.api.shared.action.SetUserQueryAction import SetUserQueryAction

logger = logging.getLogger("fedora-packager")

NS_ORDER = {
    "rpms": 0,
    "fork": 1
}

"""
Example session:

fpkg ra<pause>
    searches for packages matching ra

finfo<pause>
    list users current packages

finfo ra<pause>
    searches for packages matching ra

finfo rarian <pause> (note the trailing space)
    Returns list of possible package actions
    - Goto package source
    - Get package builds
    - Get package bugs
    - Get package updates

finfo rarian builds<pause>
    Returns list of recent builds for the given package
     - Each entry will go to build status page when selected

finfo rarian bugs<pause>
    Returns list of latest bugs for the given package
     - Each entry will go to bug status page when selected

finfo rarian updates<pause>
    Returns list of latest updates for the given package
     - Each entry will go to update status page when selected

"""

class FedoraPackagerExtension(Extension):

    def __init__(self):
        super().__init__()
        self.subscribe(KeywordQueryEvent, KeywordQueryEventListener())

def search_pkg_src(keyword, search_arg):

    res = requests.get("https://src.fedoraproject.org/api/0/projects",
                       params={
                           "pattern": f"*{search_arg.strip()}*",
                           "short": 1,
                           "per_page": 20,
                           "fork": False
                       })
    logger.debug("Request URI: %s", res.url)
    items = []
    # Only ever use the first page, since more than 20 results won't be
    # useful in ulauncher anyway.
    # on_enter=OpenUrlAction(f"https://src.fedoraproject.org/{project['fullname']}")))
    for project in res.json().get("projects", []):
        items.append(ExtensionResultItem(icon='images/icon.png',
                                         keyword=keyword,
                                         name=project["fullname"],
                                         description=project["description"],
                                         on_enter=SetUserQueryAction(keyword + " " + project["name"] + " ")))

    items = sorted(items,
                   key=lambda x: (NS_ORDER.get(x.name.split("/")[0], 2), len(x.name)))
    if not items:
        items.append(ExtensionResultItem(icon='images/icon.png',
                                         keyword=keyword,
                                         name="Nothing found",
                                         description=res.url,
                                         on_enter=HideWindowAction()))

    return RenderResultListAction(items)


def get_this_user():
    with open(os.path.expanduser("~/.fedora.upn"), encoding='utf8') as fp:
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

def return_project_list(event):
    projects = fetch_user_projects(get_this_user())
    kw = event.get_keyword()
    items = []
    for project in projects:
        items.append(ExtensionResultItem(icon="images/icon.png",
                                         keyword=kw,
                                         name=project["fullname"],
                                         description=project["description"],
                                         on_enter=SetUserQueryAction(kw + " " + project["name"] + " ")
                                         ))

    return RenderResultListAction(items)


def get_builds(keyword, package):
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
            keyword=keyword,
            name=name,
            description=f"{user} - {useful_time}",
            on_enter=OpenUrlAction(f"https://koji.fedoraproject.org/koji/buildinfo?buildID={build['build_id']}")
        )
        item.sort_key = (datetime.now().astimezone() - useful_time_dt).total_seconds()
        items.append(item)

    return RenderResultListAction(sorted(items, key=lambda x: x.sort_key))


def get_package_options(keyword, package):
    return RenderResultListAction([
        ExtensionResultItem(
            icon="images/icon.png",
            keyword=keyword,
            name=f"Goto {package} src",
            description=f"Open the dist-git for {package} in a browser.",
            on_enter=OpenUrlAction(f"https://src.fedoraproject.org/rpms/{package}")
        ),
        ExtensionResultItem(
            icon="images/icon.png",
            keyword=keyword,
            name=f"{keyword} {package} builds",
            description=f"Lists recent builds for {package}",
            on_enter=SetUserQueryAction(f"{keyword} {package} builds")
        ),
    ])


def pkg_actions(event):
    arg = event.get_argument()
    if not arg:
        return return_project_list(event)

    kw = event.get_keyword()
    args = arg.split()
    if len(args) == 1 and arg[-1] != " ":
        return search_pkg_src(kw, args[0])

    if len(args) == 1:
        return get_package_options(kw, args[0])

    if len(args) == 2:
        if args[1] == "builds":
            return get_builds(kw, args[0])


class KeywordQueryEventListener(EventListener):

    def on_event(self, event, extension):
        kw = event.get_keyword()
        if kw == "fpkg":
            return search_pkg_src(event)

        if kw == "finfo":
            return pkg_actions(event)


if __name__ == '__main__':
    FedoraPackagerExtension().run()
