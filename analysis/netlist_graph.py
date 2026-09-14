import json
from collections import defaultdict

import networkx as nx


class NetlistGraph:

    def __init__(self, json_file, top="riscv_core"):

        self.json_file = json_file
        self.top = top

        self.graph = nx.DiGraph()

        self.cells = {}

        # bit -> (cell, port, bit_index)
        self.bit_driver = {}

        # bit -> [(cell, port, bit_index), ...]
        self.bit_users = defaultdict(list)

        # bit -> top-level port label
        self.primary_inputs = {}

        self.primary_outputs = {}

        self.raw_data = None
        self.module_data = None

        self.load()


    def load(self):

        with open(self.json_file, "r") as f:
            data = json.load(f)

        self.raw_data = data

        if self.top not in data["modules"]:
            raise RuntimeError(
                f"Top module '{self.top}' not found in {self.json_file}"
            )

        module = data["modules"][self.top]

        self.module_data = module

        raw_cells = module.get("cells", {})

        # $scopeinfo 只是 flatten 保存 hierarchy metadata 用的，
        # 不是实际逻辑节点。
        self.cells = {
            name: cell
            for name, cell in raw_cells.items()
            if cell.get("type") != "$scopeinfo"
        }

        self._load_top_ports(module)

        self._add_cells()

        self._build_bit_maps()

        self._build_edges()


    def _load_top_ports(self, module):

        for port_name, port_data in module.get("ports", {}).items():

            direction = port_data.get("direction")

            for index, bit in enumerate(
                port_data.get("bits", [])
            ):

                # 字符串表示 0/1/x/z 常量
                if isinstance(bit, str):
                    continue

                bit_key = str(bit)

                label = f"{port_name}[{index}]"

                if direction in ("input", "inout"):
                    self.primary_inputs[bit_key] = label

                if direction in ("output", "inout"):
                    self.primary_outputs[bit_key] = label


    def _add_cells(self):

        for name, cell in self.cells.items():

            self.graph.add_node(
                name,

                type=cell.get("type"),

                parameters=cell.get(
                    "parameters",
                    {}
                ),

                attributes=cell.get(
                    "attributes",
                    {}
                ),

                port_directions=cell.get(
                    "port_directions",
                    {}
                ),

                connections=cell.get(
                    "connections",
                    {}
                ),
            )


    def _build_bit_maps(self):

        for cell_name, cell in self.cells.items():

            directions = cell.get(
                "port_directions",
                {}
            )

            connections = cell.get(
                "connections",
                {}
            )

            for port_name, bits in connections.items():

                direction = directions.get(port_name)

                if direction is None:
                    continue

                for bit_index, bit in enumerate(bits):

                    if isinstance(bit, str):
                        continue

                    bit_key = str(bit)

                    if direction in ("output", "inout"):

                        if bit_key not in self.bit_driver:

                            self.bit_driver[bit_key] = (
                                cell_name,
                                port_name,
                                bit_index,
                            )

                    if direction in ("input", "inout"):

                        self.bit_users[bit_key].append(
                            (
                                cell_name,
                                port_name,
                                bit_index,
                            )
                        )


    def _build_edges(self):

        for bit, driver in self.bit_driver.items():

            driver_cell, driver_port, driver_index = driver

            for user in self.bit_users.get(bit, []):

                user_cell, user_port, user_index = user

                if driver_cell == user_cell:
                    continue

                self.graph.add_edge(
                    driver_cell,
                    user_cell,

                    bit=bit,

                    driver_port=driver_port,
                    driver_index=driver_index,

                    user_port=user_port,
                    user_index=user_index,
                )


    def cell_count(self):
        return self.graph.number_of_nodes()


    def edge_count(self):
        return self.graph.number_of_edges()