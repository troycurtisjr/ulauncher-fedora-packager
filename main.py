import logging
import os.path
import requests
from datetime import datetime, timedelta
import koji
import bodhi.client.bindings

from ulauncher.api.client.Extension import Extension
from ulauncher.api.client.EventListener import EventListener
from ulauncher.api.shared.event import KeywordQueryEvent, ItemEnterEvent
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem
from ulauncher.api.shared.action.RenderResultListAction import RenderResultListAction
from ulauncher.api.shared.action.HideWindowAction import HideWindowAction
from ulauncher.api.shared.action.OpenUrlAction import OpenUrlAction
from ulauncher.api.shared.action.SetUserQueryAction import SetUserQueryAction

logger = logging.getLogger("fedora-packager")

NS_ORDER = {"rpms": 0, "fork": 1}

"""
Example session:

fpkg<pause>
    list user's current packages

fpkg ra<pause>
    searches for packages matching ra

fpkg rarian <pause> (note the trailing space)
    Returns list of possible package actions
    - Goto package source
    - Get package builds
    - Get package bugs
    - Get package updates

fpkg rarian builds<pause>
    Returns list of recent builds for the given package
     - Each entry will go to build status page when selected

fpkg rarian bugs<pause>
    Returns list of latest bugs for the given package
     - Each entry will go to bug status page when selected

fpkg rarian updates<pause>
    Returns list of latest updates for the given package
     - Each entry will go to update status page when selected

"""


class FedoraPackagerExtension(Extension):
    def __init__(self):
        super().__init__()
        self.subscribe(KeywordQueryEvent, KeywordQueryEventListener())


def search_pkg_src(keyword, search_arg):

    res = requests.get(
        "https://src.fedoraproject.org/api/0/projects",
        params={
            "pattern": f"*{search_arg.strip()}*",
            "short": 1,
            "per_page": 20,
            "fork": False,
        },
    )
    logger.debug("Request URI: %s", res.url)
    items = []
    # Only ever use the first page, since more than 20 results won't be
    # useful in ulauncher anyway.
    # on_enter=OpenUrlAction(f"https://src.fedoraproject.org/{project['fullname']}")))
    for project in res.json().get("projects", []):
        items.append(
            ExtensionResultItem(
                icon="images/pagure.png",
                keyword=keyword,
                name=project["fullname"],
                description=project["description"],
                on_enter=SetUserQueryAction(keyword + " " + project["name"] + " "),
            )
        )

    items = sorted(
        items, key=lambda x: (NS_ORDER.get(x.name.split("/")[0], 2), len(x.name))
    )
    if not items:
        items.append(
            ExtensionResultItem(
                icon="images/pagure.png",
                keyword=keyword,
                name="Nothing found",
                description=res.url,
                on_enter=HideWindowAction(),
            )
        )

    return RenderResultListAction(items)


def get_this_user():
    with open(os.path.expanduser("~/.fedora.upn"), encoding="utf8") as fp:
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
        items.append(
            ExtensionResultItem(
                icon="images/pagure.png",
                keyword=kw,
                name=project["fullname"],
                description=project["description"],
                on_enter=SetUserQueryAction(kw + " " + project["name"] + " "),
            )
        )

    return RenderResultListAction(items)


def get_builds(keyword, package):
    session = koji.ClientSession("https://koji.fedoraproject.org/kojihub")
    pkginfo = session.getPackage(package)
    if not pkginfo:
        return [
            ExtensionResultItem(
                icon="images/koji.png",
                name=f"Nothing found for {package}",
                description="",
                on_enter=HideWindowAction(),
            )
        ]
    pkg_id = pkginfo["id"]
    build_age_days = 7
    cutoff_dt = datetime.now() - timedelta(days=build_age_days)
    cutoff_ts = int(cutoff_dt.timestamp())
    items = []
    for build in session.listBuilds(packageID=pkg_id, createdAfter=cutoff_ts):
        name = "%s [%s]" % (
            build.get("nvr", "Unknown"),
            koji.BUILD_STATES[build["state"]],
        )
        useful_time_ts = (
            build.get("completion_ts")
            or build.get("start_ts")
            or build.get("creation_ts")
        )
        useful_time_dt = datetime.fromtimestamp(useful_time_ts).astimezone()
        useful_time = useful_time_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        user = build.get("owner_name") or build.get("owner_id", "Unknown User")
        item = ExtensionResultItem(
            icon="images/koji.png",
            keyword=keyword,
            name=name,
            description=f"{user} - {useful_time}",
            on_enter=OpenUrlAction(
                f"https://koji.fedoraproject.org/koji/buildinfo?buildID={build['build_id']}"
            ),
        )
        item.sort_key = (datetime.now().astimezone() - useful_time_dt).total_seconds()
        items.append(item)

    if not items:
        items.append(
            ExtensionResultItem(
                icon="images/koji.png",
                keyword=keyword,
                name="Nothing found",
                description=f"No recent ({build_age_days} days) builds found",
                on_enter=HideWindowAction(),
            )
        )
    else:
        items = sorted(items, key=lambda x: x.sort_key)

    return RenderResultListAction(items)


def get_updates(keyword, package):
    session = bodhi.client.bindings.BodhiClient()
    res = session.query(packages=package)
    items = []
    for update in res.get("updates", []):
        items.append(
            ExtensionResultItem(
                icon="images/bodhi.png",
                keyword=keyword,
                name=f"{update['title']} ({update['status']})",
                description="{} - {} - karma: {}".format(
                    update["date_submitted"], update["user"]["name"], update["karma"]
                ),
                on_enter=OpenUrlAction(update["url"]),
            )
        )

    if not items:
        items.append(
            ExtensionResultItem(
                icon="images/bodhi.png",
                keyword=keyword,
                name="Nothing found",
                description="No current updates found",
                on_enter=HideWindowAction(),
            )
        )
    else:
        items = sorted(items, key=lambda x: x.description, reverse=True)

    return RenderResultListAction(items)


def get_package_options(keyword, package):
    return [
        ExtensionResultItem(
            icon="images/pagure.png",
            keyword=keyword,
            name=f"Goto {package} src",
            description=f"Open the dist-git for {package} in a browser.",
            on_enter=OpenUrlAction(f"https://src.fedoraproject.org/rpms/{package}"),
        ),
        ExtensionResultItem(
            icon="images/koji.png",
            keyword=keyword,
            name=f"{keyword} {package} builds",
            description=f"Lists recent builds for {package}",
            on_enter=SetUserQueryAction(f"{keyword} {package} builds"),
        ),
        ExtensionResultItem(
            icon="images/bodhi.png",
            keyword=keyword,
            name=f"{keyword} {package} updates",
            description=f"Lists recent updates for {package}",
            on_enter=SetUserQueryAction(f"{keyword} {package} updates"),
        ),
    ]


def option_from_result(result):
    return result.name.split()[-1]


class KeywordQueryEventListener(EventListener):
    def on_event(self, event, extension):
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
            # For exact matches execute the appropriate option
            if args[1] == "builds":
                return get_builds(kw, args[0])
            if args[1] == "updates":
                return get_updates(kw, args[0])

            # If there was not match, filter the option list down to matching option names
            filt = args[1]
            opts = get_package_options(kw, args[0])
            opts = [opt for opt in opts if option_from_result(opt).startswith(filt)]
            return opts


if __name__ == "__main__":
    FedoraPackagerExtension().run()
