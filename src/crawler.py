from typing import List, Dict
import time
from playwright.sync_api import sync_playwright
from queue import Queue
import copy
import json


class Crawler:
    def __init__(
        self,
        limit_to_viewport: bool = True,
        viewport_width: int = 1800,
        viewport_height: int = 1000,
    ) -> None:
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height

        self.browser = (
            sync_playwright()
            .start()
            .chromium.launch(
                # channel="msedge",
                headless=False,
                args=["--accept-lang=en-GB", "--lang=en-US"],
            )
        )
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.set_viewport_size(
            {"width": viewport_width, "height": viewport_height}
        )
        self.limit_to_viewport = limit_to_viewport

    def go_to_page(self, url) -> None:
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        try:
            self.page.goto(url, timeout=0)
        except Exception as er:
            print(er)
            return
        self.client = self.page.context.new_cdp_session(self.page)
        self.page_buffer = {}

    def click(self, _id: str) -> None:
        def handle_page(page):
            page.wait_for_load_state()
            print(" > switching to:", page.title())

            self.page = page
            self.page.set_viewport_size(
                {"width": self.viewport_width, "height": self.viewport_height}
            )
            self.client = self.page.context.new_cdp_session(self.page)
            self.page_buffer = {}

        if element := self.page_buffer.get(str(_id)):
            x = element.get("x_mid")
            y = element.get("y_mid")

            self.page.mouse.click(x, y)
            self.page.wait_for_load_state()
            self.context.on("page", handle_page)

        else:
            print(f" > there is no element {_id} in the page buffer")

    def back(self) -> None:
        def handle_page(page):
            page.wait_for_load_state()
            print(" > switching to:", page.title())

            self.page = page
            self.page.set_viewport_size(
                {"width": self.viewport_width, "height": self.viewport_height}
            )
            self.client = self.page.context.new_cdp_session(self.page)
            self.page_buffer = {}

        self.page.go_back()
        self.context.on("page", handle_page)

    def select(self, _id: str, value: str) -> None:
        # option: value attribute of an option
        if element := self.page_buffer.get(str(_id)):
            # todo: try
            self.page.select_option(f"select#{element['inner_text']}", str(value))

    def type(self, _id: str, text: str) -> None:
        js = """
        const inputElements = document.querySelectorAll('input');

        for (const input of inputElements) {
            input.value = '';
        }
        """
        self.page.evaluate(js)
        self.click(_id)
        self.page.keyboard.type(text)

    def enter(self) -> None:
        self.page.keyboard.press("Enter")

    def scroll(self, direction: str) -> None:
        if direction == "up":
            self.page.evaluate(
                "(document.scrollingElement || document.body).scrollTop = (document.scrollingElement || document.body).scrollTop - (window.innerHeight / 2);"
            )
        elif direction == "down":
            self.page.evaluate(
                "(document.scrollingElement || document.body).scrollTop = (document.scrollingElement || document.body).scrollTop + (window.innerHeight / 2);"
            )

    def parse(self) -> List[str]:
        st = time.monotonic()

        device_pixel_ratio = self.page.evaluate("window.devicePixelRatio")

        window_width = self.page.evaluate("window.screen.width")
        window_height = self.page.evaluate("window.screen.height")

        window_left_bound = self.page.evaluate("window.pageXOffset")
        window_upper_bound = self.page.evaluate("window.pageYOffset")
        window_right_bound = window_left_bound + window_width
        window_lower_bound = window_upper_bound + window_height

        # https://chromedevtools.github.io/devtools-protocol/tot/DOMSnapshot/
        tree = self.client.send(
            method="DOMSnapshot.captureSnapshot",
            params={
                "computedStyles": [],
                "includeDOMRects": True,
                "includePaintOrder": True,
            },
        )

        document = tree["documents"][0]
        strings = tree["strings"]

        nodes = document["nodes"]
        parents = nodes["parentIndex"]
        names = nodes["nodeName"]
        values = nodes["nodeValue"]
        attributes = nodes["attributes"]
        is_clickable = {str(i) for i in nodes["isClickable"]["index"]}

        layout = document["layout"]

        hash_tree = {}

        for element_idx, parent_id in enumerate(parents):
            hash_name = str(element_idx)
            hash_tree[hash_name] = {
                "children": [],
                "name": strings[names[element_idx]],
                "value": strings[values[element_idx]],
                "is_clickable": True if hash_name in is_clickable else False,
                "attributes": [strings[i] for i in attributes[element_idx]],
            }

            if (hash_parent_id := str(parent_id)) in hash_tree:
                hash_tree[hash_parent_id]["children"].append(hash_name)
            hash_tree[hash_name]["parent"] = hash_parent_id

            try:
                layout_idx = layout["nodeIndex"].index(element_idx)
            except ValueError:
                continue

            # left distance, top distance, width, height
            x, y, width, height = layout["bounds"][layout_idx]
            x /= device_pixel_ratio
            y /= device_pixel_ratio
            width /= device_pixel_ratio
            height /= device_pixel_ratio

            # check if a given node is at least partially in the viewport
            if self.limit_to_viewport:
                is_partially_in_viewport = (
                    x < window_right_bound
                    and x + width >= window_left_bound
                    and y < window_lower_bound
                    and y + height >= window_upper_bound
                )

                if not is_partially_in_viewport:
                    continue

            # the calculation of x_mid and y_mid will be incorrect if the Windows scale
            # is set to the value other than 100%
            hash_tree[hash_name].update(
                {
                    "x_mid": int(x + (width / 2)),
                    "y_mid": int(y + (height / 2)),
                }
            )

        def extract_text_values(node: dict) -> List[str]:
            ignore_elements = set(["SCRIPT", "STYLE", "PATH", "SVG"])

            # breadth-first traversal of `node` subtree
            text_values = list()
            q = Queue()
            q.put(node)

            while not q.empty():
                p = q.get()
                if not p["name"] in ignore_elements:
                    if p["name"] == "#text":
                        text_values.append(p["value"])
                    if p["children"]:
                        for child in p["children"]:
                            q.put(hash_tree[child])
            return text_values

        def text_values_to_text(text_values: List[str], sep: str = " ") -> str:
            if text_values:
                return f"{sep}".join([v.strip() for v in text_values if v.strip()])
            return ""

        def extract_attributes(attrs: List[str], targets: List[str]) -> Dict[str, str]:
            # attrs: list of all attributes for a Node
            # targets: which pairs of attribute:value to extract
            return {
                attrs[i]: attrs[i + 1]
                for i in range(0, len(attrs), 2)
                if attrs[i] in targets
            }

        def is_ancestor_of(node: dict, names: List[str] = []):
            q = Queue()

            for child in node["children"]:
                q.put(hash_tree[child])

            while not q.empty():
                p = q.get()
                if p["name"] in names:
                    return True
                if p["children"]:
                    for child in p["children"]:
                        q.put(hash_tree[child])

            return False

        def collapse_select_node(hash_name: str, select_buffer: Dict = {}):
            node = hash_tree[hash_name]

            if node["name"] == "OPTION":
                name_lower = node["name"].lower()
                node["node_type"] = name_lower
                # node["inner_text"] = text_values_to_text(extract_text_values(node))
                attrs = extract_attributes(node["attributes"], ["selected", "value"])
                node["inner_text"] = attrs.get("value", "")
                is_selected = attrs.get("selected")

                node["meta"] = (
                    f"<{name_lower}"
                    # + f"id={hash_name}"
                    + (" selected" if is_selected else "")
                    + ">"
                    + node["inner_text"]
                    + f"</{name_lower}>"
                )
                select_buffer[hash_name] = node

            elif node["name"] == "SELECT":
                name_lower = node["name"].lower()
                node["node_type"] = name_lower
                node_en = copy.copy(node)
                node["inner_text"] = extract_attributes(
                    node["attributes"], ["name"]
                ).get("name", "")

                node["meta"] = (
                    f"<{name_lower} id={hash_name}"
                    + (f" name={node['inner_text']}" if node["inner_text"] else "")
                    + ">"
                )
                select_buffer[hash_name] = node

                for child_hash in hash_tree[hash_name]["children"]:
                    collapse_select_node(child_hash, select_buffer)

                node_en["meta"] = f"</{name_lower}>"
                select_buffer["_" + hash_name + "_"] = node_en
            else:
                if hash_tree[hash_name]["children"]:
                    for child_hash in hash_tree[hash_name]["children"]:
                        collapse_select_node(child_hash, select_buffer)

        def collapse_table_node(hash_name: str, table_buffer: Dict = {}):
            node = hash_tree[hash_name]

            if node["name"] in ("TD", "TH") and node["is_clickable"] == False:
                name_lower = node["name"].lower()
                node["node_type"] = name_lower
                node["inner_text"] = text_values_to_text(extract_text_values(node))
                node["meta"] = (
                    f"<{name_lower} id={hash_name}>"
                    + node["inner_text"]
                    + f"</{name_lower}>"
                )
                table_buffer[hash_name] = node
            elif node["name"] == "TD" and node["is_clickable"] == True:
                name_lower = "button"
                node["node_type"] = name_lower
                aria_label = extract_attributes(node["attributes"], ["aria-label"]).get(
                    "aria-label"
                )
                node["inner_text"] = (
                    aria_label
                    if aria_label
                    else text_values_to_text(extract_text_values(node))
                )
                node["meta"] = (
                    f"<{name_lower} id={hash_name}>"
                    + node["inner_text"]
                    + f"</{name_lower}>"
                )
                table_buffer[hash_name] = node

            elif node["name"] in ("TR", "TABLE"):
                name_lower = node["name"].lower()
                node["node_type"] = name_lower
                node_en = copy.copy(node)
                node["meta"] = f"<{name_lower}>"
                table_buffer[hash_name] = node

                for child_hash in hash_tree[hash_name]["children"]:
                    collapse_table_node(child_hash, table_buffer)

                node_en["meta"] = f"</{name_lower}>"
                table_buffer["_" + hash_name + "_"] = node_en
            else:
                if hash_tree[hash_name]["children"]:
                    for child_hash in hash_tree[hash_name]["children"]:
                        collapse_table_node(child_hash, table_buffer)

        def collapse_node(hash_name: str) -> dict:
            node = hash_tree[hash_name]

            if node["name"] in ("BUTTON", "A"):
                name_lower = (
                    node["name"].lower() if node["name"] == "BUTTON" else "link"
                )
                node["node_type"] = name_lower
                node["inner_text"] = text_values_to_text(
                    extract_text_values(node), sep=" | "
                )
                if not node["inner_text"]:
                    attrs = extract_attributes(
                        node["attributes"], ["aria-label", "aria-labelledby"]
                    )
                    if it := attrs.get("aria-labelledby"):
                        node["inner_text"] = it
                    else:
                        node["inner_text"] = attrs.get("aria-label", "")

                node["meta"] = (
                    f"<{name_lower} id={hash_name}>"
                    + ("(" if name_lower == "button" else "")
                    + node["inner_text"]
                    + (")" if name_lower == "button" else "")
                    + f"</{name_lower}>"
                )
                node["children"] = []

            elif node["name"] == "INPUT":
                attrs = extract_attributes(
                    node["attributes"], ["type", "aria-label", "placeholder"]
                )
                if attrs.get("type", "") in ("text", "search", ""):
                    node["node_type"] = "input"
                    node["inner_text"] = attrs.get("aria-label", "")
                    if not node["inner_text"]:
                        node["inner_text"] = attrs.get("placeholder", "")
                    # check if there initial value typed in the input bar
                    # input_value = attrs.get("value")  # else None
                    node["meta"] = (
                        f"<input id={hash_name} alt="
                        + node["inner_text"]
                        # + (f" value={input_value}" if input_value else "")
                        + "></input>"
                    )
                    node["children"] = []
                elif attrs.get("type") == "submit":
                    node["node_type"] = "button"
                    node["inner_text"] = attrs.get("aria-label", "")
                    node["meta"] = (
                        f"<button id={hash_name}>(" + node["inner_text"] + ")</button>"
                    )
                    node["children"] = []
            elif node["name"] == "IMG":
                attrs = extract_attributes(node["attributes"], ["alt"])
                node["node_type"] = "img"
                node["inner_text"] = attrs.get("alt", "")
                node["meta"] = f"<img id={hash_name} alt=" + node["inner_text"] + "/>"
                node["children"] = []
            elif node["name"] == "#text":
                node["node_type"] = "text"
                node["inner_text"] = node["value"].strip().replace("\n", " ")
                node["meta"] = f"<text id={hash_name}>" + node["inner_text"] + "</text>"
                node["children"] = []
            elif node["name"] == "DIV" and node["is_clickable"] == True:
                node["node_type"] = "button"
                node["inner_text"] = text_values_to_text(
                    extract_text_values(node), " | "
                )
                node["meta"] = (
                    f"<button id={hash_name}>(" + node["inner_text"] + ")</button>"
                )
                node["children"] = []

            return node

        # with open("tree.json", "w") as outfile:
        #     json.dump(hash_tree, outfile)

        buffer = {}

        def analyse_node(hash_name: str):
            collapsable = {"BUTTON", "A", "INPUT", "IMG", "#text"}
            # "TABLE", "SELECT"
            if (
                hash_tree[hash_name]["name"] in ("DIV")
                and hash_tree[hash_name]["is_clickable"] == False
            ):
                name_lower = hash_tree[hash_name]["name"].lower()
                buffer[hash_name] = {"node_type": "sep", "meta": f"<{name_lower}>"}

            elif hash_tree[hash_name]["name"] in collapsable or (
                hash_tree[hash_name]["name"] == "DIV"
                and hash_tree[hash_name]["is_clickable"] == True
                and not is_ancestor_of(
                    hash_tree[hash_name],
                    ["BUTTON", "A", "INPUT", "IMG", "TABLE", "SELECT"],
                )
            ):
                hash_tree[hash_name] = collapse_node(hash_name)
                _fileds = [
                    "node_type",
                    "meta",
                    "inner_text",
                    "name",
                    "x_mid",
                    "y_mid",
                ]
                if all([hash_tree[hash_name].get(f) for f in _fileds]):
                    buffer_node = {f: hash_tree[hash_name][f] for f in _fileds}
                    buffer_node["is_clickable"] = hash_tree[hash_name].get(
                        "is_clickable"
                    )
                    if buffer_node["inner_text"].replace("·", " ").strip() != "":
                        buffer[hash_name] = buffer_node

            elif hash_tree[hash_name]["name"] == "TABLE":
                table_buffer = {}
                collapse_table_node(hash_name, table_buffer=table_buffer)

                for k, v in table_buffer.items():
                    buffer[k] = v
                hash_tree[hash_name]["children"] = []

            elif hash_tree[hash_name]["name"] == "SELECT":
                select_buffer = {}
                collapse_select_node(hash_name, select_buffer=select_buffer)

                for k, v in select_buffer.items():
                    buffer[k] = v
                hash_tree[hash_name]["children"] = []

            if hash_tree[hash_name]["children"]:
                for child_hash in hash_tree[hash_name]["children"]:
                    analyse_node(child_hash)

        analyse_node("0")
        self.page_buffer = buffer

        et = time.monotonic()
        print(f" > ran parser in {(et-st)*1000.0:.2f} ms")

        return buffer
