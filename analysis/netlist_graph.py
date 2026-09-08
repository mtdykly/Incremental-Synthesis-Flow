import json
import networkx as nx


class NetlistGraph:


    def __init__(
            self,
            json_file,
            top=None
    ):

        self.graph = nx.DiGraph()

        self.load(
            json_file,
            top
        )



    def load(
            self,
            json_file,
            top
    ):


        with open(json_file) as f:

            data=json.load(f)


        if top is None:

            top=list(
                data["modules"].keys()
            )[0]


        module=data["modules"][top]


        cells=module.get(
            "cells",
            {}
        )


        for name,cell in cells.items():


            self.graph.add_node(

                name,

                type=cell["type"],

                connections=
                    cell.get(
                        "connections",
                        {}
                    )

            )


        self.build_edges(
            cells
        )



    def build_edges(
            self,
            cells
    ):


        net_users={}


        for cname,cell in cells.items():


            for port,bits in cell.get(
                "connections",
                {}
            ).items():


                for bit in bits:


                    net_users.setdefault(
                        str(bit),
                        []
                    ).append(
                        cname
                    )



        for net,users in net_users.items():


            if len(users)<2:

                continue


            driver=users[0]


            for u in users[1:]:


                if driver!=u:

                    self.graph.add_edge(
                        driver,
                        u,
                        net=net
                    )