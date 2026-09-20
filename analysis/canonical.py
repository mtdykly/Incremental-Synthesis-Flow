import json
import re


def _normalize_scope_text(text):

    if not isinstance(text, str):
        return ""

    text = text.strip()

    text = text.replace("\\", "")

    if text.startswith("$flatten"):
        text = text[len("$flatten"):]

    text = text.replace(" ", ".").strip(".")

    return text


def canonical_scope(node_name, node_data):

    attributes = node_data.get(
        "attributes",
        {}
    )

    #
    # 第一优先级：
    # flatten -scopename 产生的属性
    #
    scopename = attributes.get("scopename")

    if isinstance(scopename, str):

        # 防止把 001010... 这种编码值误认为名字
        if any(c.isalpha() for c in scopename):

            normalized = _normalize_scope_text(
                scopename
            )

            if normalized:
                return normalized

    #
    # 第二优先级：
    # 从 flatten 后的 cell name 恢复
    #
    name = node_name.replace("\\", "")

    if name.startswith("$flatten"):

        name = name[len("$flatten"):]

        #
        # 例如
        #
        # singlecycle_datapath.alu.$add$...
        #
        pos = name.find(".$")

        if pos != -1:

            return name[:pos].strip(".")

    #
    # 没有 hierarchy 信息的 $auto$ cell
    #
    return "<global>"


def _normalize_path(path):

    path = path.replace("\\", "/")

    #
    # 删除：
    #
    # .../work/base/
    # .../work/new/
    #
    path = re.sub(
        r"^.*?/work/(?:base|new)/",
        "",
        path
    )

    #
    # 如果仍然是绝对路径，
    # 尽量截取稳定的 RTL 相对路径。
    #
    markers = [
        "core/",
        "rtl/",
        "src/",
    ]

    for marker in markers:

        pos = path.find(marker)

        if pos != -1:
            return path[pos:]

    #
    # fallback：
    # 保留最后四层路径
    #
    pieces = [
        p
        for p in path.split("/")
        if p
    ]

    if len(pieces) > 4:
        pieces = pieces[-4:]

    return "/".join(pieces)


def canonical_sources(node_data):

    from source_map import parse_source_spans
    src = node_data.get('attributes', {}).get('src', '')
    # Preserve the matcher's historical anchor shape. The provenance index
    # separately retains complete source spans and instance paths.
    return sorted({(_normalize_path(s.path), s.start_line) for s in parse_source_spans(src)})


def intrinsic_signature(node_data):

    cell_type = node_data.get(
        "type"
    )

    parameters = node_data.get(
        "parameters",
        {}
    )

    directions = node_data.get(
        "port_directions",
        {}
    )

    connections = node_data.get(
        "connections",
        {}
    )

    #
    # 注意：
    # 这里只记录端口宽度，
    # 不记录 net ID。
    #
    ports = []

    for port in sorted(
        connections.keys()
    ):

        ports.append(
            (
                port,
                directions.get(
                    port,
                    "unknown"
                ),
                len(
                    connections[port]
                )
            )
        )

    parameter_text = json.dumps(
        parameters,
        sort_keys=True,
        default=str
    )

    return (
        cell_type,
        parameter_text,
        tuple(ports),
    )


def canonical_record(
        node_name,
        node_data
):

    sources = canonical_sources(
        node_data
    )

    return {

        "name":
            node_name,

        "scope":
            canonical_scope(
                node_name,
                node_data
            ),

        "type":
            node_data.get(
                "type"
            ),

        "sources":
            sources,

        "intrinsic":
            intrinsic_signature(
                node_data
            ),
    }