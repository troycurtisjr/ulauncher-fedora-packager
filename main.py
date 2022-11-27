import logging
import requests

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


class KeywordQueryEventListener(EventListener):

    def on_event(self, event, extension):
        search_term = event.get_argument()
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

        return RenderResultListAction(items)


if __name__ == '__main__':
    FedoraPackagerExtension().run()
